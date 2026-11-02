"""Phase 8 in the browser: an alert rule made in the UI fires from a simulated drive (acknowledge, resolve), and a
Codec 12 command sent from the UI round-trips with the simulator (spec A11, A13)."""
from __future__ import annotations

import re
import threading
import uuid

from conftest import BASE, add_tracker_via_wizard, shot, sign_in
from playwright.sync_api import Page, expect
from test_phase4_onboarding import new_imei
from tracker_client import run_simulator


def test_alert_rule_fires_and_is_handled_and_a_command_round_trips(page: Page) -> None:
    suffix = uuid.uuid4().hex[:6].upper()
    rule, reg, imei = f"Speeding {suffix}", f"S {suffix}", new_imei()
    sign_in(page, "admin@demo.local")

    # 0. A fresh vehicle.
    page.get_by_role("navigation").first.get_by_role("link", name="Vehicles").click()
    page.get_by_role("button", name="Add", exact=True).click()
    page.get_by_role("dialog").get_by_label("Registration number *").fill(reg)
    page.get_by_role("dialog").get_by_role("button", name="Add").click()
    expect(page.get_by_role("dialog")).to_have_count(0)
    # 1. Alert rule through the UI: overspeed above 90 km/h for this vehicle, notify organization admins.
    page.goto(f"{BASE}/app/alert-rules")
    page.get_by_role("button", name="Add", exact=True).click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Name *").fill(rule)
    dialog.get_by_label("Alert type *").select_option("overspeed")
    dialog.get_by_label("Severity *").select_option("warning")
    dialog.get_by_label("Threshold").fill("90")
    dialog.get_by_label(re.compile("Notify roles")).select_option("org_admin")
    dialog.get_by_label("All vehicles").uncheck()                  # only this test's vehicle
    dialog.get_by_label("Vehicles", exact=True).select_option(label=reg)
    dialog.get_by_role("button", name="Add").click()
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(page.get_by_role("cell").filter(has_text=rule)).to_be_visible()

    # 2. A tracker in the vehicle drives the overspeed scenario through the real gateway.
    add_tracker_via_wizard(page, "Teltonika FMC920", imei, f"Alerting {suffix}", vehicle=reg)
    (drive,) = run_simulator("--imei", imei, "--steps", "120", "--scenario", "overspeed")
    assert drive["records_acked"] == 120

    # 3. The alert appears (in-app); acknowledge with a note, then resolve.
    page.goto(f"{BASE}/app/alerts")
    alert = page.locator('li[data-alert="overspeed"]').filter(has_text=reg).filter(has_text=rule)
    for _ in range(10):
        if alert.count():
            break
        page.wait_for_timeout(1000)
        page.reload()
    expect(alert.first).to_contain_text(re.compile(rf"{reg} exceeded 90 km/h \(top \d+"))
    expect(page.get_by_test_id("notification-bell")).to_be_visible()
    alert.first.get_by_role("button", name="Acknowledge").click()
    page.get_by_role("dialog").get_by_label("Notes").fill("Driver called")
    page.get_by_role("dialog").get_by_role("button", name="Acknowledge").click()
    expect(alert.first).to_contain_text("Acknowledged")
    expect(alert.first).to_contain_text("Driver called")
    shot(page, "p8-01-alerts")
    alert.first.get_by_role("button", name="Resolve").click()
    page.get_by_role("dialog").get_by_role("button", name="Resolve").click()
    page.get_by_role("button", name="Resolved", exact=True).click()
    expect(page.locator('li[data-alert="overspeed"]').filter(has_text=reg).filter(has_text=rule).first).to_contain_text("Resolved")

    # 4. Commands: the simulator stays connected (FMC920 "Open Link Timeout"); getinfo round-trips.
    results: list[object] = []
    sim = threading.Thread(target=lambda: results.append(run_simulator("--imei", imei, "--steps", "3", "--hold", "25")))
    sim.start()
    page.goto(f"{BASE}/app/devices")
    page.get_by_placeholder("Search").fill(imei)
    row = page.get_by_role("row").filter(has_text=imei)
    row.get_by_role("button", name="Commands").click()
    cmds = page.get_by_role("dialog")
    cmds.get_by_role("listitem").filter(has_text="getinfo").get_by_role("button", name="Send").click()
    expect(cmds.locator('[data-command-status="ACKNOWLEDGED"]').first).to_be_visible(timeout=15000)
    expect(cmds.get_by_test_id("command-response").first).to_contain_text("RTC:")
    shot(page, "p8-02-commands")
    sim.join(timeout=40)
    assert results and results[0][0]["commands_answered"] >= 1  # type: ignore[index]

    # Clean up the rule so later runs' vehicles don't raise alerts for it.
    page.goto(f"{BASE}/app/alert-rules")
    page.get_by_role("row").filter(has_text=rule).get_by_role("button", name=re.compile("More|actions", re.I)).click()
    page.get_by_role("menuitem", name="Delete").click()
    page.get_by_role("dialog").get_by_role("button", name="Delete").click()
    expect(page.get_by_role("cell").filter(has_text=rule)).to_have_count(0)
