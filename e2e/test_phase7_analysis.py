"""Phase 7 in the browser: draw a geofence, drive two hours through the real gateway, and see the processor's
trips and geofence events in the UI (spec A10, A12)."""
from __future__ import annotations

import re
import uuid

from conftest import BASE, add_tracker_via_wizard, shot, sign_in
from playwright.sync_api import Page, expect
from test_phase4_onboarding import new_imei
from tracker_client import run_simulator


def test_geofence_trips_and_events_from_a_simulated_drive(page: Page) -> None:
    suffix = uuid.uuid4().hex[:6].upper()
    fence, reg, imei = f"Ubungo {suffix}", f"A {suffix}", new_imei()
    sign_in(page, "admin@demo.local")

    # 1. Draw a circle around the map's default centre (Ubungo junction on Morogoro Road).
    page.goto(f"{BASE}/app/geofences")
    page.get_by_role("button", name="New geofence").click()
    editor = page.get_by_test_id("geofence-editor")
    editor.get_by_label("Name *").fill(fence)
    editor.get_by_label("Circle").check()
    box = page.get_by_test_id("geofence-map").bounding_box()
    assert box is not None
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    expect(editor.get_by_role("status")).to_contain_text("Centre placed")
    editor.get_by_label("Radius (m)").fill("1500")
    editor.get_by_role("button", name="Save changes").click()
    expect(page.locator(f'[data-geofence="{fence}"]')).to_contain_text("1500 m")
    shot(page, "p7-01-geofences")

    # 2. A fresh vehicle with a tracker, then two hours of driving along Morogoro Road through the gateway.
    page.get_by_role("navigation").first.get_by_role("link", name="Vehicles").click()
    page.get_by_role("button", name="Add", exact=True).click()
    page.get_by_role("dialog").get_by_label("Registration number *").fill(reg)
    page.get_by_role("dialog").get_by_role("button", name="Add").click()
    expect(page.get_by_role("dialog")).to_have_count(0)
    add_tracker_via_wizard(page, "Teltonika FMC920", imei, f"Analysis {suffix}", vehicle=reg)
    # normal_day drives Morogoro Road, which passes the Ubungo junction inside the circle
    (drive,) = run_simulator("--imei", imei, "--steps", "720", "--scenario", "normal_day")
    assert drive["records_acked"] == 720 and not drive["rejected"]

    # 3. The processor turns it into trips, visible on the Trips page.
    page.goto(f"{BASE}/app/trips")
    page.get_by_label("Vehicle").select_option(label=reg)
    rows = page.get_by_role("row").filter(has_text=reg)
    for _attempt in range(10):                      # the processor works asynchronously; give it a few seconds
        try:
            expect(rows.first).to_be_visible(timeout=2000)
            break
        except AssertionError:
            page.reload()
            page.get_by_label("Vehicle").select_option(label=reg)
    expect(rows.first).to_be_visible()
    expect(rows.first).to_contain_text(re.compile(r"\d+\.\d km"))
    shot(page, "p7-02-trips")

    # 4. The trip's route shows the geofence crossing on the map.
    rows.first.get_by_role("link", name="View route").click()
    expect(page.get_by_test_id("history-summary")).to_be_visible()
    page.get_by_role("button", name="Last 24 hours").click()
    expect(page.locator('svg[data-pin="event"]').first).to_be_attached(timeout=5000)
    shot(page, "p7-03-history-geofence")
