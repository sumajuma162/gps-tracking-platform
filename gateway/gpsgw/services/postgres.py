"""PostgreSQL implementations of the gateway ports (asyncpg).

The gateway connects as the database owner role, so it is not subject to the web app's Row-Level Security; it
always writes rows with the organization the device belongs to at that moment (spec A4 rule 12).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import asyncpg

from gpsgw.normalization.io_mapper import IoMappingProfile
from gpsgw.normalization.normalized_location import NormalizedLocation
from gpsgw.protocols.base import ProtocolRegistry
from gpsgw.services.ports import DeviceInfo, LastFix, UnknownAttempt

log = logging.getLogger("gpsgw.postgres")


async def _init_connection(conn: asyncpg.Connection) -> None:
    for name in ("json", "jsonb"):
        await conn.set_type_codec(name, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def create_pool(dsn: str, min_size: int = 1, max_size: int = 10) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size, init=_init_connection,
                                     command_timeout=15)


# --- Registry -------------------------------------------------------------------------------------------

DEVICE_SQL = """
SELECT d.id, d.identifier, d.organization_id, d.is_active, d.log_raw_packets,
       dm.time_offset_minutes, p.name AS protocol, p.is_enabled AS protocol_enabled,
       o.is_active AND o.deleted_at IS NULL AS org_active,
       v.mappings, prof.slug AS profile_slug, gf.slug AS format_slug,
       (SELECT da.vehicle_id FROM device_assignments da
         WHERE da.device_id = d.id AND da.unassigned_at IS NULL LIMIT 1) AS vehicle_id
FROM devices d
JOIN device_models dm ON dm.id = d.device_model_id
JOIN device_protocols p ON p.id = dm.protocol_id
JOIN organizations o ON o.id = d.organization_id
LEFT JOIN io_mapping_profiles prof ON prof.id = dm.io_profile_id
LEFT JOIN io_mapping_profile_versions v ON v.id = prof.current_version_id
LEFT JOIN generic_formats gf ON gf.id = dm.generic_format_id
WHERE {where} AND d.deleted_at IS NULL
"""


@dataclass
class _Entry:
    device: DeviceInfo | None
    expires: float


class PostgresRegistry:
    """Identifier -> DeviceInfo with a short cache.

    Unknown identifiers are cached too (negative cache, spec A7) so a flood of fake IMEIs cannot hammer the
    database. ``invalidate`` is called from the Redis ``device_registry`` channel; the TTL makes the cache
    converge even if a message is lost.
    """

    def __init__(self, pool: asyncpg.Pool, ttl: float = 60.0, negative_ttl: float = 60.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.pool = pool
        self.ttl = ttl
        self.negative_ttl = negative_ttl
        self._clock = clock
        self._cache: dict[str, _Entry] = {}
        self.queries = 0

    def invalidate(self, identifier: str | None = None) -> None:
        if identifier is None:
            self._cache.clear()
        else:
            self._cache.pop(identifier, None)

    async def lookup(self, identifier: str, protocol: str) -> DeviceInfo | None:
        now = self._clock()
        entry = self._cache.get(identifier)
        if entry is None or entry.expires <= now:
            device = await self._fetch(identifier)
            entry = _Entry(device, now + (self.ttl if device else self.negative_ttl))
            self._cache[identifier] = entry
        device = entry.device
        return device if device is not None and device.protocol == protocol else None

    async def _fetch(self, identifier: str) -> DeviceInfo | None:
        self.queries += 1
        row = await self.pool.fetchrow(DEVICE_SQL.format(where="d.identifier = $1"), identifier)
        return self._device(row)

    async def lookup_token(self, token: str) -> DeviceInfo | None:
        """HTTP trackers: the per-device token (only its SHA-256 is stored). Short positive cache."""
        import hashlib

        digest = hashlib.sha256(token.encode()).hexdigest()
        now = self._clock()
        key = f"token:{digest}"
        entry = self._cache.get(key)
        if entry is None or entry.expires <= now:
            self.queries += 1
            row = await self.pool.fetchrow(DEVICE_SQL.format(where="d.ingest_token_hash = $1"), digest)
            device = self._device(row)
            entry = _Entry(device, now + (self.ttl if device else self.negative_ttl))
            self._cache[key] = entry
        return entry.device

    def _device(self, row: Any) -> DeviceInfo | None:
        if row is None or not row["is_active"] or not row["org_active"] or not row["protocol_enabled"]:
            return None
        profile = None
        if row["mappings"] is not None:
            try:
                profile = IoMappingProfile.model_validate({"name": row["profile_slug"], "mappings": row["mappings"]})
            except ValueError as exc:  # keep ingesting; raw IO values stay in raw_metadata
                log.error("invalid IO profile; storing unmapped IO", extra={"profile": row["profile_slug"],
                                                                             "error": str(exc)[:200]})
        return DeviceInfo(device_id=row["id"], identifier=row["identifier"], protocol=row["protocol"],
                          organization_id=row["organization_id"], vehicle_id=row["vehicle_id"], profile=profile,
                          active=True, log_raw_packets=row["log_raw_packets"],
                          time_offset_minutes=row["time_offset_minutes"], format_slug=row["format_slug"])


# --- Persistence -----------------------------------------------------------------------------------------

INSERT_POSITIONS = """
INSERT INTO positions (organization_id, device_id, vehicle_id, device_time, server_time, latitude, longitude,
    altitude, speed_kmh, heading, satellites, hdop, gps_valid, is_outlier, is_late, ignition, motion, odometer_km,
    engine_hours, battery_voltage, external_voltage, battery_level, gsm_signal, event_code, events, sensors,
    protocol, raw_metadata, record_hash)
