"""Annotator, clock offset, listener manager, registry cache and inbox throttling (no database)."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from gpsgw.app import _manual_device_time
from gpsgw.listeners.manager import ListenerManager
from gpsgw.normalization.io_mapper import normalize
from gpsgw.protocols.base import RawRecord, default_registry
from gpsgw.services.annotate import PositionAnnotator, haversine_km
from gpsgw.services.memory import MemoryPersistence, MemoryPublisher
from gpsgw.services.pipeline import IngestPipeline, PersistenceFailed
from gpsgw.services.ports import DeviceInfo, LastFix, UnknownAttempt
from gpsgw.services.postgres import ListenerSpec, PostgresInbox, PostgresRegistry

T0 = datetime(2026, 9, 30, 8, 0, tzinfo=UTC)
DAR = (-6.8160, 39.2803)


def rec(minutes: float, lat: float = DAR[0], lon: float = DAR[1], sats: int = 10):
    raw = RawRecord(device_time=T0 + timedelta(minutes=minutes), latitude=lat, longitude=lon, satellites=sats)
    return normalize(raw, identifier="x", protocol="teltonika", profile=None, server_time=T0 + timedelta(days=1))


async def no_fix(_: int) -> LastFix | None:
    return None


def test_haversine_dar_to_morogoro():
    assert haversine_km(-6.8160, 39.2803, -6.8278, 37.6591) == pytest.approx(179, abs=2)


async def test_late_records_flagged_against_previous_batches():
    a = PositionAnnotator(no_fix)
    out, track = await a.annotate(1, [rec(10), rec(11)])
    assert not any(r.is_late for r in out)
    a.commit(1, track)
    out, _ = await a.annotate(1, [rec(5), rec(12)])        # 08:05 arrives after 08:11 was stored
    assert [r.is_late for r in out] == [True, False]


async def test_gps_jump_is_outlier_and_does_not_move_the_reference():
    a = PositionAnnotator(no_fix)
    out, track = await a.annotate(1, [rec(0), rec(1, lat=-3.37, lon=36.68), rec(2, lat=-6.8170, lon=39.2810)])
    assert [r.is_outlier for r in out] == [False, True, False]   # Arusha within a minute is impossible
    assert track.latitude == pytest.approx(-6.8170)


async def test_manual_position_can_move_the_live_reference_without_jump_filtering():
    async def stored(_: int) -> LastFix:
        return LastFix(T0, T0, *DAR)

    manual = rec(1, lat=-3.37, lon=36.68).model_copy(update={"raw_metadata": {"manual_position": True}})
    out, track = await PositionAnnotator(stored).annotate(1, [manual])
    assert not out[0].is_outlier and track.latitude == pytest.approx(-3.37)


async def test_long_gap_is_not_a_jump_and_invalid_fixes_are_ignored():
    a = PositionAnnotator(no_fix)
    out, _ = await a.annotate(1, [rec(0), rec(0.5, lat=0, lon=0, sats=0), rec(240, lat=-6.8278, lon=37.6591)])
    assert not any(r.is_outlier for r in out)                    # 180 km in 4 hours is a normal drive


async def test_state_only_advances_after_commit():
    """A failed write followed by the device's resend must not mark the resent records late."""
    a = PositionAnnotator(no_fix)
    first, _ = await a.annotate(1, [rec(10)])                    # not committed (write failed)
    resend, _ = await a.annotate(1, [rec(10)])
    assert not resend[0].is_late


async def test_annotator_seeds_from_stored_state():
    async def stored(_: int) -> LastFix:
        return LastFix(T0 + timedelta(minutes=30), T0 + timedelta(minutes=30), *DAR)
    out, _ = await PositionAnnotator(stored).annotate(1, [rec(20)])
    assert out[0].is_late


async def test_pipeline_applies_clock_offset_and_commits_only_on_success():
    persistence = MemoryPersistence()
    pipeline = IngestPipeline(persistence, MemoryPublisher())
    device = DeviceInfo(1, "x", "gt06", 1, None, None, time_offset_minutes=180)   # device reports EAT local time
    raw = RawRecord(device_time=T0 + timedelta(hours=3), latitude=DAR[0], longitude=DAR[1], satellites=9)
    persistence.fail = True
    with pytest.raises(PersistenceFailed):
        await pipeline.ingest(device, [raw])
    persistence.fail = False
    assert await pipeline.ingest(device, [raw]) == 1
    (stored,) = persistence.rows.values()
    assert stored.device_time == T0 and not stored.is_late
    assert pipeline.stats.records_stored == 1 and pipeline.stats.persist_failures == 1


def test_manual_device_time_uses_submitted_timestamp_as_utc():
    assert _manual_device_time("2026-11-09T10:30:00+03:00") == datetime(2026, 11, 9, 7, 30, tzinfo=UTC)
    with pytest.raises(ValueError, match="include a timezone"):
        _manual_device_time("2026-11-09T10:30:00")


