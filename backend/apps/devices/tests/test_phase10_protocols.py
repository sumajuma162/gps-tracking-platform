"""Phase 10 acceptance against PostgreSQL + Redis with the real gateway and processor:

- Scenario 2 step 4: a JSON-over-HTTP format is defined in the UI (API), checked with test-parse, a device of that
  format is registered, and a plain HTTP POST (what ``curl`` sends) puts the vehicle on the live map.
- Scenario 3: a GT06 tracker registered through the API logs in, sends locations and heartbeats; its records flow
  through the same positions table, live state, trips, alerts and reports as Teltonika's.
- A delimited-text listener port bound to a UI-defined format accepts lines over TCP.
"""
from __future__ import annotations

import asyncio
import json
import urllib.request
from datetime import UTC, datetime, timedelta

import pytest
from django.conf import settings
from django.db import connection

from apps.alerts.models import Alert, AlertRule
from apps.analysis.models import Trip
from apps.device_catalog.models import DeviceModel, DeviceProtocol, GenericFormat, ListenerPort
from apps.devices.models import Device
from apps.fleet.models import Vehicle
from apps.tracking.models import DeviceState
from apps.tracking.tests.test_gateway_ingest import free_port, orm, redis_available, wait_until
from apps.tracking.tests.test_positions import ALL_APPS

gpsgw = pytest.importorskip("gpsgw", reason="install the gateway package: pip install -e ../gateway")
pytest.importorskip("gpsproc.app", reason="add ../processor to PYTHONPATH")
from gpsgw.app import Gateway  # noqa: E402
from gpsgw.config import GatewayConfig  # noqa: E402
from gpsgw.protocols.gt06.decoder import encode_packet  # noqa: E402
from gpsproc.app import Processor, ProcessorConfig  # noqa: E402

pytestmark = [pytest.mark.skipif("connection.vendor != 'postgresql'"),
              pytest.mark.django_db(transaction=True, available_apps=[
                  *ALL_APPS, "apps.analysis", "apps.geofences", "apps.alerts", "apps.integrations",
                  "apps.settings_app", "apps.reports"])]
GT06_IMEI = "868120145233604"
FORMAT = {"identifier": "unit", "latitude": "gps.lat", "longitude": "gps.lng", "timestamp": "ts",
          "time_format": "unix", "speed": "gps.kph", "fields": {"ignition": "acc"}, "auth": "token", "ack": "STORED"}


def gt06_location(when: datetime, lat: float, lon: float, speed: int, acc: bool, serial: int) -> bytes:
    flags = 90 | (1 << 12) | (1 << 10 if lat >= 0 else 0) | (1 << 11 if lon < 0 else 0)
    content = (bytes([when.year - 2000, when.month, when.day, when.hour, when.minute, when.second, 0xC9])
               + round(abs(lat) * 1_800_000).to_bytes(4, "big") + round(abs(lon) * 1_800_000).to_bytes(4, "big")
               + bytes([speed]) + flags.to_bytes(2, "big")
               + (640).to_bytes(2, "big") + bytes([4]) + (0x1234).to_bytes(2, "big") + (0xABCDE).to_bytes(3, "big")
               + bytes([1 if acc else 0, 0, 0]))
    return encode_packet(0x22, content, serial)


def gt06_login(imei: str) -> bytes:
    return encode_packet(0x01, bytes.fromhex(imei.rjust(16, "0")), 1)


