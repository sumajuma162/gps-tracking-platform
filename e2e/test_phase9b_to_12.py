"""Browser acceptance for Phases 9B-12 on the live stack: driver portal, settings, API keys used from outside,
maintenance, analytics, a generic HTTP tracker set up in the UI and posting over HTTP (Scenario 2 step 4), and a
GT06 tracker reaching the live map through the plugin (Scenario 3)."""
from __future__ import annotations

import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime

from playwright.sync_api import Page, expect

from conftest import BASE, sign_in

TRACKER_HOST = os.environ.get("E2E_TRACKER_HOST", "localhost")


def test_driver_lands_on_their_vehicle_only(page: Page) -> None:
    sign_in(page, "driver@demo.local")
    page.goto(f"{BASE}/app")
    expect(page.get_by_role("heading", name="My vehicle")).to_be_visible()
    expect(page.get_by_text("T 123 ABC").first).to_be_visible()
    nav = page.get_by_role("navigation").first
    expect(nav.get_by_role("link", name="Live map")).to_have_count(0)
    expect(nav.get_by_role("link", name="Vehicles")).to_have_count(0)


def test_settings_api_key_maintenance_and_analytics(page: Page) -> None:
    sign_in(page, "admin@demo.local")
    nav = page.get_by_role("navigation").first

    nav.get_by_role("link", name="Settings").click()
    field = page.get_by_label("Long stop alert (min)")
    field.fill("150")
    page.get_by_role("button", name="Save changes").click()
    expect(page.get_by_role("status").filter(has_text="Settings saved.")).to_be_visible()
    page.reload()
    expect(page.get_by_label("Long stop alert (min)")).to_have_value("150")
    page.get_by_label("Long stop alert (min)").fill("")          # back to the default for later runs
    page.get_by_role("button", name="Save changes").click()

    nav.get_by_role("link", name="Integrations").click()
    page.get_by_role("tab", name="API keys").click()
    page.get_by_role("button", name="Create API key").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Name").fill(f"e2e {int(time.time())}")
    dialog.get_by_label("vehicles.view").check()
    dialog.get_by_role("button", name="Create API key").click()
    key = dialog.get_by_test_id("one-time-secret").inner_text().strip()
    assert key.startswith("gpk_")
    req = urllib.request.Request(f"{BASE}/api/v1/vehicles/?search=T%20123", headers={"Authorization": f"Api-Key {key}"})
    with urllib.request.urlopen(req, timeout=10) as resp:                # an external system using the key
        regs = [v["registration_number"] for v in json.loads(resp.read())["results"]]
    assert regs == ["T 123 ABC"]
    try:
        urllib.request.urlopen(urllib.request.Request(f"{BASE}/api/v1/drivers/",
                                                      headers={"Authorization": f"Api-Key {key}"}), timeout=10)
        raise AssertionError("a key without drivers.view read drivers")
    except urllib.error.HTTPError as exc:
        assert exc.code == 403
    dialog.get_by_role("button", name="Close", exact=True).last.click()

    nav.get_by_role("link", name="Maintenance").click()
    page.get_by_role("button", name="Add", exact=True).click()
    form = page.get_by_role("dialog")
    form.get_by_label("Vehicles").select_option(label="T 456 XYZ")
    task = f"Oil change {int(time.time())}"
    form.get_by_label("Service").fill(task)
    form.get_by_label("Every (km)").fill("5000")
    form.get_by_role("button", name="Add").click()
    expect(page.get_by_role("dialog")).to_have_count(0)
    page.get_by_placeholder("Search").fill(task)
    expect(page.get_by_role("row").filter(has_text=task)).to_contain_text(re.compile("OK|Due soon|Overdue"))

    nav.get_by_role("link", name="Analytics").click()
    expect(page.get_by_role("region", name="Distance per day (km)")).to_be_visible()
    expect(page.get_by_role("region", name="Records received")).to_be_visible()


