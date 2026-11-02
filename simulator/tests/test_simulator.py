"""Simulator: route geometry, vehicle behaviour, and every A16 scenario against an in-process gateway."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from gpsgw.listeners.tcp import TcpListener
from gpsgw.listeners.udp import UdpListener
from gpsgw.normalization.io_mapper import IoMappingProfile
from gpsgw.protocols.teltonika.decoder import TeltonikaDecoder
from gpsgw.services.memory import MemoryInbox, MemoryPersistence, MemoryPublisher, MemoryRegistry
from gpsgw.services.pipeline import IngestPipeline
from gpsgw.services.ports import DeviceInfo

from gpssim.routes import Route, distance_km
from gpssim.scenarios import SCENARIOS, run_device
from gpssim.vehicle import Behaviour, VehicleSim

IMEI = "356307042441013"
T0 = datetime(2026, 9, 30, 6, 0, tzinfo=UTC)


def test_routes_are_plausible_city_distances():
    r = Route.named("morogoro_road")
    assert 15 < r.length_km < 25                                     # Kariakoo to Mbezi is about 20 km
    (lat, lon), heading, leg = r.locate(r.length_km / 2)
    assert -6.9 < lat < -6.7 and 39.1 < lon < 39.3 and 0 <= heading < 360
    assert r.locate(0)[0] == r.points[0] and r.locate(10_000)[0] == r.points[-1]
    assert distance_km(r.points[0], r.points[1]) == pytest.approx(r.legs_km[0])


def test_vehicle_drives_stops_and_switches_engine_off():
    sim = VehicleSim(Route.named("port_airport"), T0, 10.0, Behaviour(stop_minutes=2, idle_minutes=0.5))
    recs = [sim.tick() for _ in range(400)]
    speeds = [r.speed_kmh for r in recs]
    assert max(speeds) <= 70 and max(speeds) > 40
    assert all(abs(a - b) <= 30 for a, b in zip(speeds, speeds[1:], strict=False))   # no impossible accelerations
    engine_off = [r for r in recs if r.io[239][1] == 0]
    assert engine_off and all(r.speed_kmh == 0 for r in engine_off)
    odo = [r.io[16][1] for r in recs]
    assert odo == sorted(odo) and odo[-1] > odo[0]
    assert all(r.time > p.time for p, r in zip(recs, recs[1:], strict=False))


def test_overspeed_and_long_idle_behaviours():
    fast = VehicleSim(Route.named("morogoro_road"), T0, 10.0, Behaviour(overspeed_leg=1))
    assert max(fast.tick().speed_kmh for _ in range(300)) > 100
    idle = VehicleSim(Route.named("morogoro_road"), T0, 10.0, Behaviour(long_idle_minutes=8))
    recs = [idle.tick() for _ in range(300)]
    run = best = 0
    for r in recs:
        run = run + 1 if r.speed_kmh == 0 and r.io[239][1] == 1 else 0
        best = max(best, run)
    assert best * 10 >= 8 * 60                                       # >= 8 minutes engine on, standing still


@pytest.fixture
async def gateway():
    profile = IoMappingProfile.load_builtin("teltonika_fmc920")
    persistence = MemoryPersistence()
    registry = MemoryRegistry([DeviceInfo(1, IMEI, "teltonika", 1, 1, profile)])
    pipeline = IngestPipeline(persistence, MemoryPublisher())
    inbox = MemoryInbox()
    tcp = TcpListener(TeltonikaDecoder(), registry, pipeline, inbox)
    udp = UdpListener(TeltonikaDecoder(), registry, pipeline, inbox)
    ports = (await tcp.start("127.0.0.1"), await udp.start("127.0.0.1"))
    yield ports, persistence, inbox, pipeline
    await tcp.stop()
    await udp.stop()


def rows(persistence: MemoryPersistence):  # type: ignore[no-untyped-def]
    return sorted(persistence.rows.values(), key=lambda r: r.device_time)


async def test_normal_day_through_the_real_gateway(gateway):
    (tcp, _), persistence, _, _ = gateway
    stats = await run_device("127.0.0.1", tcp, IMEI, "normal_day", steps=40, start=T0)
    assert stats.records_acked == 40 and not stats.rejected
    stored = rows(persistence)
    assert len(stored) == 40 and all(r.gps_valid for r in stored)
    assert stored[-1].odometer_km > stored[0].odometer_km and stored[0].ignition is True
    assert 26 < stored[0].external_voltage < 28                       # 24 V truck system, engine running


async def test_udp_transport(gateway):
    (_, udp), persistence, _, _ = gateway
    stats = await run_device("127.0.0.1", udp, IMEI, "normal_day", steps=5, start=T0, transport="udp", batch=1)
    assert stats.records_acked == 5 and len(persistence.rows) == 5


async def test_gps_jump_and_invalid_fix_are_flagged(gateway):
    (tcp, _), persistence, _, _ = gateway
    await run_device("127.0.0.1", tcp, IMEI, "gps_jump", steps=20, start=T0, batch=1)
    assert sum(r.is_outlier for r in persistence.rows.values()) == 1
    persistence.rows.clear()
    await run_device("127.0.0.1", tcp, IMEI, "invalid_fix", steps=20, start=T0.replace(hour=9))
    assert sum(not r.gps_valid for r in persistence.rows.values()) == 4


async def test_coverage_gap_uploads_the_backlog(gateway):
    (tcp, _), persistence, _, pipeline = gateway
    stats = await run_device("127.0.0.1", tcp, IMEI, "coverage_gap_backlog", steps=60, start=T0, gap_minutes=5,
                             batch=10)
    assert stats.max_backlog >= 30 and stats.records_acked == 60 and len(persistence.rows) == 60


async def test_duplicate_resend_is_stored_once(gateway):
    (tcp, _), persistence, _, pipeline = gateway
    stats = await run_device("127.0.0.1", tcp, IMEI, "duplicate_resend", steps=10, start=T0, batch=1)
    assert stats.resends == 10 and len(persistence.rows) == 10 and pipeline.stats.duplicates == 10


async def test_unknown_imei_is_rejected_and_reported(gateway):
    (tcp, _), persistence, inbox, _ = gateway
    stats = await run_device("127.0.0.1", tcp, IMEI, "unknown_imei", steps=5, start=T0)
    assert stats.rejected and stats.imei != IMEI and not persistence.rows
    assert inbox.attempts[0].identifier == stats.imei


async def test_malformed_packet_drops_the_connection_and_the_tracker_recovers(gateway):
    (tcp, _), persistence, _, pipeline = gateway
    stats = await run_device("127.0.0.1", tcp, IMEI, "malformed_packet", steps=12, start=T0, batch=1)
    assert stats.malformed_closed and stats.records_acked == 12 and len(persistence.rows) == 12
    assert pipeline.stats.protocol_errors.get("bad_preamble") == 1


async def test_go_offline_stops_reporting(gateway):
    (tcp, _), persistence, _, _ = gateway
    stats = await run_device("127.0.0.1", tcp, IMEI, "go_offline", steps=60, start=T0)
    assert stats.records_generated == 18 and len(persistence.rows) == 18


@pytest.mark.parametrize("scenario", ["overspeed", "geofence_cross", "long_idle"])
async def test_remaining_scenarios_run_cleanly(gateway, scenario):
    (tcp, _), persistence, _, _ = gateway
    stats = await run_device("127.0.0.1", tcp, IMEI, scenario, steps=30, start=T0)
    assert stats.records_acked == 30 and not stats.rejected


def test_scenario_list_matches_spec():
    assert set(SCENARIOS) == {"normal_day", "overspeed", "geofence_cross", "long_idle", "go_offline",
                              "coverage_gap_backlog", "gps_jump", "invalid_fix", "duplicate_resend", "unknown_imei",
                              "malformed_packet"}


def test_a_full_day_loops_with_trips_and_parking():
    """24 h at 10 s: back-and-forth trips with engine-off parking; positions stay on the route, no teleports."""
    sim = VehicleSim(Route.named("port_airport"), T0, 10.0, Behaviour())
    recs = [sim.tick() for _ in range(8640)]
    assert not sim.done
    lats = [r.latitude for r in recs]
    assert -6.88 < min(lats) and max(lats) < -6.83                       # stayed on the Port-Airport road
    parked = sum(1 for r in recs if r.io[239][1] == 0)
    assert parked * 10 > 3 * 3600                                         # several hours parked across the day
    trips = sum(1 for a, b in zip(recs, recs[1:], strict=False) if a.speed_kmh == 0 and b.speed_kmh > 0)
    assert trips >= 10
    pairs = zip(recs, recs[1:], strict=False)
    jumps = [distance_km((a.latitude, a.longitude), (b.latitude, b.longitude)) for a, b in pairs]
    assert max(jumps) < 0.25                                              # never more than 90 km/h per 10 s


async def test_answers_codec12_commands_while_connected():
    """The simulator replies to commands the way an FMC920 would (Codec 12 type 0x06), CRC included."""
    import asyncio

    from gpsgw.protocols.teltonika.codec12 import decode_response, encode_command
    from gpsgw.protocols.teltonika.crc import crc16_ibm

    from gpssim.client import TcpClient

    got: list[bytes] = []

    async def server(reader, writer):  # type: ignore[no-untyped-def]
        assert len(await reader.readexactly(17)) == 17        # IMEI handshake
        writer.write(b"\x01")
        writer.write(encode_command("getver"))
        await writer.drain()
        head = await reader.readexactly(8)
        got.append(head + await reader.readexactly(int.from_bytes(head[4:], "big") + 4))
        writer.close()

    srv = await asyncio.start_server(server, "127.0.0.1", 0)
    client = TcpClient("127.0.0.1", srv.sockets[0].getsockname()[1], IMEI)
    await client.connect()
    await client.idle(1.0)
    await client.close()
    srv.close()
    packet = got[0]
    size = int.from_bytes(packet[4:8], "big")
    data, crc = packet[8:8 + size], int.from_bytes(packet[8 + size:], "big")
    assert packet[:4] == b"\x00\x00\x00\x00" and crc == crc16_ibm(data)
    resp = decode_response(data)                                   # the gateway's own decoder
    assert client.commands_received == ["getver"]
    assert resp.text.startswith("Ver:03.28") and IMEI in resp.text


async def test_gt06_and_http_clients_against_the_real_listeners():
    """The simulator's GT06 and HTTP clients drive the gateway's listeners with the same scenarios."""
    from gpsgw.listeners.http import HttpLimits, HttpListener
    from gpsgw.listeners.tcp import TcpListener
    from gpsgw.normalization.io_mapper import IoMappingProfile
    from gpsgw.protocols.gt06.decoder import Gt06Decoder
    from gpsgw.services.memory import MemoryFormats, MemoryInbox, MemoryPersistence, MemoryPublisher, MemoryRegistry
    from gpsgw.services.pipeline import IngestPipeline
    from gpsgw.services.ports import DeviceInfo

    from gpssim.scenarios import run_device

    profile = IoMappingProfile.model_validate({"name": "g", "mappings": [{"io_id": 1, "kind": "field",
                                                                         "target": "ignition"}]})
    registry = MemoryRegistry([DeviceInfo(1, "868120145233604", "gt06", 1, 1, profile)])
    registry.tokens["tok"] = DeviceInfo(2, "HTTP-SIM", "generic_json", 1, 2, None, format_slug="fleet-json")
    storage = MemoryPersistence()
    pipeline = IngestPipeline(storage, MemoryPublisher())
    fleet_json = {"kind": "json", "identifier": "device_id", "latitude": "lat", "longitude": "lon",
                  "timestamp": "time", "speed": "speed_kmh", "fields": {"ignition": "ignition"}, "ack": "OK"}
    gt = TcpListener(Gt06Decoder(), registry, pipeline, MemoryInbox())
    http = HttpListener(registry, pipeline, MemoryInbox(), MemoryFormats({"fleet-json": fleet_json}),
                        limits=HttpLimits(rate=1000, burst=1000))
    gport, hport = await gt.start("127.0.0.1"), await http.start("127.0.0.1")
    try:
        g = await run_device("127.0.0.1", gport, "868120145233604", "normal_day", steps=60, protocol="gt06")
        h = await run_device("127.0.0.1", hport, "HTTP-SIM", "duplicate_resend", steps=30, protocol="http",
                             token="tok")
        bad = await run_device("127.0.0.1", hport, "HTTP-SIM", "normal_day", steps=5, protocol="http", token="no")
    finally:
        await gt.stop()
        await http.stop()
    assert (g.records_acked, h.records_acked, bad.rejected) == (60, 30, True)
    protocols = sorted({r.protocol for r in storage.rows.values()})
    assert protocols == ["generic_json", "gt06"] and len(storage.rows) == 90
    assert any(r.ignition for r in storage.rows.values() if r.protocol == "gt06")


def test_load_imeis_are_valid_and_match_the_backend_command():
    import importlib.util
    from pathlib import Path

    from gpssim.load import load_imei

    path = Path(__file__).resolve().parents[2] / "backend/apps/devices/management/commands/create_load_devices.py"
    source = path.read_text()
    assert 'LOAD_PREFIX = "35999"' in source
    spec = importlib.util.spec_from_file_location("x", path)
    assert spec is not None
    imeis = [load_imei(i) for i in (0, 1, 1999)]
    assert all(len(i) == 15 for i in imeis)
    # Luhn check from first principles
    for imei in imeis:
        digits = [int(c) for c in imei]
        total = sum(d if i % 2 == 0 else (d * 2 - 9 if d * 2 > 9 else d * 2) for i, d in enumerate(digits))
        assert total % 10 == 0