def test_pipeline_does_not_apply_tracker_offset_to_manual_timestamp():
    async def run() -> None:
        persistence = MemoryPersistence()
        pipeline = IngestPipeline(persistence, MemoryPublisher())
        device = DeviceInfo(1, "x", "teltonika", 1, None, None, time_offset_minutes=180)
        raw = RawRecord(device_time=T0, latitude=DAR[0], longitude=DAR[1], satellites=9,
                        extra={"manual_position": True})

        assert await pipeline.ingest(device, [raw]) == 1
        (stored,) = persistence.rows.values()
        assert stored.device_time == T0

    asyncio.run(run())


# --- listener manager -------------------------------------------------------------------------------------

class FakeListener:
    def __init__(self, decoder, transport, port, fail=False):  # type: ignore[no-untyped-def]
        self.decoder, self.transport, self.port, self.fail = decoder, transport, port, fail
        self.started = self.stopped = False

    async def start(self, host: str = "0.0.0.0") -> int:
        if self.fail:
            raise OSError("address already in use")
        self.started = True
        return self.port

    async def stop(self) -> None:
        self.stopped = True


async def test_listener_manager_reconciles():
    specs = [ListenerSpec(5027, "tcp", "teltonika"), ListenerSpec(5027, "udp", "teltonika"),
             ListenerSpec(5023, "tcp", "gt06"), ListenerSpec(5055, "http", "generic_json")]
    made: list[FakeListener] = []
    busy = {5101}

    def factory(decoder, transport, port):  # type: ignore[no-untyped-def]
        made.append(FakeListener(decoder, transport, port, fail=port in busy))
        return made[-1]

    async def source():  # type: ignore[no-untyped-def]
        return list(specs)

    m = ListenerManager(default_registry(), factory, source)
    assert set(await m.reconcile()) == {(5027, "tcp"), (5027, "udp"), (5023, "tcp"), (5055, "http")}
    specs.append(ListenerSpec(5102, "udp", "gt06"))                      # GT06 is TCP only: skipped
    specs.append(ListenerSpec(5103, "tcp", "generic_text"))              # no format: reported, not started
    current = await m.reconcile()
    assert (5102, "udp") not in current and (5103, "tcp") in m.failed
    fmt = {"kind": "delimited", "identifier": 0, "latitude": 1, "longitude": 2, "slug": "csv"}
    specs[-1] = ListenerSpec(5103, "tcp", "generic_text", {"format": fmt})
    assert (5103, "tcp") in await m.reconcile()
    first = next(lst for lst in made if lst.port == 5103 and not lst.stopped)
    assert first.decoder.format.identifier == 0
    specs[-1] = ListenerSpec(5103, "tcp", "generic_text", {"format": {**fmt, "separator": ";"}})
    await m.reconcile()                                                   # edited format: listener restarted
    restarted = next(lst for lst in made if lst.port == 5103 and not lst.stopped)
    assert first.stopped and restarted.decoder.format.separator == ";"
    specs[:] = [s for s in specs if s.port not in (5102, 5103)]
    specs.append(ListenerSpec(5100, "tcp", "teltonika"))
    specs.append(ListenerSpec(5101, "tcp", "teltonika"))
    assert (5100, "tcp") in await m.reconcile()
    assert (5101, "tcp") in m.failed                                     # port busy: reported, retried later
    specs[:] = [s for s in specs if s.port != 5027]
    current = await m.reconcile()
    assert (5027, "tcp") not in current and all(lst.stopped for lst in made if lst.port == 5027)
    busy.clear()
    assert (5101, "tcp") in await m.reconcile()


async def test_listener_manager_keeps_running_when_config_unavailable():
    async def broken():  # type: ignore[no-untyped-def]
        raise ConnectionError("db down")
    m = ListenerManager(default_registry(), lambda d, t, p: FakeListener(d, t, p), broken)
    assert await m.reconcile() == {}


# --- registry cache and inbox throttling with fake pools ---------------------------------------------------

class FakePool:
    def __init__(self, rows: dict[str, dict] | None = None) -> None:  # type: ignore[type-arg]
        self.rows = rows or {}
        self.fetches = 0
        self.executed: list[tuple] = []  # type: ignore[type-arg]

    async def fetchrow(self, sql: str, identifier: str):  # type: ignore[no-untyped-def]
        self.fetches += 1
        return self.rows.get(identifier)

    async def execute(self, sql: str, *args):  # type: ignore[no-untyped-def]
        self.executed.append(args)


def device_row(**kw):  # type: ignore[no-untyped-def]
    row = dict(id=7, identifier="356307042441013", organization_id=3, is_active=True, log_raw_packets=False,
               time_offset_minutes=0, protocol="teltonika", protocol_enabled=True, org_active=True,
               mappings=[{"io_id": 239, "kind": "field", "target": "ignition"}], profile_slug="p", vehicle_id=11,
               format_slug=None)
    row.update(kw)
    return row