def http_post(port: int, path: str, body: object, token: str) -> tuple[int, str]:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 - local test server
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def test_generic_http_gt06_and_text_through_the_real_pipeline(org_a, make_member, api, superadmin, isolated_redis_url):
    if not redis_available():
        pytest.skip("Redis is required for the positions stream")
    import redis
    rds = redis.Redis.from_url(settings.REDIS_URL)
    rds.delete("positions")
    root, admin = api(superadmin), api(make_member(org_a, "org_admin"))
    van = Vehicle.unscoped.create(organization=org_a, registration_number="T 610 HTP")
    truck = Vehicle.unscoped.create(organization=org_a, registration_number="T 620 GTS")

    # --- Scenario 2.4, in the UI: define the format, test-parse a sample, add a model and a device ----------------
    bad = root.post("/api/v1/device-catalog/generic-formats/",
                    {"name": "Bad", "slug": "bad", "kind": "json", "definition": {**FORMAT, "latitude": 3}},
                    format="json")
    assert bad.status_code == 400 and "definition" in bad.json()["error"]["details"]
    created = root.post("/api/v1/device-catalog/generic-formats/", {"name": "Acme JSON", "slug": "acme",
                                                                     "kind": "json", "definition": FORMAT},
                        format="json")
    assert created.status_code == 201, created.json()
    fmt_id = created.json()["id"]
    sample = {"unit": "ACME-77", "ts": 1759480000, "gps": {"lat": -6.79, "lng": 39.21, "kph": 33}, "acc": 1}
    parsed = root.post(f"/api/v1/device-catalog/generic-formats/{fmt_id}/test-parse/",
                       {"sample": json.dumps(sample)}, format="json").json()
    assert parsed["ok"] and parsed["records"][0]["identifier"] == "ACME-77" and parsed["records"][0]["ignition"] is True
    broken = root.post(f"/api/v1/device-catalog/generic-formats/{fmt_id}/test-parse/",
                       {"sample": json.dumps({"unit": "ACME-77"})}, format="json").json()
    assert not broken["ok"] and broken["errors"][0]["field"] == "latitude"
    assert admin.post("/api/v1/device-catalog/generic-formats/", {}, format="json").status_code == 403
    generic = DeviceModel.objects.get(slug="generic-http-json")
    model = root.post(f"/api/v1/device-catalog/models/{generic.pk}/clone/", {"name": "Acme HTTP", "slug": "acme-http"},
                      format="json").json()
    assert root.patch(f"/api/v1/device-catalog/models/{model['id']}/", {"generic_format": fmt_id},
                      format="json").status_code == 200
    http_dev = admin.post("/api/v1/devices/", {"name": "Acme van", "identifier": "ACME-77",
                                               "device_model": model["id"]}, format="json")
    assert http_dev.status_code == 201, http_dev.json()
    assert admin.post(f"/api/v1/devices/{http_dev.json()['id']}/assign/", {"vehicle_id": van.pk},
                      format="json").status_code == 200
    token = admin.post(f"/api/v1/devices/{http_dev.json()['id']}/rotate-token/").json()["token"]
    assert Device.unscoped.get(identifier="ACME-77").ingest_token_hash not in (None, token)   # only a hash stored

    # --- Scenario 3: a GT06 registered through the API, and a text port bound to a UI format -------------------
    gt06_model = DeviceModel.objects.get(slug="gt06-generic")
    gt = admin.post("/api/v1/devices/", {"name": "GT06 truck", "identifier": GT06_IMEI,
                                         "device_model": gt06_model.pk}, format="json")
    assert gt.status_code == 201, gt.json()
    assert admin.post(f"/api/v1/devices/{gt.json()['id']}/assign/", {"vehicle_id": truck.pk},
                      format="json").status_code == 200
    AlertRule.unscoped.create(organization=org_a, name="Speed", type="overspeed", recipient_roles=["org_admin"],
                              params={"limit_kmh": 80})
    gt_port, http_port, text_port = free_port(), free_port(), free_port()
    ListenerPort.objects.filter(port=5023).update(port=gt_port)
    ListenerPort.objects.filter(port=5055).update(port=http_port)
    text_proto = DeviceProtocol.objects.get(name="generic_text")
    port_resp = root.post("/api/v1/device-catalog/listener-ports/", {
        "port": text_port, "transport": "tcp", "protocol": text_proto.pk,
        "generic_format": GenericFormat.objects.get(slug="csv-basic").pk}, format="json")
    assert port_resp.status_code == 201, port_resp.json()
    text_model = root.post(f"/api/v1/device-catalog/models/{generic.pk}/clone/",
                           {"name": "CSV box", "slug": "csv-box"}, format="json").json()
    root.patch(f"/api/v1/device-catalog/models/{text_model['id']}/", {
        "protocol": text_proto.pk, "transports": ["tcp"], "generic_format": GenericFormat.objects.get(
            slug="csv-basic").pk}, format="json")
    assert admin.post("/api/v1/devices/", {"name": "CSV box", "identifier": "CSV-9", "device_model": text_model["id"]},
                      format="json").status_code == 201

    db = connection.settings_dict
    dsn = f"postgresql://{db['USER']}:{db['PASSWORD']}@{db['HOST'] or 'localhost'}:{db['PORT'] or 5432}/{db['NAME']}"
    cfg = GatewayConfig(teltonika_tcp_port=0, teltonika_udp_port=0, health_port=free_port(), storage="postgres",
                        allow_memory_storage=False, dev_devices=(), database_url=dsn, redis_url=settings.REDIS_URL,
                        listen_host="127.0.0.1", reconcile_interval=1.0)
    now = datetime.now(UTC).replace(microsecond=0)
    t0 = now - timedelta(hours=1)

    async def scenario() -> None:
        gw = Gateway(cfg)
        await gw.start()
        try:
            # HTTP: what `curl -H "Authorization: Bearer <token>" -d '{...}' http://host:5055/acme` does.
            body = [{**sample, "ts": int((now - timedelta(seconds=30 - i * 10)).timestamp()),
                     "gps": {"lat": -6.79 - i * 0.001, "lng": 39.21, "kph": 33}} for i in range(3)]
            assert await asyncio.to_thread(http_post, http_port, "/acme", body, token) == (200, "STORED")
            assert (await asyncio.to_thread(http_post, http_port, "/acme", body, "stolen"))[0] == 401
            # GT06: login, 1 h of driving (5 min above 80 km/h), ignition off, then a heartbeat.
            r, w = await asyncio.open_connection("127.0.0.1", gt_port)
            w.write(gt06_login(GT06_IMEI))
            assert await asyncio.wait_for(r.readexactly(10), 5) == encode_packet(0x01, b"", 1)
            for i in range(120):
                speed = 0 if i < 3 or i > 110 else 95 if 40 <= i < 70 else 50
                w.write(gt06_location(t0 + timedelta(seconds=30 * i), -6.80, 39.20 + i * 0.004, speed,
                                      acc=3 <= i <= 112, serial=2 + i))
            w.write(encode_packet(0x13, bytes([0, 4, 3, 0, 1]), 500))
            assert await asyncio.wait_for(r.readexactly(10), 10) == encode_packet(0x13, b"", 500)
            w.close()
            # Delimited text on the UI-bound port.
            r2, w2 = await asyncio.open_connection("127.0.0.1", text_port)
            w2.write(f"CSV-9,{int(now.timestamp()) - 5},-6.77,39.25,12,90,1\n".encode())
            assert await asyncio.wait_for(r2.readexactly(3), 5) == b"OK\n"
            w2.close()

            async def stored() -> bool:
                return await orm(lambda: DeviceState.unscoped.filter(device__identifier="CSV-9").exists())
            await wait_until(stored, 5)
        finally:
            await gw.stop()
        # The processor consumes the same stream as for Teltonika.
        proc = Processor(ProcessorConfig(database_url=dsn, redis_url=settings.REDIS_URL, recompute_debounce_s=0))
        import asyncpg

        class NullLayer:
            async def group_send(self, group: str, message: dict) -> None:  # type: ignore[type-arg]
                return None
        proc.pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
        proc.layer = NullLayer()  # type: ignore[assignment]
        try:
            entries = await proc_entries()
            await proc.handle(entries)
        finally:
            await proc.pool.close()

    async def proc_entries() -> list:  # type: ignore[type-arg]
        import redis.asyncio as aioredis
        client = aioredis.Redis.from_url(settings.REDIS_URL)
        try:
            return await client.xrange("positions")
        finally:
            await client.aclose()

    asyncio.run(scenario())

    with connection.cursor() as cur:
        cur.execute("SELECT protocol, count(*), bool_or(ignition) FROM positions GROUP BY protocol ORDER BY protocol")
        by_protocol = {p: (n, ign) for p, n, ign in cur.fetchall()}
    assert by_protocol == {"generic_json": (3, True), "generic_text": (1, True), "gt06": (120, True)}
    van_state = DeviceState.unscoped.get(device__identifier="ACME-77")
    assert van_state.vehicle_id == van.pk and van_state.latitude == pytest.approx(-6.792)
    live = admin.get("/api/v1/tracking/live/").json()
    on_map = {row["vehicle"]["registration_number"]: row for row in live if row["vehicle"]}
    assert on_map["T 610 HTP"]["latitude"] == pytest.approx(-6.792)                       # on the live map
    assert on_map["T 620 GTS"]["latitude"] == pytest.approx(-6.80)
    trip = Trip.unscoped.get(vehicle=truck)
    assert trip.max_speed_kmh == 95 and trip.distance_km > 20
    assert Alert.unscoped.filter(vehicle=truck, type="overspeed").exists()
    report = admin.get(f"/api/v1/reports/trips/?start={(t0 - timedelta(hours=1)).isoformat()}"
                       f"&end={(now + timedelta(hours=1)).isoformat()}".replace("+", "%2B")).json()
    assert any(row["vehicle"] == "T 620 GTS" for row in report["rows"])
