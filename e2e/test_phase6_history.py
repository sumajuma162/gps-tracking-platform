"""Phase 6 acceptance: a full day of history (8,640 points through the real gateway) renders and plays in < 2 s."""
from __future__ import annotations

import re
import time
import uuid

from conftest import BASE, add_tracker_via_wizard, shot, sign_in
from playwright.sync_api import Page, expect
from test_phase4_onboarding import new_imei
from tracker_client import run_simulator


def test_a_day_of_history_renders_and_plays_within_two_seconds(page: Page) -> None:
    reg, imei = f"H {uuid.uuid4().hex[:6].upper()}", new_imei()
    sign_in(page, "admin@demo.local")
    page.get_by_role("navigation").first.get_by_role("link", name="Vehicles").click()
    page.get_by_role("button", name="Add", exact=True).click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Registration number *").fill(reg)
    dialog.get_by_role("button", name="Add").click()
    expect(page.get_by_role("dialog")).to_have_count(0)
    add_tracker_via_wizard(page, "Teltonika FMC920", imei, f"History {reg}", vehicle=reg)

    started = time.monotonic()                               # a working day through the real gateway
    (day,) = run_simulator("--imei", imei, "--backfill-hours", "24")
    print(f"\nbackfilled {day['records_acked']} records in {time.monotonic() - started:.1f} s")
    assert day["records_acked"] == 8640 and not day["rejected"]

    page.goto(f"{BASE}/app/history")
    page.get_by_label("Vehicle").select_option(label=reg)
    route = page.get_by_test_id("history-map")
    clicked = time.monotonic()
    page.get_by_role("button", name="Last 24 hours").click()
    page.wait_for_function("el => Number(el.dataset.routePoints || 0) > 100", arg=route.element_handle(),
                           polling="raf", timeout=5000)
    render_s = time.monotonic() - clicked

    summary = page.get_by_test_id("history-summary")
    expect(summary).to_contain_text(re.compile(r"\d+(\.\d+)? km"))
    expect(page.get_by_text(re.compile(r"Drawn with \d+ of 86\d\d recorded points"))).to_be_visible()
    stops = page.get_by_role("list", name="Stops").get_by_role("button")
    assert stops.count() >= 5, "a working day has several parking stops"

    cursor = page.get_by_test_id("playback-cursor")
    expect(cursor).to_be_attached()
    before = cursor.get_attribute("data-lon")
    page.get_by_label("Speed", exact=True).select_option("300")
    pressed = time.monotonic()
    page.get_by_role("button", name="Play").click()
    page.wait_for_function("([el, v]) => el.dataset.lon !== v", arg=[cursor.element_handle(), before],
                           polling="raf", timeout=5000)
    play_s = time.monotonic() - pressed
    page.get_by_role("button", name="Pause").click()
    shot(page, "p6-01-history")
    print(f"route drawn in {render_s:.2f} s; playback moving in {play_s:.2f} s")
    assert render_s < 2.0, f"a day of history took {render_s:.2f} s to render"
    assert play_s < 2.0, f"playback took {play_s:.2f} s to start"

    with page.expect_download() as info:
        page.get_by_role("button", name="CSV").click()
    path = info.value.path()
    assert info.value.suggested_filename.startswith(reg.replace(" ", "-"))
    with open(path, encoding="utf-8") as fh:
        assert sum(1 for _ in fh) - 1 >= 8600                # every recorded point, not the simplified line

    url = page.url
    assert "vehicle=" in url                                  # the view is shareable as a link
    page.get_by_role("button", name="Sign out").click()
    sign_in(page, "admin@coastal.local")                     # another organization: the vehicle does not exist
    page.goto(url)
    expect(page.get_by_role("alert").filter(has_text="Vehicle not found")).to_be_visible()
    expect(page.get_by_test_id("history-summary")).to_have_count(0)
