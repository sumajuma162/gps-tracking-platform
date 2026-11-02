"""Database side of the analysis engine: device context, geofence cache, idempotent writes and the late-data
recompute (spec A10). Runs inside the processor with the database owner role."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

import asyncpg

from gpsproc.analysis.engine import Config, Engine, Sample
from gpsproc.analysis.geo import Fence

log = logging.getLogger("gpsproc.analysis")

CONTEXT_SQL = """
SELECT d.id AS device_id, d.organization_id, dm.trip_detection_mode AS model_mode,
       coalesce((dm.capabilities ->> 'odometer')::boolean, false) AS has_odometer,
       a.vehicle_id, nullif(v.trip_detection_mode, '') AS vehicle_mode,
       (SELECT da.driver_id FROM driver_assignments da WHERE da.vehicle_id = a.vehicle_id AND da.unassigned_at IS NULL
         LIMIT 1) AS driver_id,
       pc.state
FROM devices d
JOIN device_models dm ON dm.id = d.device_model_id
LEFT JOIN device_assignments a ON a.device_id = d.id AND a.unassigned_at IS NULL
LEFT JOIN vehicles v ON v.id = a.vehicle_id
LEFT JOIN processing_checkpoints pc ON pc.device_id = d.id
WHERE d.id = ANY($1::bigint[])
"""

FENCES_SQL = """SELECT id, kind, center_lat, center_lon, radius_m, polygon, dwell_minutes FROM geofences
                WHERE organization_id = $1 AND is_active AND deleted_at IS NULL"""

# (table, start column, end column) of everything a replay replaces.
RESULT_TABLES = (("trips", "start_time", "end_time"), ("stops", "start_time", "end_time"),
                 ("idle_events", "start_time", "end_time"), ("speed_events", "start_time", "end_time"),
                 ("driving_behavior_events", "occurred_at", "occurred_at"),
                 ("geofence_events", "occurred_at", "occurred_at"))


@dataclass
class DeviceContext:
    device_id: int
    organization_id: int
    vehicle_id: int | None
    driver_id: int | None
    config: Config
    state: dict[str, Any]


def _json(v: Any) -> Any:
    return json.loads(v) if isinstance(v, str) else v


def sample_from_record(r: dict[str, Any]) -> Sample:
    t = r["device_time"]
    if isinstance(t, str):
        t = datetime.fromisoformat(t.replace("Z", "+00:00"))
    return Sample(t=t, lat=r["latitude"], lon=r["longitude"], speed=r.get("speed_kmh") or 0.0,
                  heading=r.get("heading"), ignition=r.get("ignition"), motion=r.get("motion"),
                  gps_valid=bool(r.get("gps_valid")), is_outlier=bool(r.get("is_outlier")),
                  odometer_km=r.get("odometer_km"), events=tuple(_json(r.get("events")) or ()))


def fence_from_row(r: Any) -> Fence | None:
    dwell = (r["dwell_minutes"] or 0) * 60 or None
    if r["kind"] == "circle" and r["center_lat"] is not None and r["radius_m"]:
        return Fence(id=r["id"], kind="circle", center=(r["center_lat"], r["center_lon"]), radius_m=r["radius_m"],
                     dwell_s=dwell)
    ring = tuple((float(p[0]), float(p[1])) for p in (_json(r["polygon"]) or []))
    if r["kind"] == "polygon" and len(ring) >= 3:
        return Fence(id=r["id"], kind="polygon", polygon=ring, dwell_s=dwell)
    return None


class AnalysisStore:
    def __init__(self, base: Config | None = None, fence_ttl: float = 60.0, settings: Any = None,
                 outbox: Any = None) -> None:
        self.base = base or Config()
        self.fence_ttl = fence_ttl
        self.settings = settings        # gpsproc.orgsettings.OrgSettings: per-organization thresholds
        self.outbox = outbox            # gpsproc.outbox.Outbox: webhook events
        self._fences: dict[int, tuple[float, list[Fence]]] = {}

    def invalidate_fences(self, organization_id: int | None = None) -> None:
        if organization_id is None:
            self._fences.clear()
        else:
            self._fences.pop(organization_id, None)

    async def fences(self, conn: asyncpg.Connection, organization_id: int) -> list[Fence]:
        cached = self._fences.get(organization_id)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        fences = [f for f in (fence_from_row(r) for r in await conn.fetch(FENCES_SQL, organization_id)) if f]
        self._fences[organization_id] = (time.monotonic() + self.fence_ttl, fences)
        return fences

    async def contexts(self, conn: asyncpg.Connection, device_ids: list[int]) -> dict[int, DeviceContext]:
        out = {}
        for r in await conn.fetch(CONTEXT_SQL, device_ids):
            mode = r["vehicle_mode"] or r["model_mode"] or "ignition"
            base = self.base
            if self.settings is not None:
                from gpsproc import orgsettings
                base = orgsettings.analysis(base, await self.settings.values(conn, r["organization_id"]))
            cfg = replace(base, mode=mode, use_odometer=bool(r["has_odometer"]))
            out[r["device_id"]] = DeviceContext(r["device_id"], r["organization_id"], r["vehicle_id"], r["driver_id"],
                                                cfg, _json(r["state"]) or {})
        return out

    async def engine(self, conn: asyncpg.Connection, ctx: DeviceContext, fresh: bool = False) -> Engine:
        return Engine(ctx.config, await self.fences(conn, ctx.organization_id), {} if fresh else dict(ctx.state))

    # --- writes ---------------------------------------------------------------------------------------

    async def write(self, conn: asyncpg.Connection, ctx: DeviceContext, items: list[dict[str, Any]],
                    delayed_from: datetime | None = None) -> int:
        """Insert finished items idempotently; items ending at or after ``delayed_from`` are marked delayed."""
        base = (ctx.organization_id, ctx.device_id, ctx.vehicle_id, ctx.driver_id)
        n = 0
        for it in items:
            end = it.get("end") or it.get("at")
            late = delayed_from is not None and end is not None and end >= delayed_from
            added = await self._insert(conn, it, base, ctx.config.mode, late)
            n += added
            if added and self.outbox is not None:
                await self._emit(conn, ctx, it, late)
        return n

    async def _emit(self, conn: asyncpg.Connection, ctx: DeviceContext, it: dict[str, Any], late: bool) -> None:
        """Webhook events for completed trips and geofence events (spec A13). Keys are stable, so a recompute
        that re-inserts the same result doesn't send it twice."""
        common = {"device_id": ctx.device_id, "vehicle_id": ctx.vehicle_id, "driver_id": ctx.driver_id,
                  "delayed": late}
        if it["kind"] == "trip":
            await self.outbox.emit(conn, ctx.organization_id, "trip.completed",
                                   f"trip:{ctx.device_id}:{it['start'].isoformat()}", {
                                       **common, "start": it["start"], "end": it["end"],
                                       "distance_km": round(it["distance_km"], 3), "duration_s": it["duration_s"],
                                       "max_speed_kmh": it["max_speed_kmh"],
                                       "start_position": [it["start_lat"], it["start_lon"]],
                                       "end_position": [it["end_lat"], it["end_lon"]]}, it["end"])
        elif it["kind"] == "geofence":
            await self.outbox.emit(conn, ctx.organization_id, "geofence.event",
                                   f"geofence:{ctx.device_id}:{it['geofence_id']}:{it['event']}:{it['at'].isoformat()}",
                                   {**common, "geofence_id": it["geofence_id"], "event": it["event"], "at": it["at"],
                                    "position": [it["lat"], it["lon"]], "dwell_s": it.get("dwell_s")}, it["at"])

    async def _insert(self, conn: asyncpg.Connection, it: dict[str, Any], base: tuple[Any, ...], mode: str,
                      late: bool) -> int:
        k = it["kind"]
        common = "organization_id, device_id, vehicle_id, driver_id"
        if k == "trip":
            sql = (f"INSERT INTO trips ({common}, start_time, end_time, start_lat, start_lon, end_lat, end_lon, "
                   "start_address, end_address, distance_km, distance_source, duration_s, driving_s, idle_s, "
                   "stopped_s, max_speed_kmh, avg_moving_speed_kmh, points, detection_mode, delayed, created_at) "
                   "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'','',$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,now()) "
                   "ON CONFLICT (device_id, start_time) DO NOTHING")
            args = [it["start"], it["end"], it["start_lat"], it["start_lon"], it["end_lat"], it["end_lon"],
                    it["distance_km"], it["distance_source"], it["duration_s"], it["driving_s"], it["idle_s"],
                    it["stopped_s"], it["max_speed_kmh"], it["avg_moving_speed_kmh"], it["points"], mode, late]
        elif k == "stop":
            sql = (f"INSERT INTO stops ({common}, start_time, end_time, duration_s, lat, lon, engine_off, address, "
                   "delayed, created_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'',$11,now()) "
                   "ON CONFLICT (device_id, start_time) DO NOTHING")
            args = [it["start"], it["end"], it["duration_s"], it["lat"], it["lon"], it["engine_off"], late]
        elif k == "idle":
            sql = (f"INSERT INTO idle_events ({common}, start_time, end_time, duration_s, lat, lon, delayed, "
                   "created_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,now()) "
                   "ON CONFLICT (device_id, start_time) DO NOTHING")
            args = [it["start"], it["end"], it["duration_s"], it["lat"], it["lon"], late]
        elif k == "overspeed":
            sql = (f"INSERT INTO speed_events ({common}, start_time, end_time, duration_s, max_speed_kmh, "
                   "threshold_kmh, severity, lat, lon, end_lat, end_lon, delayed, created_at) "
                   "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,now()) "
                   "ON CONFLICT (device_id, start_time) DO NOTHING")
            args = [it["start"], it["end"], it["duration_s"], it["max_speed_kmh"], it["threshold_kmh"],
                    it["severity"], it["lat"], it["lon"], it["end_lat"], it["end_lon"], late]
        elif k == "behavior":
            sql = (f"INSERT INTO driving_behavior_events ({common}, type, occurred_at, lat, lon, speed_kmh, delayed, "
                   "created_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,now()) "
                   "ON CONFLICT (device_id, type, occurred_at) DO NOTHING")
            args = [it["type"], it["at"], it["lat"], it["lon"], it["speed_kmh"], late]
        elif k == "geofence":
            sql = (f"INSERT INTO geofence_events ({common}, geofence_id, event, occurred_at, lat, lon, dwell_s, "
                   "delayed, created_at) SELECT $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,now() "
                   "WHERE EXISTS (SELECT 1 FROM geofences WHERE id = $5) "
                   "ON CONFLICT (device_id, geofence_id, event, occurred_at) DO NOTHING")
            args = [it["geofence_id"], it["event"], it["at"], it["lat"], it["lon"], it.get("dwell_s"), late]
        else:
            return 0
        status = await conn.execute(sql, *base, *args)
        return int(status.rsplit(" ", 1)[-1])

    async def save_state(self, conn: asyncpg.Connection, device_id: int, engine: Engine, last_hash: str) -> None:
        last_t = engine.state.get("last_t")
        if not last_t:
            return
        await conn.execute(
            "INSERT INTO processing_checkpoints (device_id, last_device_time, last_record_hash, updated_at, state) "
            "VALUES ($1, $2, $3, now(), $4::jsonb) ON CONFLICT (device_id) DO UPDATE SET "
            "last_device_time = EXCLUDED.last_device_time, last_record_hash = EXCLUDED.last_record_hash, "
            "updated_at = now(), state = EXCLUDED.state",
            device_id, datetime.fromisoformat(last_t), last_hash, json.dumps(engine.state))

    # --- late data ------------------------------------------------------------------------------------

    async def request_recompute(self, conn: asyncpg.Connection, device_id: int, times: list[datetime]) -> None:
        """Queue, or widen, the device's single pending replay window around these late records."""
        first, last = min(times), max(times)
        await conn.execute(
            "INSERT INTO recompute_requests (device_id, window_start, window_end, requested_at, first_late, "
            "replayed_points) VALUES ($1, $2, $3, now(), $4, 0) "
            "ON CONFLICT (device_id) WHERE done_at IS NULL DO UPDATE SET "
            "window_start = LEAST(recompute_requests.window_start, EXCLUDED.window_start), "
            "window_end = GREATEST(recompute_requests.window_end, EXCLUDED.window_end), "
            "first_late = LEAST(recompute_requests.first_late, EXCLUDED.first_late), requested_at = now()",
            device_id, first - timedelta(hours=1), last + timedelta(hours=1), first)

    async def run_due_recomputes(self, pool: asyncpg.Pool, debounce_s: float = 30.0, on_items: Any = None) -> int:
        """Replay requests that have been quiet for ``debounce_s`` (a backlog upload arrives in many batches)."""
        rows = await pool.fetch("SELECT id FROM recompute_requests WHERE done_at IS NULL "
                                "AND requested_at < now() - make_interval(secs => $1) ORDER BY requested_at LIMIT 20",
                                debounce_s)
        for r in rows:
            async with pool.acquire() as conn, conn.transaction():
                await self.recompute(conn, r["id"], on_items)
        return len(rows)

    async def missing(self, conn: asyncpg.Connection, device_id: int, after: datetime,
                      before: datetime) -> list[dict[str, Any]]:
        """Stored positions between the last analysed record and the next streamed one (stream loss recovery)."""
        rows = await conn.fetch(
            "SELECT device_time, latitude, longitude, speed_kmh, heading, ignition, motion, gps_valid, is_outlier, "
            "odometer_km, events FROM positions WHERE device_id = $1 AND device_time > $2 AND device_time < $3 "
            "AND NOT is_late ORDER BY device_time LIMIT 20000", device_id, after, before)
        return [dict(r) for r in rows]

    async def recompute(self, conn: asyncpg.Connection, request_id: int, on_items: Any = None) -> int:
        """Replay a device's window with a fresh engine and replace that window's results, in the caller's
        transaction. The replay starts at the beginning of any trip open at the window start, so trips are never
        cut; results still open at the window end and rows extending past it are left to the live engine."""
        req = await conn.fetchrow("SELECT * FROM recompute_requests WHERE id = $1 FOR UPDATE SKIP LOCKED", request_id)
        if req is None or req["done_at"] is not None:
            return 0
        device_id, ws, we = req["device_id"], req["window_start"], req["window_end"]
        spanning = await conn.fetchval("SELECT min(start_time) FROM trips WHERE device_id = $1 AND start_time < $2 "
                                       "AND end_time > $2", device_id, ws)
        start = min(ws, spanning) if spanning else ws
        ctx = (await self.contexts(conn, [device_id])).get(device_id)
        if ctx is None:
            await conn.execute("UPDATE recompute_requests SET done_at = now() WHERE id = $1", request_id)
            return 0
        for table, col_start, col_end in RESULT_TABLES:
            await conn.execute(f"DELETE FROM {table} WHERE device_id = $1 AND {col_start} >= $2 AND {col_end} < $3",
                               device_id, start, we)
        rows = await conn.fetch(
            "SELECT device_time, latitude, longitude, speed_kmh, heading, ignition, motion, gps_valid, is_outlier, "
            "odometer_km, events FROM positions WHERE device_id = $1 AND device_time >= $2 AND device_time < $3 "
            "ORDER BY device_time", device_id, start, we)
        engine = await self.engine(conn, ctx, fresh=True)
        items: list[dict[str, Any]] = []
        for r in rows:
            items += engine.process(sample_from_record(dict(r)))
        await self.write(conn, ctx, items, delayed_from=req["first_late"])
        if on_items is not None:            # e.g. delayed alerts for results produced by the late data
            await on_items(conn, ctx, [i for i in items if (i.get("end") or i.get("at")) >= req["first_late"]])
        await conn.execute("UPDATE recompute_requests SET done_at = now(), replayed_points = $2 WHERE id = $1",
                           request_id, len(rows))
        log.info("recomputed", extra={"device_id": device_id, "points": len(rows), "items": len(items)})
        return len(rows)
