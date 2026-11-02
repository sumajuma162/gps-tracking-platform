"""Seed the device catalog (spec A7): protocols, manufacturers, models, the FMC920 IO profile, listener ports
and read-only FMC920 command templates. Parameter details must be verified against Teltonika docs."""
import json
from pathlib import Path

from django.db import migrations

FMC920_SETUP = """## Connect a Teltonika {model}

1. Insert a SIM card with an active data plan. Disable its PIN or note it.
2. Connect the tracker to a Windows PC by USB and open **Teltonika Configurator**.
3. In **GPRS**, set the APN to `{apn}` (username and password only if your operator requires them).
4. In **Server settings**, set Domain `{server_host}`, Port `{port}`, Protocol **TCP**.
5. In **System > Data protocol**, choose **Codec 8 Extended** (or Codec 8).
6. Save to device, install it in the vehicle and switch the ignition on.

Watch the connection test on this page. Verify every parameter name against Teltonika's documentation for
your firmware version.
"""

GT06_SETUP = """## Connect a GT06-compatible tracker

GT06 clones are usually configured by SMS. Typical commands (check your device's manual):

- `APN,{apn}#`
- `SERVER,0,{server_host},{port},0#`

Some clones send local time instead of UTC: set the model's time offset if positions look shifted.
"""


def seed(apps, schema_editor):  # type: ignore[no-untyped-def]
    Protocol = apps.get_model("device_catalog", "DeviceProtocol")
    Manufacturer = apps.get_model("device_catalog", "DeviceManufacturer")
    Profile = apps.get_model("device_catalog", "IoMappingProfile")
    Version = apps.get_model("device_catalog", "IoMappingProfileVersion")
    Model = apps.get_model("device_catalog", "DeviceModel")
    Port = apps.get_model("device_catalog", "ListenerPort")
    Command = apps.get_model("device_catalog", "CommandTemplate")

    teltonika = Protocol.objects.create(name="teltonika", display_name="Teltonika Codec 8 / 8E / 12",
                                        transports=["tcp", "udp"], supports_commands=True, implemented=True)
    gt06 = Protocol.objects.create(name="gt06", display_name="GT06", transports=["tcp"], implemented=False)
    generic = Protocol.objects.create(name="generic_json", display_name="Generic JSON over HTTP",
                                      transports=["http"], implemented=False)

    m_tel = Manufacturer.objects.create(name="Teltonika", slug="teltonika", website="https://teltonika-gps.com")
    m_concox = Manufacturer.objects.create(name="Concox / GT06 compatible", slug="gt06-compatible")
    m_generic = Manufacturer.objects.create(name="Generic", slug="generic")

    data = json.loads((Path(__file__).resolve().parents[1] / "seed" / "teltonika_fmc920.json").read_text())
    profile = Profile.objects.create(name="Teltonika FMC920", slug="teltonika-fmc920", protocol=teltonika,
                                     description=data.get("_note", ""), verified=False)
    v1 = Version.objects.create(profile=profile, version=1, mappings=data["mappings"], notes="Seeded default")
    profile.current_version = v1
    profile.save(update_fields=["current_version"])

    teltonika_caps = {c: True for c in ["ignition", "odometer", "external_voltage", "battery", "gsm_signal",
                                        "commands", "harsh_driving", "crash", "jamming"]}
    fmc920 = Model.objects.create(manufacturer=m_tel, name="FMC920", slug="teltonika-fmc920", protocol=teltonika,
                                  transports=["tcp", "udp"], default_port=5027, identifier_type="imei",
                                  capabilities=teltonika_caps, io_profile=profile, trip_detection_mode="ignition",
                                  setup_instructions=FMC920_SETUP.replace("{model}", "FMC920"), verified=False)
    for name in ("FMB920", "FMC130"):
        # Cloned from the FMC920; unverified until tested on real hardware (spec A7 seed models).
        Model.objects.create(manufacturer=m_tel, name=name, slug=f"teltonika-{name.lower()}", protocol=teltonika,
                             transports=["tcp", "udp"], default_port=5027, identifier_type="imei",
                             capabilities=teltonika_caps, io_profile=profile, trip_detection_mode="ignition",
                             setup_instructions=FMC920_SETUP.replace("{model}", name), verified=False)
    Model.objects.create(manufacturer=m_concox, name="GT06 (generic)", slug="gt06-generic", protocol=gt06,
                         transports=["tcp"], default_port=5023, identifier_type="imei",
                         capabilities={"ignition": True, "gsm_signal": True}, trip_detection_mode="ignition",
                         setup_instructions=GT06_SETUP, verified=False)
    Model.objects.create(manufacturer=m_generic, name="HTTP JSON tracker", slug="generic-http-json",
                         protocol=generic, transports=["http"], default_port=5055, identifier_type="custom",
                         identifier_pattern=r"^[A-Za-z0-9_-]{4,40}$", capabilities={},
                         trip_detection_mode="speed", verified=False)

    Port.objects.create(port=5027, transport="tcp", protocol=teltonika, description="Teltonika devices (TCP)")
    Port.objects.create(port=5027, transport="udp", protocol=teltonika, description="Teltonika devices (UDP)")
    Port.objects.create(port=5023, transport="tcp", protocol=gt06, description="GT06-compatible devices",
                        is_enabled=False)
    Port.objects.create(port=5055, transport="http", protocol=generic, description="Generic HTTP trackers",
                        is_enabled=False)

    for name, text, desc in [
        ("Device information", "getinfo", "Firmware start time, GSM and GPS status"),
        ("Firmware version", "getver", "Firmware version and IMEI"),
        ("Current GPS data", "getgps", "Latest GPS fix as reported by the device"),
        ("Status", "getstatus", "Modem and connection status"),
    ]:
        Command.objects.create(device_model=fmc920, name=name, command_text=text, description=desc,
                               is_verified=False, is_dangerous=False)


class Migration(migrations.Migration):
    dependencies = [("device_catalog", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