SELECT $1, $2, $3, u.dt, u.st, u.lat, u.lon, u.alt, u.spd, u.hdg, u.sat, u.hdop, u.valid, u.outlier, u.late,
       u.ign, u.mot, u.odo, u.eng, u.bat, u.ext, u.lvl, u.gsm, u.evt, u.events::jsonb, u.sensors::jsonb, $4,
       u.meta::jsonb, u.hash
FROM unnest($5::timestamptz[], $6::timestamptz[], $7::float8[], $8::float8[], $9::float4[], $10::float4[],
            $11::float4[], $12::int2[], $13::float4[], $14::bool[], $15::bool[], $16::bool[], $17::bool[],
            $18::bool[], $19::float8[], $20::float8[], $21::float4[], $22::float4[], $23::float4[], $24::int2[],
            $25::int4[], $26::text[], $27::text[], $28::text[], $29::text[])
     AS u(dt, st, lat, lon, alt, spd, hdg, sat, hdop, valid, outlier, late, ign, mot, odo, eng, bat, ext, lvl, gsm,
          evt, events, sensors, meta, hash)
ON CONFLICT (device_id, device_time, record_hash) DO NOTHING
RETURNING 1
"""

# One statement per packet (spec A4.3): positions, live state and the device's last-seen time are written by
# data-modifying CTEs, which PostgreSQL runs atomically as one statement: a single round trip instead of six.
# Position fields move only forward in device time: a backlog upload never drags the live marker back.
_NEWER = "($38::timestamptz IS NOT NULL AND (device_state.device_time IS NULL OR device_state.device_time < $38))"
SAVE_SQL = """
WITH ins AS (""" + INSERT_POSITIONS + """),
st AS (
INSERT INTO device_state (device_id, organization_id, vehicle_id, status, last_packet_at, gps_valid, ignition, motion,
    external_voltage, battery_voltage, gsm_signal, odometer_km, io, device_time, latitude, longitude, speed_kmh,
    heading, satellites, updated_at)
VALUES ($2, $1, $3, 'UNKNOWN', $30, $38::timestamptz IS NOT NULL, $31, $32, $33, $34, $35, $36, $37::jsonb, $38,
        $39, $40, $41, $42, $43, now())
ON CONFLICT (device_id) DO UPDATE SET
    organization_id = EXCLUDED.organization_id, vehicle_id = EXCLUDED.vehicle_id,
    last_packet_at = EXCLUDED.last_packet_at, ignition = coalesce(EXCLUDED.ignition, device_state.ignition),
    motion = coalesce(EXCLUDED.motion, device_state.motion),
    external_voltage = coalesce(EXCLUDED.external_voltage, device_state.external_voltage),
    battery_voltage = coalesce(EXCLUDED.battery_voltage, device_state.battery_voltage),
    gsm_signal = coalesce(EXCLUDED.gsm_signal, device_state.gsm_signal),
    odometer_km = coalesce(EXCLUDED.odometer_km, device_state.odometer_km),
    io = EXCLUDED.io, updated_at = now(),
    device_time = CASE WHEN {n} THEN $38 ELSE device_state.device_time END,
    latitude = CASE WHEN {n} THEN $39 ELSE device_state.latitude END,
    longitude = CASE WHEN {n} THEN $40 ELSE device_state.longitude END,
    speed_kmh = CASE WHEN {n} THEN $41 ELSE device_state.speed_kmh END,
    heading = CASE WHEN {n} THEN $42 ELSE device_state.heading END,
    satellites = CASE WHEN {n} THEN $43 ELSE device_state.satellites END,
    gps_valid = CASE WHEN {n} THEN true ELSE device_state.gps_valid END
RETURNING 1),
dev AS (UPDATE devices SET last_packet_at = $30 WHERE id = $2 AND $44)
SELECT count(*) FROM ins
""".replace("{n}", _NEWER)  # noqa: S608 - constants of this module only; values are bind parameters
#: devices.last_packet_at ("last seen" in lists) is refreshed at most this often per device; the live state's
#: last_packet_at is exact for every packet.
LAST_SEEN_EVERY_S = 30.0


def _arrays(records: list[NormalizedLocation]) -> list[list[Any]]:
    def col(fn: Callable[[NormalizedLocation], Any]) -> list[Any]:
        return [fn(r) for r in records]
    return [
        col(lambda r: r.device_time), col(lambda r: r.server_time), col(lambda r: r.latitude),
        col(lambda r: r.longitude), col(lambda r: r.altitude), col(lambda r: r.speed_kmh), col(lambda r: r.heading),
        col(lambda r: r.satellites), col(lambda r: r.hdop), col(lambda r: r.gps_valid), col(lambda r: r.is_outlier),
        col(lambda r: r.is_late), col(lambda r: r.ignition), col(lambda r: r.motion), col(lambda r: r.odometer_km),
        col(lambda r: r.engine_hours), col(lambda r: r.battery_voltage), col(lambda r: r.external_voltage),
        col(lambda r: r.battery_level), col(lambda r: r.gsm_signal), col(lambda r: r.event_code),
        col(lambda r: json.dumps(r.events)), col(lambda r: json.dumps(r.sensors)),
        col(lambda r: json.dumps(r.raw_metadata)), col(lambda r: r.record_hash),
    ]


class PostgresPersistence:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool
        self._last_seen: dict[int, float] = {}
        self.retries = 0

    async def save(self, device: DeviceInfo, records: list[NormalizedLocation]) -> int:
        """One atomic statement: positions (deduplicated), live state, device last-seen. Raises on any failure."""
        current = [r for r in records if not r.is_late]
        src = max(current, key=lambda r: r.device_time) if current else None
        fix = max((r for r in current if r.gps_valid and not r.is_outlier), key=lambda r: r.device_time,
                  default=None)
        now = datetime.now(UTC)
        clock = time.monotonic()
        touch = clock - self._last_seen.get(device.device_id, -1e9) >= LAST_SEEN_EVERY_S
        count = await self._save_with_retry(
            device.organization_id, device.device_id, device.vehicle_id, device.protocol,
            *_arrays(records), now,
            src.ignition if src else None, src.motion if src else None,
            src.external_voltage if src else None, src.battery_voltage if src else None,
            src.gsm_signal if src else None, src.odometer_km if src else None,
            src.raw_metadata.get("io", {}) if src else {},
            fix.device_time if fix else None, fix.latitude if fix else None, fix.longitude if fix else None,
            fix.speed_kmh if fix else None, fix.heading if fix else None, fix.satellites if fix else None, touch)
        if touch:
            self._last_seen[device.device_id] = clock
        return int(count)

    async def _save_with_retry(self, *args: Any) -> int:
        """Retry the statement on a deadlock or serialization failure. When a new time range starts, every
        tracker's first insert creates the same TimescaleDB chunk at once; PostgreSQL resolves the resulting lock
        cycle by cancelling some of them. A quick retry stores those records instead of dropping the device's
        connection (it would resend anyway: nothing is lost either way)."""
        for attempt in range(3):
            try:
                return int(await self.pool.fetchval(SAVE_SQL, *args))
            except (asyncpg.exceptions.DeadlockDetectedError, asyncpg.exceptions.SerializationError):
                if attempt == 2:
                    raise
                self.retries += 1
                await asyncio.sleep(0.05 * (attempt + 1))
        raise AssertionError("unreachable")

    async def last_fix(self, device_id: int) -> LastFix | None:
        row = await self.pool.fetchrow(
            "SELECT device_time, latitude, longitude FROM device_state WHERE device_id = $1", device_id)
        newest = await self.pool.fetchval(
            "SELECT max(device_time) FROM positions WHERE device_id = $1 AND device_time > now() - INTERVAL '30 days'",
            device_id)
        if row is None and newest is None:
            return None
        return LastFix(newest or (row["device_time"] if row else None), row["device_time"] if row else None,
                       row["latitude"] if row else None, row["longitude"] if row else None)

    async def record_raw(self, device: DeviceInfo, direction: str, transport: str, payload: bytes,
                         result: str) -> None:
        await self.pool.execute(
            "INSERT INTO raw_packets (organization_id, device_id, direction, transport, payload_hex, decode_result) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            device.organization_id, device.device_id, direction, transport, payload.hex()[:131072], result[:60])


# --- Inbox and connection history --------------------------------------------------------------------------

UPSERT_ATTEMPT = """
INSERT INTO unregistered_device_attempts (identifier, protocol, transport, port, last_peer, first_seen_at,
    last_seen_at, attempts, sample_hex)
