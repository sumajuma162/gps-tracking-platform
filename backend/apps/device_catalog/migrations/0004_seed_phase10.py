"""Phase 10 seed: the GT06 IO mapping profile, the generic text protocol, two example generic formats, and the
GT06 (5023/tcp) and generic HTTP (5055) listener ports switched on now that the gateway has their decoders.
Idempotent, so the test suite can re-run it after a flush."""
import json
from pathlib import Path

from django.db import migrations

FLEET_JSON = {
    "kind": "json", "identifier": "device_id", "latitude": "lat", "longitude": "lon", "timestamp": "time",
    "time_format": "iso8601", "speed": "speed_kmh", "speed_unit": "kmh", "heading": "heading", "altitude": "alt",
    "satellites": "sats", "fields": {"ignition": "ignition", "battery_voltage": "battery"}, "sensors": {},
    "auth": "token", "ack": "OK", "records_path": "",
}
CSV_TEXT = {
    "kind": "delimited", "identifier": 0, "timestamp": 1, "time_format": "unix", "latitude": 2, "longitude": 3,
    "speed": 4, "speed_unit": "kmh", "heading": 5, "fields": {"ignition": 6}, "separator": ",", "terminator": "\n",
    "ack": "OK",
}
HTTP_SETUP = """## Connect an HTTP tracker

Send each position to **http://{server_host}:{port}/fleet-json** as JSON, for example:

```
{"device_id": "{identifier}", "time": "2026-10-03T08:30:00Z", "lat": -6.8161, "lon": 39.2803, "speed_kmh": 42}
```

Authenticate with the tracker's token: `Authorization: Bearer <token>` (create it on the tracker's page).
The reply `OK` means the position was stored.
"""


def seed(apps, schema_editor):  # type: ignore[no-untyped-def]
    Protocol = apps.get_model("device_catalog", "DeviceProtocol")
    Profile = apps.get_model("device_catalog", "IoMappingProfile")
    Version = apps.get_model("device_catalog", "IoMappingProfileVersion")
    Model = apps.get_model("device_catalog", "DeviceModel")
    Port = apps.get_model("device_catalog", "ListenerPort")
    Format = apps.get_model("device_catalog", "GenericFormat")

    gt06 = Protocol.objects.filter(name="gt06").first()
    if gt06 is not None and not Profile.objects.filter(slug="gt06-generic").exists():
        data = json.loads((Path(__file__).resolve().parents[1] / "seed" / "gt06_generic.json").read_text())
        profile = Profile.objects.create(name="GT06 (generic)", slug="gt06-generic", protocol=gt06,
                                         description=data["_note"], verified=False)
        v1 = Version.objects.create(profile=profile, version=1, mappings=data["mappings"], notes="Seeded default")
        profile.current_version = v1
        profile.save(update_fields=["current_version"])
        Model.objects.filter(slug="gt06-generic").update(io_profile=profile, time_offset_minutes=0)
    Protocol.objects.get_or_create(name="generic_text", defaults={
        "display_name": "Generic delimited text", "transports": ["tcp", "udp"], "implemented": False})
    fleet, _ = Format.objects.get_or_create(slug="fleet-json", defaults={
        "name": "Fleet JSON (example)", "kind": "json", "definition": FLEET_JSON,
        "notes": "Example JSON-over-HTTP format. Clone it for your tracker's field names."})
    Format.objects.get_or_create(slug="csv-basic", defaults={
        "name": "CSV line (example)", "kind": "delimited", "definition": CSV_TEXT,
        "notes": "id,unix time,lat,lon,speed km/h,heading,ignition. Attach to a listener port in the reserved range."})
    Model.objects.filter(slug="generic-http-json").update(generic_format=fleet, setup_instructions=HTTP_SETUP)
    Port.objects.filter(port__in=(5023, 5055)).update(is_enabled=True)


class Migration(migrations.Migration):
    dependencies = [("device_catalog", "0003_genericformat_devicemodel_generic_format_and_more")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
