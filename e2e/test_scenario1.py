"""Spec A18 Scenario 1: core tracking flow (Teltonika), end to end in the browser on the live stack.

Super Admin creates an organization and its admin; the admin onboards an FMC920 and a vehicle; the simulator
drives through the real gateway; live map, route history, trips, stops, overspeed and geofence alerts, a trip
report exported to CSV/Excel/PDF and audited; and another organization sees none of it.
"""
from __future__ import annotations

import csv
import io
import re
import uuid

from conftest import BASE, add_tracker_via_wizard, invitation_link, shot, sign_in
from playwright.sync_api import Page, expect
from test_phase4_onboarding import new_imei
from tracker_client import Tracker, run_simulator

PASSWORD = "Scenario-One-2026!"


def test_scenario_1_core_tracking_flow(page: Page) -> None:
    sfx = uuid.uuid4().hex[:6]
    org_name, admin_email = f"Scenario Freight {sfx}", f"admin-{sfx}@scenario.test"
    reg, imei, tracker_name = f"T {sfx.upper()[:3]} SCN", new_imei(), f"FMC920 {sfx}"

    # 1. Super Admin creates the organization and its admin; the admin sets a password from the invitation.
    sign_in(page, "superadmin@example.com")
    page.get_by_role("navigation").first.get_by_role("link", name="Organizations").click()
    page.get_by_role("button", name="Add organization").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Name", exact=True).fill(org_name)
    dialog.get_by_label("First administrator's email").fill(admin_email)
    dialog.get_by_role("button", name="Add organization").click()
    expect(page.get_by_role("dialog")).to_have_count(0)
    page.get_by_role("button", name="Sign out").click()
    page.goto(invitation_link(admin_email))
    page.get_by_label("New password", exact=True).fill(PASSWORD)
    page.get_by_label("Confirm new password").fill(PASSWORD)
    with page.expect_response(lambda r: "/auth/password/reset/" in r.url) as reset:
        page.get_by_role("button", name="Set password").click()
    assert reset.value.status < 300, f"setting the password failed: {reset.value.status}"
    page.goto(f"{BASE}/login")
    page.get_by_label("Email").fill(admin_email)
    page.get_by_label("Password").fill(PASSWORD)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(re.compile(r"/app"))
    expect(page.get_by_text(org_name).first).to_be_visible()

    # Alerting set-up: a geofence around Ubungo junction and two alert rules.
    page.goto(f"{BASE}/app/geofences")
    page.get_by_role("button", name="New geofence").click()
    editor = page.get_by_test_id("geofence-editor")
    editor.get_by_label("Name *").fill("Ubungo junction")
    editor.get_by_label("Circle").check()
    box = page.get_by_test_id("geofence-map").bounding_box()
    assert box is not None
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    editor.get_by_label("Radius (m)").fill("1500")
    editor.get_by_role("button", name="Save changes").click()
    expect(page.locator('[data-geofence="Ubungo junction"]')).to_be_visible()
    for name, kind, threshold in (("Speeding", "overspeed", "90"), ("Arrived at Ubungo", "geofence_enter", "")):
        page.goto(f"{BASE}/app/alert-rules")
        page.get_by_role("button", name="Add", exact=True).click()
        d = page.get_by_role("dialog")
        d.get_by_label("Name *").fill(name)
        d.get_by_label("Alert type *").select_option(kind)
        if threshold:
            d.get_by_label("Threshold").fill(threshold)
        d.get_by_label(re.compile("Notify roles")).select_option("org_admin")
        d.get_by_role("button", name="Add").click()
        expect(page.get_by_role("dialog")).to_have_count(0)

    # 2. A vehicle, and the FMC920 added through the wizard and assigned to it.
    page.goto(f"{BASE}/app/vehicles")
    page.get_by_role("button", name="Add", exact=True).click()
    page.get_by_role("dialog").get_by_label("Registration number *").fill(reg)
    page.get_by_role("dialog").get_by_role("button", name="Add").click()
    expect(page.get_by_role("dialog")).to_have_count(0)
    add_tracker_via_wizard(page, "Teltonika FMC920", imei, tracker_name, vehicle=reg)

    # 3. The simulator drives two hours (speeding on the first leg, through Ubungo) via the public port.
    (drive,) = run_simulator("--imei", imei, "--steps", "720", "--scenario", "overspeed")
    assert drive["records_acked"] == 720 and not drive["rejected"]

    # 4. Connection test: every stage done. Live map: the marker moves over WebSocket without a reload.
    page.goto(f"{BASE}/app/devices")
    page.get_by_placeholder("Search").fill(imei)
    page.get_by_role("row").filter(has_text=imei).get_by_role("button", name="Connection test").click()
    stages = page.get_by_test_id("diagnostics").locator("[data-stage]")
    expect(stages.first).to_be_visible()
    for i in range(stages.count()):
        expect(stages.nth(i)).to_have_attribute("data-done", "true")
    page.keyboard.press("Escape")
    page.goto(f"{BASE}/app/live")
    entry = page.get_by_role("list", name="Vehicles").get_by_role("button", name=re.compile(reg))
    expect(entry).to_be_visible()
    entry.click()
    tracker = Tracker(imei)
    try:
        assert tracker.accepted
        here = page.get_by_test_id("panel-position").inner_text()     # continue from where the drive ended
        lat0, lon0 = (float(x) for x in here.split(","))              # (a jump elsewhere would be an outlier)
        for i in range(1, 3):
            lat = lat0 + i * 0.0003                                   # ~33 m steps
            assert tracker.send(latitude=lat, longitude=lon0, speed=35) == 1
            page.wait_for_function("e => document.querySelector('[data-testid=panel-position]')?.textContent === e",
                                   arg=f"{lat:.5f}, {lon0:.5f}", timeout=5000)
    finally:
        tracker.close()
    shot(page, "s1-04-live")

    # 5. Route history shows the path; playback runs at every speed.
    page.goto(f"{BASE}/app/history")
    page.get_by_label("Vehicle").select_option(label=reg)
    page.get_by_role("button", name="Last 24 hours").click()
    page.wait_for_function("() => Number(document.querySelector('[data-testid=history-map]')?.dataset.routePoints || 0) > 20")
    cursor = page.get_by_test_id("playback-cursor")
    speeds = page.get_by_label("Speed", exact=True).locator("option").all_inner_texts()
    assert {"0.5×", "1×", "2×", "4×", "8×", "16×"} <= set(speeds)
    for option in page.get_by_label("Speed", exact=True).locator("option").evaluate_all("os => os.map(o => o.value)"):
        page.get_by_role("button", name="Restart").click()
        page.get_by_label("Speed", exact=True).select_option(option)
        before = page.get_by_test_id("playback-time").get_attribute("data-time")
        page.wait_for_function("t => document.querySelector('[data-testid=playback-time]').dataset.time !== t",
                               arg=before, timeout=8000)            # the exact playback clock advances
        page.get_by_role("button", name="Pause").click()
        assert cursor.get_attribute("data-lat")
    history_url = page.url                       # contains ?vehicle=<id>: used for the isolation check
    shot(page, "s1-05-history")

    # 6. Trips and stops detected; overspeed and geofence alerts on the dashboard, acknowledged.
    page.goto(f"{BASE}/app/trips")
    page.get_by_label("Vehicle").select_option(label=reg)
    expect(page.get_by_role("row").filter(has_text=reg).first).to_be_visible(timeout=15000)
    page.goto(f"{BASE}/app")
    dash = page.get_by_test_id("dashboard-alerts")
    expect(dash).to_contain_text(re.compile(rf"{reg} exceeded 90 km/h"), timeout=15000)
    expect(dash).to_contain_text(f"{reg} entered Ubungo junction")
    page.goto(f"{BASE}/app/alerts")
    speeding = page.locator('li[data-alert="overspeed"]').filter(has_text=reg).first
    speeding.get_by_role("button", name="Acknowledge").click()
    page.get_by_role("dialog").get_by_role("button", name="Acknowledge").click()
    expect(speeding).to_contain_text("Acknowledged")
    page.goto(f"{BASE}/app/reports")
    page.get_by_role("button", name="Stops").click()
    page.get_by_role("button", name="Run report").click()
    expect(page.get_by_test_id("report-table")).to_contain_text(reg)

    # 7. Trip report, exported to CSV, Excel and PDF, and the exports are in the audit log.
    page.get_by_role("button", name="Trips", exact=True).click()
    page.get_by_role("button", name="Run report").click()
    expect(page.get_by_test_id("report-table")).to_contain_text(reg)
    for label, magic in (("CSV", b"\xef\xbb\xbfVehicle"), ("Excel", b"PK"), ("PDF", b"%PDF-")):
        with page.expect_download() as info:
            page.get_by_role("button", name=label, exact=True).click()
        data = open(info.value.path(), "rb").read()
        assert data.startswith(magic), label
        if label == "CSV":
            rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
            assert any(reg in r for r in rows[1:])
    page.goto(f"{BASE}/app/audit")
    expect(page.get_by_text("reports.exported").first).to_be_visible()
    shot(page, "s1-07-audit")

    # 8. Another organization sees none of it: no vehicle, no trips, no alerts; the direct link answers "not found".
    page.get_by_role("button", name="Sign out").click()
    sign_in(page, "admin@coastal.local")
    page.goto(f"{BASE}/app/vehicles")
    page.get_by_placeholder("Search").fill(reg)
    expect(page.get_by_role("row").filter(has_text=reg)).to_have_count(0)
    page.goto(f"{BASE}/app/alerts")
    page.get_by_role("button", name="All", exact=True).click()
    expect(page.locator("li[data-alert]").filter(has_text=reg)).to_have_count(0)
    page.goto(f"{BASE}/app/trips")
    expect(page.get_by_role("row").filter(has_text=reg)).to_have_count(0)
    page.goto(history_url)
    expect(page.get_by_role("alert").filter(has_text="Vehicle not found")).to_be_visible()
    shot(page, "s1-08-isolation")
