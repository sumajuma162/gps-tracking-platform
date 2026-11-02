"""Generic configurable protocol: UI-defined JSON-over-HTTP and delimited-text formats (spec A7, A16)."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from gpsgw.health import serve_health
from gpsgw.listeners.http import HttpLimits, HttpListener
from gpsgw.listeners.tcp import TcpLimits, TcpListener
from gpsgw.listeners.udp import UdpListener
from gpsgw.protocols.base import DeviceIdentity, ProtocolError, SessionContext, Transport
from gpsgw.protocols.generic.decoder import GenericTextDecoder
from gpsgw.protocols.generic.format import GenericFormat, ParseError, parse_record, parse_sample
from gpsgw.services.memory import MemoryFormats, MemoryInbox, MemoryPersistence, MemoryPublisher, MemoryRegistry
from gpsgw.services.pipeline import IngestPipeline
from gpsgw.services.ports import DeviceInfo

NOW = datetime.now(UTC).replace(microsecond=0)
JSON_FMT = {"kind": "json", "identifier": "device.id", "latitude": "pos.lat", "longitude": "pos.lon",
            "timestamp": "time", "time_format": "unix", "speed": "pos.speed", "speed_unit": "knots",
            "heading": "pos.course", "fields": {"ignition": "io.ign", "battery_voltage": "io.batt"},
            "sensors": {"fuel_l": "io.fuel"}, "auth": "token", "ack": "OK"}
TEXT_FMT = {"kind": "delimited", "identifier": 0, "timestamp": 1, "time_format": "%d%m%y%H%M%S", "latitude": 2,
            "longitude": 3, "speed": 4, "fields": {"ignition": 5}, "separator": ",", "terminator": "\n", "ack": "ACK"}


def msg(lat: float = -6.8, when: datetime = NOW, ident: str = "trk-1") -> dict:  # type: ignore[type-arg]
    return {"device": {"id": ident}, "time": int(when.timestamp()), "pos": {"lat": lat, "lon": 39.28, "speed": 10,
                                                                            "course": 370},
            "io": {"ign": "on", "batt": "12.6", "fuel": 41.5}}


def line(ident: str = "SN100", when: datetime = NOW, ign: str = "1") -> bytes:
    return f"{ident},{when:%d%m%y%H%M%S},-6.81,39.27,36,{ign}\n".encode()


def test_json_parse_units_fields_and_sensors():
    ident, raw = parse_record(GenericFormat.model_validate(JSON_FMT), msg(), NOW)
    assert ident == "trk-1" and raw.device_time == NOW
    assert raw.speed_kmh == pytest.approx(18.52) and raw.heading == 10.0          # knots -> km/h; 370° -> 10°
    assert raw.extra == {"fields": {"ignition": True, "battery_voltage": 12.6}, "sensors": {"fuel_l": 41.5}}


def test_delimited_parse_and_time_pattern():
    fmt = GenericFormat.model_validate(TEXT_FMT)
    ident, raw = parse_record(fmt, line().decode().strip().split(","), NOW)
    assert ident == "SN100" and raw.device_time == NOW and raw.extra["fields"]["ignition"] is True


@pytest.mark.parametrize("change,field", [
    (lambda m: m["pos"].pop("lat"), "latitude"),
    (lambda m: m["pos"].update(lat=95), "latitude"),
    (lambda m: m.update(time="yesterday"), "timestamp"),
    (lambda m: m["io"].update(ign="maybe"), "ignition"),
    (lambda m: m["device"].update(id="bad id!"), "identifier"),
    (lambda m: m["pos"].update(speed=-3), "speed"),
])
def test_json_errors_name_the_field(change, field):
    m = msg()
    change(m)
    with pytest.raises(ParseError) as exc:
        parse_record(GenericFormat.model_validate(JSON_FMT), m, NOW)
    assert exc.value.field == field and exc.value.code == "invalid_message"


def test_format_definition_is_validated():
    for bad in ({**JSON_FMT, "latitude": 3}, {**TEXT_FMT, "latitude": "lat"}, {**JSON_FMT, "fields": {"nope": "x"}},
                {**TEXT_FMT, "separator": ""}):
        result = parse_sample(bad, "{}")
        assert not result["ok"] and result["stage"] == "format"


def test_test_parse_shows_records_or_errors():
    ok = parse_sample(JSON_FMT, json.dumps([msg(), msg(lat=-6.9)]))
    assert ok["ok"] and len(ok["records"]) == 2 and ok["records"][0]["ignition"] is True and ok["ack"] == "OK"
    bad = parse_sample(JSON_FMT, "{not json")
    assert bad == {"ok": False, "stage": "sample", "errors": [{"field": "body", "message": "not valid JSON"}]}
    text = parse_sample(TEXT_FMT, line().decode() + "\n" + line(ign="0").decode())
    assert [r["ignition"] for r in text["records"]] == [True, False]


@settings(max_examples=1500, deadline=None)
@given(st.recursive(st.none() | st.booleans() | st.floats(allow_nan=True) | st.integers() | st.text(max_size=20),
                    lambda c: st.lists(c, max_size=4) | st.dictionaries(st.text(max_size=8), c, max_size=4),
                    max_leaves=20))
def test_fuzz_json_parser(data):
    fmt = GenericFormat.model_validate(JSON_FMT)
    try:
        parse_record(fmt, data if isinstance(data, dict) else {"x": data}, NOW)
    except ParseError:
        pass
    parse_sample(JSON_FMT, json.dumps(data, default=str))


@settings(max_examples=1500, deadline=None)
@given(st.binary(max_size=200))
def test_fuzz_text_decoder(data):
    dec = GenericTextDecoder().configure({"format": TEXT_FMT})
    ctx = SessionContext(Transport.TCP, "x:1", DeviceIdentity("SN100"))
    buf = bytearray(data)
    try:
        while dec.extract_frame(buf, ctx) is not None:
            pass
    except ProtocolError:
        pass


def device(ident: str, protocol: str, format_slug: str | None = None) -> DeviceInfo:
    return DeviceInfo(device_id=hash(ident) % 1000, identifier=ident, protocol=protocol, organization_id=1,
                      vehicle_id=None, profile=None, format_slug=format_slug)


async def test_delimited_over_tcp_and_udp():
    registry = MemoryRegistry([device("SN100", "generic_text")])
    storage, inbox = MemoryPersistence(), MemoryInbox()
    pipeline = IngestPipeline(storage, MemoryPublisher())
    dec = GenericTextDecoder().configure({"format": TEXT_FMT})
    tcp = TcpListener(dec, registry, pipeline, inbox, limits=TcpLimits(handshake_timeout=2, idle_timeout=2))
    udp = UdpListener(dec, registry, pipeline, inbox)
    tport, uport = await tcp.start("127.0.0.1"), await udp.start("127.0.0.1")
    try:
        r, w = await asyncio.open_connection("127.0.0.1", tport)
        w.write(line(when=NOW - timedelta(seconds=20)) + line(when=NOW - timedelta(seconds=10), ign="0"))
        assert await asyncio.wait_for(r.readexactly(8), 2) == b"ACK\nACK\n"
        w.write(line(ident="OTHER"))                                       # another identifier on the session
        assert await asyncio.wait_for(r.read(10), 2) == b""
        assert sorted(x.ignition for x in storage.rows.values()) == [False, True]
        loop = asyncio.get_running_loop()
        got: asyncio.Future[bytes] = loop.create_future()

        class Client(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr: object) -> None:
                got.set_result(data)
        transport, _ = await loop.create_datagram_endpoint(Client, remote_addr=("127.0.0.1", uport))
        transport.sendto(line(when=NOW))
        assert await asyncio.wait_for(got, 2) == b"ACK\n"
        transport.close()
        assert len(storage.rows) == 3
        r2, w2 = await asyncio.open_connection("127.0.0.1", tport)
        w2.write(line(ident="UNKNOWN1"))
        assert await asyncio.wait_for(r2.read(10), 2) == b""
        assert inbox.attempts[-1].identifier == "UNKNOWN1"
    finally:
        await tcp.stop()
        await udp.stop()


async def http(port: int, raw: bytes) -> tuple[int, bytes]:
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(raw)
    data = await asyncio.wait_for(r.read(), 3)
    w.close()
    head, _, body = data.partition(b"\r\n\r\n")
    return int(head.split()[1]), body


def post(path: str, body: dict | list | str, token: str | None = None, ctype: str = "application/json") -> bytes:  # type: ignore[type-arg]
    payload = body.encode() if isinstance(body, str) else json.dumps(body).encode()
    auth = f"Authorization: Bearer {token}\r\n" if token else ""
    return (f"POST {path} HTTP/1.1\r\nHost: x\r\nContent-Type: {ctype}\r\n{auth}Content-Length: {len(payload)}"
            f"\r\n\r\n").encode() + payload


@pytest.fixture
async def http_stack():
    registry = MemoryRegistry([device("trk-1", "generic_json", "fleetjson"), device("osm7", "generic_json", "osm")])
    registry.tokens["secret-token"] = device("trk-1", "generic_json", "fleetjson")
    storage, inbox = MemoryPersistence(), MemoryInbox()
    formats = MemoryFormats({"fleetjson": JSON_FMT, "osm": {
        "kind": "json", "identifier": "id", "latitude": "lat", "longitude": "lon", "timestamp": "timestamp",
        "time_format": "unix", "speed": "speed", "speed_unit": "knots", "auth": "identifier"},
        "csv": TEXT_FMT})
    listener = HttpListener(registry, IngestPipeline(storage, MemoryPublisher()), inbox, formats,
                            limits=HttpLimits(max_body=4096, rate=1000, burst=1000))
    port = await listener.start("127.0.0.1")
    yield {"port": port, "storage": storage, "inbox": inbox, "listener": listener}
    await listener.stop()


async def test_http_token_auth_ack_after_storage(http_stack):
    port, storage = http_stack["port"], http_stack["storage"]
    assert await http(port, post("/fleetjson", [msg(), msg(lat=-6.81, when=NOW - timedelta(seconds=5))],
                                 "secret-token")) == (200, b"OK")
    assert len(storage.rows) == 2 and {r.protocol for r in storage.rows.values()} == {"generic_json"}
    assert next(iter(storage.rows.values())).sensors == {"fuel_l": 41.5}
    assert (await http(port, post("/fleetjson", msg())))[0] == 401                         # no token
    assert (await http(port, post("/fleetjson", msg(), "wrong")))[0] == 401
    assert (await http(port, post("/fleetjson", msg(ident="trk-2"), "secret-token")))[0] == 401   # other device
    status, body = await http(port, post("/fleetjson", {"device": {"id": "trk-1"}}, "secret-token"))
    assert status == 400 and json.loads(body)["field"] == "latitude"
    assert (await http(port, post("/nope", msg(), "secret-token")))[0] == 404
    assert (await http(port, post("/csv", msg(), "secret-token")))[0] == 404              # text formats aren't HTTP
    assert (await http(port, post("/fleetjson", "x" * 5000, "secret-token")))[0] == 413
    storage.fail = True
    assert (await http(port, post("/fleetjson", msg(when=NOW - timedelta(seconds=30)), "secret-token")))[0] == 503
    assert len(storage.rows) == 2


async def test_http_identifier_allowlist_query_string_and_inbox(http_stack):
    port, storage, inbox = http_stack["port"], http_stack["storage"], http_stack["inbox"]
    query = f"/osm?id=osm7&lat=-6.8&lon=39.2&timestamp={int(NOW.timestamp())}&speed=5"
    status, body = await http(port, f"GET {query} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
    assert status == 200 and json.loads(body) == {"accepted": 1}
    assert next(iter(storage.rows.values())).speed_kmh == pytest.approx(9.26)
    assert (await http(port, f"GET {query.replace('osm7', 'osm8')} HTTP/1.1\r\n\r\n".encode()))[0] == 401
    assert inbox.attempts[-1].identifier == "osm8" and inbox.attempts[-1].transport == "http"
    # a device registered with another format can't post here
    assert (await http(port, post("/osm", {"id": "trk-1", "lat": 1, "lon": 1, "timestamp": 1}, None)))[0] == 403


async def test_http_rate_limit():
    listener = HttpListener(MemoryRegistry(), IngestPipeline(MemoryPersistence(), MemoryPublisher()), MemoryInbox(),
                            MemoryFormats(), limits=HttpLimits(rate=0.01, burst=2))
    port = await listener.start("127.0.0.1")
    try:
        codes = [(await http(port, b"GET /x HTTP/1.1\r\n\r\n"))[0] for _ in range(3)]
        assert codes == [404, 404, 429]
    finally:
        await listener.stop()


async def test_health_test_parse_endpoint():
    server = await serve_health(0, lambda: {}, host="127.0.0.1", parse_sample=parse_sample)
    port = server.sockets[0].getsockname()[1]
    try:
        status, body = await http(port, post("/test-parse", {"definition": TEXT_FMT, "sample": line().decode()}))
        assert status == 200 and json.loads(body)["records"][0]["identifier"] == "SN100"
        assert (await http(port, post("/test-parse", {"sample": "x"})))[0] == 400
        assert (await http(port, b"GET /health HTTP/1.1\r\n\r\n"))[0] == 200
    finally:
        server.close()


async def test_manual_position_endpoint_requires_internal_token():
    async def accept_position(payload):  # type: ignore[no-untyped-def]
        return {"accepted_records": 1, "latitude": payload["latitude"]}

    server = await serve_health(0, lambda: {}, host="127.0.0.1", manual_position=accept_position,
                                manual_token="internal-secret")  # noqa: S106 - test-only token
    port = server.sockets[0].getsockname()[1]
    try:
        assert (await http(port, post("/manual-position", {"latitude": -6.8}, "wrong")))[0] == 401
        status, body = await http(port, post("/manual-position", {"latitude": -6.8}, "internal-secret"))
        assert status == 200 and json.loads(body) == {"accepted_records": 1, "latitude": -6.8}
    finally:
        server.close()
