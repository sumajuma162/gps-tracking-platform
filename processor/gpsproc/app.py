"""Processor: consumes the gateway's ``positions`` stream, maintains vehicle status and pushes live updates.

At-least-once: entries are acknowledged after their effects are stored; resends and replays are deduplicated by
record hash. Live updates go to the web app's Channels groups ``tracking.org.<id>`` (spec A12 live map).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import asyncpg
import redis.asyncio as aioredis
from channels_redis.core import RedisChannelLayer

from gpsproc import orgsettings
from gpsproc.alerts import AlertEngine, Signal, signals_from_items
from gpsproc.analysis.engine import Config as AnalysisConfig
from gpsproc.analysis.store import AnalysisStore, sample_from_record
from gpsproc.orgsettings import OrgSettings
from gpsproc.outbox import Outbox
from gpsproc.status import (
    IDLE,
    MOVING,
    NO_GPS,
    OFFLINE,
    STOPPED,
    SeenRecords,
    Thresholds,
    status_from_record,
    status_from_silence,
)

log = logging.getLogger("gpsproc")
STREAM, GROUP = "positions", "processor"


def _database_url_from_env() -> str:
    if url := os.environ.get("DATABASE_URL", ""):
        return url
    user = os.environ.get("DATABASE_USER", "")
    password = os.environ.get("DATABASE_PASSWORD", "")
    host = os.environ.get("DATABASE_HOST", "postgres")
    port = os.environ.get("DATABASE_PORT", "5432")
    name = os.environ.get("DATABASE_NAME", "")
    if not user or not name:
        return ""
    return f"postgresql://{quote(user)}:{quote(password)}@{host}:{port}/{quote(name)}"


def _redis_url_from_env() -> str:
    if url := os.environ.get("REDIS_URL", ""):
        return url
    password = os.environ.get("REDIS_PASSWORD", "")
    host = os.environ.get("REDIS_HOST", "redis")
    port = os.environ.get("REDIS_PORT", "6379")
    db = os.environ.get("REDIS_DB", "0")
    auth = f":{quote(password)}@" if password else ""
    return f"redis://{auth}{host}:{port}/{db}"


def group_name(organization_id: int) -> str:
    return f"tracking.org.{organization_id}"


@dataclass(frozen=True)
class ProcessorConfig:
    database_url: str
    redis_url: str
    consumer: str = field(default_factory=socket.gethostname)
    thresholds: Thresholds = Thresholds()
    sweep_seconds: float = 15.0
    recompute_debounce_s: float = 30.0
    #: When a device's next streamed record is this much newer than the last one analysed, the processor reads
    #: the positions in between from the database (records stored while Redis or the stream was lost; spec A3).
    catchup_gap_s: float = 90.0
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    batch: int = 500
    block_ms: int = 1000

    @classmethod
    def from_env(cls) -> ProcessorConfig:
        return cls(
            database_url=_database_url_from_env(), redis_url=_redis_url_from_env(),
            consumer=os.environ.get("PROCESSOR_CONSUMER", socket.gethostname()),
            thresholds=Thresholds(moving_kmh=float(os.environ.get("MOVING_SPEED_KMH", "5")),
                                  offline_after=timedelta(minutes=float(os.environ.get("OFFLINE_MINUTES", "5"))),
                                  no_gps_after=timedelta(minutes=float(os.environ.get("NO_GPS_MINUTES", "10")))),
            sweep_seconds=float(os.environ.get("SWEEP_SECONDS", "15")),
            recompute_debounce_s=float(os.environ.get("RECOMPUTE_DEBOUNCE_SECONDS", "30")),
            catchup_gap_s=float(os.environ.get("CATCHUP_GAP_SECONDS", "90")),
            analysis=AnalysisConfig(
                stop_kmh=float(os.environ.get("STOP_SPEED_KMH", "3")),
                overspeed_warning_kmh=float(os.environ.get("OVERSPEED_WARNING_KMH", "80")),
                overspeed_critical_kmh=float(os.environ.get("OVERSPEED_CRITICAL_KMH", "100"))),
        )


@dataclass
class Stats:
    entries: int = 0
    duplicates: int = 0
    late_skipped: int = 0
    status_changes: int = 0
    updates_sent: int = 0
    last_lag_ms: float = 0.0
    errors: int = 0
    group_recreated: int = 0
    analysis_items: int = 0
    recompute_requests: int = 0
    recomputes_run: int = 0
    alerts_touched: int = 0
    webhook_events: int = 0
    caught_up: int = 0


def _parse_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


SET_STATUS = "UPDATE device_state SET status = $2, updated_at = now() WHERE device_id = $1"

STATE_SQL = """SELECT device_id, organization_id, vehicle_id, status, device_time, last_packet_at, latitude, longitude,
                      speed_kmh, heading, satellites, ignition, gps_valid, external_voltage, battery_voltage, gsm_signal
               FROM device_state WHERE device_id = ANY($1::bigint[])"""


def live_update(state: dict[str, Any], status: str) -> dict[str, Any]:
    """The payload the live map receives (positions only from the newest valid fix, never from backlog)."""
    def iso(v: datetime | None) -> str | None:
        return v.isoformat() if v else None
    return {"device_id": state["device_id"], "vehicle_id": state["vehicle_id"], "status": status,
            "latitude": state["latitude"], "longitude": state["longitude"], "speed_kmh": state["speed_kmh"],
            "heading": state["heading"], "satellites": state["satellites"], "ignition": state["ignition"],
            "gps_valid": state["gps_valid"], "fix_time": iso(state["device_time"]),
            "last_packet_at": iso(state["last_packet_at"]), "external_voltage": state["external_voltage"],
            "battery_voltage": state["battery_voltage"], "gsm_signal": state["gsm_signal"]}


class Processor:
    def __init__(self, config: ProcessorConfig) -> None:
        self.config = config
        self.stats = Stats()
        self.seen = SeenRecords()
        self.pool: asyncpg.Pool | None = None
        self.redis: aioredis.Redis | None = None
        self.layer: RedisChannelLayer | None = None
        self.tasks: list[asyncio.Task[Any]] = []
        self.settings = OrgSettings()
        self.outbox = Outbox()
        self.analysis = AnalysisStore(config.analysis, settings=self.settings, outbox=self.outbox)
        self.alerts = AlertEngine(settings=self.settings, outbox=self.outbox)

    async def start(self) -> None:
        self.pool = await asyncpg.create_pool(self.config.database_url, min_size=1, max_size=5)
        self.redis = aioredis.Redis.from_url(self.config.redis_url)
        self.layer = RedisChannelLayer(hosts=[self.config.redis_url])
        self.alerts.layer = self.layer
        await self.ensure_group()
        self.tasks = [asyncio.create_task(self._consume()), asyncio.create_task(self._sweep_loop()),
                      asyncio.create_task(self._watch_geofences())]

    async def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.redis is not None:
            await self.redis.aclose()
        if self.pool is not None:
            await self.pool.close()

    # --- stream ----------------------------------------------------------------------------------------

    async def ensure_group(self) -> None:
        """Create the consumer group (and stream) if missing. Called at start and whenever Redis lost them,
        e.g. after a Redis restart without persistence; the database stays the source of truth."""
        assert self.redis is not None
        try:
            await self.redis.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
            log.info("consumer group ready", extra={"stream": STREAM, "group": GROUP})
        except aioredis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def _consume(self) -> None:
        assert self.redis is not None
        start_id = "0"          # first our own pending entries (unacked before a crash), then new ones
        while True:
            try:
                resp = await self.redis.xreadgroup(GROUP, self.config.consumer, {STREAM: start_id},
                                                   count=self.config.batch, block=self.config.block_ms)
                entries = resp[0][1] if resp else []
                if start_id == "0" and not entries:
                    start_id = ">"
                    continue
                if entries:
                    await self.handle(entries)
                    await self.redis.xack(STREAM, GROUP, *[eid for eid, _ in entries])
            except asyncio.CancelledError:
                raise
            except aioredis.ResponseError as exc:
                if "NOGROUP" not in str(exc) and "UNBLOCKED" not in str(exc):
                    self.stats.errors += 1
                    log.error("stream read failed", extra={"error": f"{type(exc).__name__}: {exc}"})
                    await asyncio.sleep(1)
                    continue
                log.warning("consumer group missing (Redis lost it); recreating")
                self.stats.group_recreated += 1
                await self.ensure_group()
                start_id = "0"
            except Exception as exc:  # keep consuming; unacked entries are retried after a restart
                self.stats.errors += 1
                log.error("stream batch failed", extra={"error": f"{type(exc).__name__}: {exc}"})
                await asyncio.sleep(1)

    async def handle(self, entries: list[tuple[bytes, dict[bytes, bytes]]]) -> None:
        now = datetime.now(UTC)
        latest: dict[int, dict[str, Any]] = {}
        ordered: dict[int, list[dict[str, Any]]] = {}     # every new record, for the analysis engine
        late: dict[int, list[datetime]] = {}
        for _, fields in entries:
            self.stats.entries += 1
            device_id = int(fields[b"device_id"])
            record = json.loads(fields[b"record"])
            if not self.seen.first_time(device_id, record["record_hash"]):
                self.stats.duplicates += 1
                continue
            if record.get("is_late"):
                self.stats.late_skipped += 1   # backlog: stored by the gateway, never moves the live map
                late.setdefault(device_id, []).append(_parse_time(record["device_time"]))  # type: ignore[arg-type]
                continue
            ordered.setdefault(device_id, []).append(record)
            current = latest.get(device_id)
            if current is None or record["device_time"] >= current["device_time"]:
                latest[device_id] = record
            server_time = _parse_time(record.get("server_time"))
            if server_time:
                self.stats.last_lag_ms = max(0.0, (now - server_time).total_seconds() * 1000)
        if not latest and not late:
            return
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            states = {r["device_id"]: dict(r) for r in await conn.fetch(STATE_SQL, list(latest))}
            changed: list[tuple[int, str]] = []
            by_org: dict[int, list[dict[str, Any]]] = {}
            for device_id, record in latest.items():
                state = states.get(device_id)
                if state is None:
                    continue
                status = status_from_record(
                    speed_kmh=record.get("speed_kmh"), ignition=record.get("ignition"), motion=record.get("motion"),
                    gps_valid=bool(record.get("gps_valid")) and not record.get("is_outlier"),
                    last_valid_fix=state["device_time"], now=now,
                    t=await self.thresholds(conn, state["organization_id"]))
                if status != state["status"]:
                    changed.append((device_id, status))
                    if state["status"] == OFFLINE:
                        await self.alerts.resolve(conn, device_id, "device_offline",
                                                  "Resolved automatically: the tracker is reporting again.")
                    if state["status"] == NO_GPS and status != NO_GPS:
                        await self.alerts.resolve(conn, device_id, "gps_lost",
                                                  "Resolved automatically: GPS fix restored.")
                by_org.setdefault(state["organization_id"], []).append(live_update(state, status))
            async with conn.transaction():
                if changed:
                    await conn.executemany(SET_STATUS, changed)
                await self._analyse(conn, ordered, late)
        self.stats.status_changes += len(changed)
        await self._publish(by_org)

    async def _analyse(self, conn: Any, ordered: dict[int, list[dict[str, Any]]],
                       late: dict[int, list[datetime]]) -> None:
        """Run each device's analysis engine over its new records (device-time order) and store the results and
        the engine state with the checkpoint, in the caller's transaction (spec A10)."""
        devices = sorted(set(ordered) | set(late))
        contexts = await self.analysis.contexts(conn, devices)
        for device_id in devices:
            ctx = contexts.get(device_id)
            if ctx is None:
                continue
            engine = await self.analysis.engine(conn, ctx)
            last_t = _parse_time(engine.state.get("last_t"))
            items: list[dict[str, Any]] = []
            signals: list[Signal] = []
            records = sorted(ordered.get(device_id, []), key=lambda r: r["device_time"])
            first_t = sample_from_record(records[0]).t if records else None
            if first_t is not None and last_t is not None and \
                    (first_t - last_t).total_seconds() > self.config.catchup_gap_s:
                # Positions stored while the stream was down never reached us: resume from the database first.
                missed = await self.analysis.missing(conn, device_id, last_t, first_t)
                if missed:
                    self.stats.caught_up += len(missed)
                    gap_items: list[dict[str, Any]] = []
                    for row in missed:
                        gap_items += engine.process(sample_from_record(row))
                        last_t = row["device_time"]
                    if gap_items:
                        self.stats.analysis_items += await self.analysis.write(conn, ctx, gap_items)
                        signals += signals_from_items(gap_items, delayed=True)   # not seen live: no live alerts
            for record in records:
                sample = sample_from_record(record)
                if last_t is not None and sample.t <= last_t:
                    late.setdefault(device_id, []).append(sample.t)    # older than what the engine has seen
                    continue
                before = engine.state.get("last_ignition")
                if before is not None and sample.ignition is not None and sample.ignition != before:
                    signals.append(Signal("ignition_on" if sample.ignition else "ignition_off", sample.t,
                                          sample.lat if sample.usable else None, sample.lon if sample.usable else None))
                battery = record.get("battery_voltage")
                if battery is not None and battery < 3.8:
                    signals.append(Signal("low_battery", sample.t, sample.lat, sample.lon, value=battery))
                items += engine.process(sample)
                last_t = sample.t
            signals += signals_from_items(items)
            if items:
                self.stats.analysis_items += await self.analysis.write(conn, ctx, items)
            if records:
                await self.analysis.save_state(conn, device_id, engine, records[-1]["record_hash"])
            if signals:
                self.stats.alerts_touched += await self.alerts.evaluate(
                    conn, organization_id=ctx.organization_id, vehicle_id=ctx.vehicle_id, device_id=device_id,
                    driver_id=ctx.driver_id, signals=signals, now=datetime.now(UTC))
            if late.get(device_id):
                await self.analysis.request_recompute(conn, device_id, late[device_id])
                self.stats.recompute_requests += 1

    async def _delayed_alerts(self, conn: Any, ctx: Any, items: list[dict[str, Any]]) -> None:
        """Alerts from recomputed backlog data are stored as delayed and never notified as live (spec A11)."""
        self.stats.alerts_touched += await self.alerts.evaluate(
            conn, organization_id=ctx.organization_id, vehicle_id=ctx.vehicle_id, device_id=ctx.device_id,
            driver_id=ctx.driver_id, signals=signals_from_items(items, delayed=True), now=datetime.now(UTC))

    async def thresholds(self, conn: Any, organization_id: int) -> Thresholds:
        """Status thresholds for one organization (its settings over the configured defaults)."""
        return orgsettings.thresholds(self.config.thresholds, await self.settings.values(conn, organization_id))

    def on_config_changed(self, data: Any) -> None:
        """Geofences or alert rules changed in the web app: both caches must drop, or a new rule could miss up
        to a cache lifetime of events."""
        try:
            org: int | None = int(json.loads(data)["organization_id"])
        except (ValueError, KeyError, TypeError):
            org = None
        self.analysis.invalidate_fences(org)
        self.alerts.invalidate(org)
        self.settings.invalidate(org)
        self.outbox.invalidate(org)

    async def _watch_geofences(self) -> None:
        """Drop cached geofences as soon as the web app changes them (they also expire after 60 s)."""
        assert self.redis is not None
        while True:
            try:
                pubsub = self.redis.pubsub()
                await pubsub.subscribe("geofences")
                async for message in pubsub.listen():
                    if message.get("type") == "message":
                        self.on_config_changed(message["data"])
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(2)

    async def _publish(self, by_org: dict[int, list[dict[str, Any]]]) -> None:
        assert self.layer is not None
        for org_id, updates in by_org.items():
            await self.layer.group_send(group_name(org_id), {"type": "tracking.update", "updates": updates})
            self.stats.updates_sent += len(updates)

    # --- offline / no-GPS sweep --------------------------------------------------------------------------

    async def sweep(self, now: datetime | None = None) -> int:
        """Move silent vehicles to OFFLINE / NO_GPS (spec A10). Returns how many changed."""
        assert self.pool is not None
        now = now or datetime.now(UTC)
        rows = await self.pool.fetch(
            "SELECT device_id, organization_id, vehicle_id, status, device_time, last_packet_at, latitude, longitude, "
            "speed_kmh, heading, satellites, ignition, gps_valid, external_voltage, battery_voltage, gsm_signal "
            "FROM device_state WHERE status IN ($1, $2, $3, $4, 'UNKNOWN')", MOVING, IDLE, STOPPED, NO_GPS)
        changed: list[tuple[int, str]] = []
        by_org: dict[int, list[dict[str, Any]]] = {}
        limits: dict[int, Thresholds] = {}
        if rows:
            async with self.pool.acquire() as conn:
                for org_id in {r["organization_id"] for r in rows}:
                    limits[org_id] = await self.thresholds(conn, org_id)
        for row in rows:
            state = dict(row)
            status = status_from_silence(state["status"], last_packet_at=state["last_packet_at"],
                                         last_valid_fix=state["device_time"], now=now,
                                         t=limits.get(state["organization_id"], self.config.thresholds))
            if status != state["status"]:
                changed.append((state["device_id"], status))
                by_org.setdefault(state["organization_id"], []).append(live_update(state, status))
        if changed:
            await self.pool.executemany(SET_STATUS, changed)
            self.stats.status_changes += len(changed)
            await self._publish(by_org)
            rows_by_device = {r["device_id"]: r for r in rows}
            async with self.pool.acquire() as conn, conn.transaction():
                for device_id, status in changed:
                    kind = {OFFLINE: "device_offline", NO_GPS: "gps_lost"}.get(status)
                    if kind is None:
                        continue
                    row = rows_by_device[device_id]
                    if status == OFFLINE:
                        await self.outbox.emit(conn, row["organization_id"], "device.offline",
                                               f"offline:{device_id}:{row['last_packet_at'].isoformat()}", {
                                                   "device_id": device_id, "vehicle_id": row["vehicle_id"],
                                                   "last_packet_at": row["last_packet_at"],
                                                   "position": [row["latitude"], row["longitude"]]}, now)
                    self.stats.alerts_touched += await self.alerts.evaluate(
                        conn, organization_id=row["organization_id"], vehicle_id=row["vehicle_id"],
                        device_id=device_id, driver_id=None, now=now,
                        signals=[Signal(kind, now, row["latitude"], row["longitude"])])
        return len(changed)

    async def _sweep_loop(self) -> None:
        while True:
            try:
                await self.sweep()
                assert self.pool is not None
                self.stats.recomputes_run += await self.analysis.run_due_recomputes(
                    self.pool, self.config.recompute_debounce_s, on_items=self._delayed_alerts)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.stats.errors += 1
                log.error("status sweep failed", extra={"error": f"{type(exc).__name__}: {exc}"})
            await asyncio.sleep(self.config.sweep_seconds)

    def snapshot(self) -> dict[str, Any]:
        self.stats.webhook_events = self.outbox.emitted
        return {"consumer": self.config.consumer, "stats": self.stats.__dict__, "time": time.time(),
                "statuses": [MOVING, IDLE, STOPPED, NO_GPS, OFFLINE]}
