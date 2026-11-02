"""Phase 5 acceptance: a vehicle moves on the live map in under 2 s; isolation holds; dashboard counts live."""
from __future__ import annotations

import random
import re
import time

from conftest import BASE, add_tracker_via_wizard, shot, sign_in
from playwright.sync_api import Browser, Page, expect
from test_phase4_onboarding import new_imei
from tracker_client import Tracker


def test_vehicle_moves_on_the_live_map_within_two_seconds(page: Page) -> None:
    imei, name = new_imei(), f"Live {random.randint(1000, 9999)}"
    sign_in(page, "admin@demo.local")
    add_tracker_via_wizard(page, "Teltonika FMC920", imei, name)
    page.goto(f"{BASE}/app/live")
    expect(page.get_by_role("list", name="Vehicles")).to_be_visible()
    tracker = Tracker(imei)
    try:
        assert tracker.accepted and tracker.send(latitude=-6.8000, longitude=39.2800, speed=30) == 1
        entry = page.get_by_role("list", name="Vehicles").get_by_role("button", name=re.compile(name))
        expect(entry).to_be_visible(timeout=5000)        # unknown tracker -> snapshot refreshed, no reload
        entry.click()
        panel = page.get_by_test_id("vehicle-panel")
        expect(panel).to_contain_text("Moving")
        latencies = []
        for i in range(1, 4):
            lat = -6.8000 + i * 0.0003               # ~33 m steps: a realistic drive, never an "outlier"
            sent = time.monotonic()
            assert tracker.send(latitude=lat, longitude=39.2800, speed=40 + i) == 1
            # Checked on every animation frame, so the measurement is the real latency, not test polling delay.
            page.wait_for_function("e => document.querySelector('[data-testid=panel-position]')?.textContent === e",
                                   arg=f"{lat:.5f}, 39.28000", polling="raf", timeout=5000)
            latencies.append(time.monotonic() - sent)
        assert max(latencies) < 2.0, f"live map latencies {latencies}"
        expect(page.get_by_role("status").filter(has_text="interrupted")).to_have_count(0)
        expect(panel).to_contain_text("43 km/h")
        shot(page, "p5-01-live-map")
        print(f"\nlive map latency (s): {[round(x, 2) for x in latencies]}")
    finally:
        tracker.close()

    page.get_by_role("button", name="Sign out").click()
    sign_in(page, "admin@coastal.local")                  # another organization never sees it
    page.goto(f"{BASE}/app/live")
    expect(page.get_by_role("list", name="Vehicles")).to_be_visible()
    page.get_by_label("Find vehicle, driver or IMEI").fill(imei)
    expect(page.get_by_role("list", name="Vehicles").get_by_role("button")).to_have_count(0)


def test_dashboard_counts_vehicles_live(page: Page) -> None:
    sign_in(page, "fleet@demo.local")
    expect(page.get_by_role("heading", name="Dashboard")).to_be_visible()
    vehicles = page.get_by_test_id("card-vehicles")
    expect(vehicles).not_to_have_text(re.compile(r"Vehicles\s*0$"))
    expect(page.get_by_test_id("device-health")).to_be_visible()
    moving_card = page.get_by_test_id("card-moving")
    tracker = Tracker("356307042441013")                  # demo tracker in T 123 ABC
    try:
        assert tracker.accepted and tracker.send(speed=55, latitude=-6.8170, longitude=39.2760) in (0, 1)
    finally:
        tracker.close()
    expect(moving_card).to_contain_text(re.compile(r"[1-9]"), timeout=5000)
    shot(page, "p5-02-dashboard")


def test_live_map_fits_a_phone(browser: Browser) -> None:
    context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
    page = context.new_page()
    sign_in(page, "monitor@demo.local")
    page.goto(f"{BASE}/app/live")
    expect(page.get_by_role("list", name="Vehicles")).to_be_visible()
    width = page.evaluate("document.documentElement.scrollWidth")
    assert width <= 390, f"live map scrolls sideways on mobile ({width}px)"
    shot(page, "p5-03-live-mobile")
    context.close()