def test_generic_http_tracker_from_ui_to_live_map(page: Page) -> None:
    """Scenario 2 step 4: format test-parsed in the UI; token issued in the UI; a plain HTTP POST; live map."""
    sign_in(page, "superadmin@example.com")
    page.get_by_role("navigation").first.get_by_role("link", name="Tracker formats").click()
    page.get_by_role("listitem").filter(has_text="fleet-json").get_by_role("button", name="Edit and test").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Sample message").fill(
        '{"device_id": "COASTAL-HTTP-1", "time": "2026-10-04T08:00:00Z", "lat": -6.79, "lon": 39.21}')
    dialog.get_by_role("button", name="Test parse").click()
    expect(dialog.get_by_test_id("parse-result")).to_contain_text("COASTAL-HTTP-1")
    dialog.get_by_role("button", name="Cancel").click()
    page.get_by_role("button", name="Sign out").click()

    sign_in(page, "admin@coastal.local")
    page.goto(f"{BASE}/app/devices")
    page.get_by_placeholder("Search").fill("COASTAL-HTTP-1")
    page.get_by_role("row").filter(has_text="COASTAL-HTTP-1").get_by_role("button", name="HTTP token").click()
    page.get_by_role("dialog").get_by_role("button", name="Create new token").click()
    token = page.get_by_role("dialog").get_by_test_id("one-time-secret").inner_text().strip()
    page.keyboard.press("Escape")
    now = datetime.now(UTC).replace(microsecond=0)
    lat = -6.7700 - (int(time.time()) % 50) * 0.0001
    body = json.dumps({"device_id": "COASTAL-HTTP-1", "time": now.isoformat(), "lat": lat, "lon": 39.2500,
                       "speed_kmh": 22, "ignition": 1}).encode()
    req = urllib.request.Request(f"http://{TRACKER_HOST}:5055/fleet-json", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=10) as resp:                  # what curl does
        assert resp.status == 200 and resp.read() == b"OK"
    page.goto(f"{BASE}/app/live")
    entry = page.get_by_role("list", name="Vehicles").get_by_role("button", name=re.compile("T 888 CTL"))
    expect(entry).to_be_visible()
    entry.click()
    expect(page.get_by_test_id("panel-position")).to_have_text(f"{lat:.5f}, 39.25000")


def gt06_packet(protocol: int, content: bytes, serial: int) -> bytes:
    from gpsgw.protocols.gt06.decoder import encode_packet
    return encode_packet(protocol, content, serial)


def test_gt06_tracker_reaches_the_live_map(page: Page) -> None:
    """Scenario 3 in the browser: the seeded GT06 (Coastal Transport) logs in, reports, and appears live."""
    now = datetime.now(UTC).replace(microsecond=0)
    lat, lon = -6.8123, 39.2876
    flags = 90 | (1 << 12)                                  # GPS fixed, south latitude, east longitude
    content = (bytes([now.year - 2000, now.month, now.day, now.hour, now.minute, now.second, 0xC9])
               + round(abs(lat) * 1_800_000).to_bytes(4, "big") + round(lon * 1_800_000).to_bytes(4, "big")
               + bytes([18]) + flags.to_bytes(2, "big") + (640).to_bytes(2, "big") + bytes([4])
               + (0x1234).to_bytes(2, "big") + (0xABCDE).to_bytes(3, "big") + bytes([1, 0, 0]))
    with socket.create_connection((TRACKER_HOST, 5023), timeout=10) as s:
        s.sendall(gt06_packet(0x01, bytes.fromhex("0868120145233604"), 1))
        assert s.recv(10) == gt06_packet(0x01, b"", 1), "GT06 login not acknowledged"
        s.sendall(gt06_packet(0x22, content, 2) + gt06_packet(0x13, bytes([0b0100_0010, 4, 3, 0, 1]), 3))
        assert s.recv(10) == gt06_packet(0x13, b"", 3), "heartbeat not acknowledged"
    sign_in(page, "admin@coastal.local")
    page.goto(f"{BASE}/app/live")
    entry = page.get_by_role("list", name="Vehicles").get_by_role("button", name=re.compile("T 777 CTL"))
    expect(entry).to_be_visible()
    entry.click()
    expect(page.get_by_test_id("panel-position")).to_have_text(f"{lat:.5f}, {lon:.5f}")