async def test_registry_caches_and_invalidates():
    now = [0.0]
    pool = FakePool({"356307042441013": device_row()})
    reg = PostgresRegistry(pool, ttl=60, negative_ttl=60, clock=lambda: now[0])  # type: ignore[arg-type]
    dev = await reg.lookup("356307042441013", "teltonika")
    assert dev and dev.vehicle_id == 11 and dev.profile is not None
    await reg.lookup("356307042441013", "teltonika")
    assert pool.fetches == 1                                             # cached
    assert await reg.lookup("356307042441013", "gt06") is None          # wrong port/protocol
    assert await reg.lookup("490154203237518", "teltonika") is None
    assert await reg.lookup("490154203237518", "teltonika") is None
    assert pool.fetches == 2                                             # unknown identifiers cached too
    pool.rows["490154203237518"] = device_row(id=8, identifier="490154203237518")
    reg.invalidate("490154203237518")                                    # registered in the UI
    assert (await reg.lookup("490154203237518", "teltonika")).device_id == 8
    pool.rows["356307042441013"] = device_row(is_active=False)
    now[0] = 61                                                          # TTL converges without an event
    assert await reg.lookup("356307042441013", "teltonika") is None


@pytest.mark.parametrize("override", [{"org_active": False}, {"protocol_enabled": False}, {"is_active": False}])
async def test_registry_refuses_inactive(override):
    reg = PostgresRegistry(FakePool({"x": device_row(**override)}))  # type: ignore[arg-type]
    assert await reg.lookup("x", "teltonika") is None


async def test_registry_tolerates_a_broken_profile():
    reg = PostgresRegistry(FakePool({"x": device_row(mappings=[{"io_id": 1, "kind": "field", "target": "bogus"}])}))  # type: ignore[arg-type]
    dev = await reg.lookup("x", "teltonika")
    assert dev is not None and dev.profile is None


async def test_inbox_throttles_and_aggregates_attempts():
    now = [0.0]
    pool = FakePool()
    inbox = PostgresInbox(pool, min_interval=10, clock=lambda: now[0])  # type: ignore[arg-type]
    attempt = UnknownAttempt("490154203237518", "teltonika", "tcp", 5027, "1.2.3.4:5", T0, "00")
    for _ in range(5):
        await inbox.record(attempt)
    assert len(pool.executed) == 1 and pool.executed[0][6] == 1
    now[0] = 11
    await inbox.record(attempt)
    assert len(pool.executed) == 2 and pool.executed[1][6] == 5          # 4 throttled + this one


async def test_token_lookup_formats_and_listener_specs():
    """Phase 10 adapters: HTTP tokens (by SHA-256), cached formats and per-port format options."""
    import hashlib

    from gpsgw.services.postgres import PostgresFormats, load_listener_specs

    digest = hashlib.sha256(b"tok").hexdigest()
    pool = FakePool({digest: device_row(identifier="HTTP-1", protocol="generic_json", format_slug="acme")})
    reg = PostgresRegistry(pool, ttl=60, negative_ttl=60)  # type: ignore[arg-type]
    dev = await reg.lookup_token("tok")
    assert dev is not None and dev.format_slug == "acme" and await reg.lookup_token("tok") is dev
    assert pool.fetches == 1 and await reg.lookup_token("nope") is None

    class FormatPool:
        calls = 0

        async def fetchrow(self, sql: str, slug: str):  # type: ignore[no-untyped-def]
            FormatPool.calls += 1
            return {"definition": {"kind": "json"}} if slug == "acme" else None

        async def fetch(self, sql: str):  # type: ignore[no-untyped-def]
            return [{"port": 5100, "transport": "tcp", "name": "generic_text", "format_slug": "csv",
                     "format_definition": {"kind": "delimited"}},
                    {"port": 5027, "transport": "tcp", "name": "teltonika", "format_slug": None,
                     "format_definition": None}]
    formats = PostgresFormats(FormatPool())  # type: ignore[arg-type]
    assert await formats.get("acme") == {"kind": "json"} and await formats.get("acme") == {"kind": "json"}
    assert await formats.get("missing") is None and FormatPool.calls == 2
    specs = await load_listener_specs(FormatPool())  # type: ignore[arg-type]
    assert specs[0].options == {"format": {"kind": "delimited", "slug": "csv"}} and specs[1].options == {}
    assert len(specs[0].options_key) == 12


async def test_save_retries_deadlocks():
    """Concurrent first inserts into a new TimescaleDB chunk can deadlock; the save is retried."""
    import asyncpg

    from gpsgw.services.postgres import PostgresPersistence

    class Pool:
        calls = 0

        async def fetchval(self, sql: str, *args: object) -> int:
            Pool.calls += 1
            if Pool.calls < 3:
                raise asyncpg.exceptions.DeadlockDetectedError("deadlock detected")
            return 2
    p = PostgresPersistence(Pool())  # type: ignore[arg-type]
    assert await p._save_with_retry(1) == 2 and p.retries == 2

    class Always(Pool):
        async def fetchval(self, sql: str, *args: object) -> int:
            raise asyncpg.exceptions.DeadlockDetectedError("deadlock detected")
    with pytest.raises(asyncpg.exceptions.DeadlockDetectedError):
        await PostgresPersistence(Always())._save_with_retry(1)  # type: ignore[arg-type]