VALUES ($1, $2, $3, $4, $5, $6, $6, $7, $8)
ON CONFLICT (identifier, protocol) DO UPDATE SET
    transport = EXCLUDED.transport, port = EXCLUDED.port, last_peer = EXCLUDED.last_peer,
    last_seen_at = EXCLUDED.last_seen_at,
    attempts = unregistered_device_attempts.attempts + EXCLUDED.attempts,
    sample_hex = EXCLUDED.sample_hex, dismissed_at = NULL
"""


class PostgresInbox:
    """Records unknown identifiers. Writes at most once per identifier per ``min_interval`` seconds, adding up
    the attempts seen meanwhile, so a device retrying in a tight loop cannot flood the database."""

    def __init__(self, pool: asyncpg.Pool, min_interval: float = 10.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.pool = pool
        self.min_interval = min_interval
        self._clock = clock
        self._last_write: dict[tuple[str, str], float] = {}
        self._pending: dict[tuple[str, str], int] = {}

    async def record(self, attempt: UnknownAttempt) -> None:
        key = (attempt.identifier, attempt.protocol)
        self._pending[key] = self._pending.get(key, 0) + 1
        now = self._clock()
        if now - self._last_write.get(key, float("-inf")) < self.min_interval:
            return
        count = self._pending.pop(key)
        self._last_write[key] = now
        if len(self._last_write) > 10_000:  # bound memory under a flood of random identifiers
            self._last_write.clear()
        await self.pool.execute(UPSERT_ATTEMPT, attempt.identifier, attempt.protocol, attempt.transport,
                                attempt.port, attempt.peer[:64], attempt.seen_at, count, attempt.sample_hex)


class PostgresConnectionLog:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def opened(self, device: DeviceInfo, transport: str, port: int, peer: str) -> int | None:
        now = datetime.now(UTC)
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("UPDATE devices SET last_connected_at = $2 WHERE id = $1", device.device_id, now)
            return await conn.fetchval(  # type: ignore[no-any-return]
                "INSERT INTO device_connections (organization_id, device_id, transport, port, peer, connected_at, "
                "close_reason, packets, records) VALUES ($1, $2, $3, $4, $5, $6, '', 0, 0) RETURNING id",
                device.organization_id, device.device_id, transport, port, peer[:64], now)

    async def closed(self, connection_id: int, reason: str, packets: int, records: int) -> None:
        await self.pool.execute(
            "UPDATE device_connections SET disconnected_at = now(), close_reason = $2, packets = $3, records = $4 "
            "WHERE id = $1", connection_id, reason[:40], packets, records)


# --- Protocol sync and listener configuration ---------------------------------------------------------------

async def sync_protocols(pool: asyncpg.Pool, registry: ProtocolRegistry) -> None:
    """Tell the backend which decoders this gateway actually runs (spec A6)."""
    described = registry.describe()
    async with pool.acquire() as conn, conn.transaction():
        for d in described:
            await conn.execute(
                "INSERT INTO device_protocols (name, display_name, transports, supports_commands, implemented, "
                "is_enabled, created_at, updated_at) VALUES ($1, $1, $2::jsonb, $3, true, true, now(), now()) "
                "ON CONFLICT (name) DO UPDATE SET transports = EXCLUDED.transports, "
                "supports_commands = EXCLUDED.supports_commands, implemented = true, updated_at = now()",
                d["name"], d["transports"], d["supports_commands"])  # jsonb codec encodes
        await conn.execute("UPDATE device_protocols SET implemented = false, updated_at = now() "
                           "WHERE implemented AND NOT (name = ANY($1::text[]))", [d["name"] for d in described])


@dataclass(frozen=True)
class ListenerSpec:
    port: int
    transport: str
    protocol: str
    #: Per-port configuration, e.g. ``{"format": {...}}`` for generic text listeners.
    options: dict[str, Any] = field(default_factory=dict)

    @property
    def options_key(self) -> str:
        import hashlib
        import json as _json
        return hashlib.sha256(_json.dumps(self.options, sort_keys=True).encode()).hexdigest()[:12]


async def load_listener_specs(pool: asyncpg.Pool) -> list[ListenerSpec]:
    rows = await pool.fetch(
        "SELECT lp.port, lp.transport, p.name, gf.slug AS format_slug, gf.definition AS format_definition "
        "FROM listener_ports lp JOIN device_protocols p ON p.id = lp.protocol_id "
        "LEFT JOIN generic_formats gf ON gf.id = lp.generic_format_id AND gf.is_active "
        "WHERE lp.is_enabled AND p.is_enabled ORDER BY lp.port, lp.transport")
    out = []
    for r in rows:
        definition = r["format_definition"]
        options = {"format": {**definition, "slug": r["format_slug"]}} if definition else {}
        out.append(ListenerSpec(r["port"], r["transport"], r["name"], options))
    return out


class PostgresFormats:
    """Generic formats by slug for the HTTP listener, cached briefly (edits apply within ``ttl`` seconds)."""

    def __init__(self, pool: asyncpg.Pool, ttl: float = 10.0) -> None:
        self.pool, self.ttl = pool, ttl
        self._cache: dict[str, tuple[float, dict[str, Any] | None]] = {}

    async def get(self, slug: str) -> dict[str, Any] | None:
        hit = self._cache.get(slug)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        row = await self.pool.fetchrow("SELECT definition FROM generic_formats WHERE slug = $1 AND is_active AND "
                                       "kind = 'json'", slug)
        value = dict(row["definition"]) if row else None
        if len(self._cache) > 1000:
            self._cache.clear()
        self._cache[slug] = (time.monotonic() + self.ttl, value)
        return value
