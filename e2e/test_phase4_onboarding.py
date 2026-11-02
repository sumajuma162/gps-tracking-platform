"""Phase 4 acceptance (spec A18 Scenario 2, steps 1, 2, 3 and 5) against the live stack.

Needs the full stack: web app, real gateway (PostgreSQL storage, Redis events) and Redis. Tracker traffic comes
from the real simulator CLI or the protocol-level client in ``tracker_client``.
"""
from __future__ import annotations

import random
import re
import time
import uuid

from conftest import BASE, add_tracker_via_wizard, shot, sign_in
from playwright.sync_api import Page, expect
from tracker_client import Tracker, run_simulator


def new_imei() -> str:
    body = "35" + "".join(random.choice("0123456789") for _ in range(12))
    for check in "0123456789":
        digits = body + check
        total = sum((d * 2 - 9 if d * 2 > 9 else d * 2) if i % 2 else d
                    for i, d in enumerate(int(c) for c in reversed(digits)))
        if total % 10 == 0:
            return digits
    raise AssertionError("unreachable")


def stage(page: Page, key: str):  # type: ignore[no-untyped-def]
    return page.locator(f'[data-stage="{key}"]')


def test_step1_new_unit_registered_in_the_ui_is_accepted_without_restart(page: Page) -> None:
    imei = new_imei()
    sign_in(page, "admin@demo.local")
    add_tracker_via_wizard(page, "Teltonika FMC920", imei, f"Wizard {imei[-4:]}")
    panel = page.get_by_test_id("diagnostics")
    expect(panel).to_contain_text("No connection yet")
    expect(panel.get_by_text("Live", exact=True)).to_be_visible()          # WebSocket through the Nginx edge
    registered_at = time.monotonic()
    (result,) = run_simulator("--imei", imei, "--steps", "6", "--scenario", "normal_day")
    assert result["rejected"] is False and result["records_acked"] == 6
    assert time.monotonic() - registered_at < 10, "accepted within 10 s of registration"
    expect(panel).to_contain_text("Everything works. The tracker is reporting.", timeout=10_000)
    for key in ("connected", "first_packet", "valid_fix", "io_mapped"):
        expect(stage(page, key)).to_have_attribute("data-done", "true")
    expect(page.get_by_test_id("diag-ignition")).to_have_text("On")
    shot(page, "p4-01-wizard-connected")


def test_step2_unknown_device_goes_to_the_inbox_and_is_registered_from_there(page: Page) -> None:
    imei = new_imei()
    (first,) = run_simulator("--imei", imei, "--steps", "2")
    assert first["rejected"] is True
    sign_in(page, "superadmin@example.com")
    page.get_by_role("navigation").first.get_by_role("link", name="Unregistered devices").click()
    entry = page.locator(f'[data-identifier="{imei}"]')
    expect(entry).to_be_visible()
    expect(entry).to_contain_text("teltonika on port 5027/TCP")
    shot(page, "p4-02-inbox")
    entry.get_by_role("button", name="Register").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Organization *").select_option(label="Demo Logistics Ltd")
    dialog.get_by_label("Tracker model *").select_option(label="Teltonika FMC920")
    dialog.get_by_label("Name *").fill(f"Inbox {imei[-4:]}")
    dialog.get_by_role("button", name="Register").click()
    expect(page.get_by_role("status").filter(has_text="Registered in Demo Logistics Ltd")).to_be_visible()
    expect(entry).to_have_count(0)
    (second,) = run_simulator("--imei", imei, "--steps", "3")
    assert second["rejected"] is False and second["records_acked"] == 3   # next attempt accepted, no restart
    page.get_by_role("button", name="Sign out").click()
    sign_in(page, "admin@demo.local")
    page.goto(f"{BASE}/app/devices?")
    page.get_by_placeholder("Search").fill(imei)
    expect(page.get_by_role("row").filter(has_text=imei)).not_to_contain_text("Not connected yet")


def test_step3_cloned_model_with_edited_mapping_applies_to_the_next_record(page: Page) -> None:
    suffix = uuid.uuid4().hex[:8]                    # wide enough never to collide across test runs
    model_name, slug, imei = f"FMB920 {suffix}", f"teltonika-fmb920-{suffix}", new_imei()
    sign_in(page, "superadmin@example.com")
    page.get_by_role("navigation").first.get_by_role("link", name="Device catalog").click()
    page.get_by_placeholder("Search").fill("teltonika-fmc920")     # the catalog is paginated
    page.get_by_role("row").filter(has_text="teltonika-fmc920").get_by_role("button", name="Clone").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Name").fill(model_name)
    dialog.get_by_label("Slug").fill(slug)
    dialog.get_by_role("button", name="Clone").click()
    expect(page.get_by_role("dialog")).to_have_count(0)
    page.get_by_placeholder("Search").fill(slug)
    clone_row = page.get_by_role("row").filter(has_text=slug)
    expect(clone_row).to_be_visible()
    clone_row.get_by_role("link", name="IO mapping").click()
    scale = page.get_by_label("Scale for IO 66")
    expect(scale).to_have_value("0.001")
    scale.fill("0.01")
    page.get_by_role("button", name="Preview").click()
    expect(page.get_by_test_id("draft-66")).to_have_class(re.compile("bg-marking-soft"))
    page.get_by_label("What changed").fill("This unit reports voltage in 10 mV steps")
    page.get_by_role("button", name="Save as new version").click()
    expect(page.get_by_role("heading", level=1)).to_contain_text("v2")
    shot(page, "p4-03-io-editor")
    page.get_by_role("button", name="Sign out").click()

    sign_in(page, "admin@demo.local")
    add_tracker_via_wizard(page, f"Teltonika {model_name}", imei, f"Clone {suffix}")
    tracker = Tracker(imei)
    try:
        assert tracker.accepted
        assert tracker.send(io={239: (1, 1), 66: (2, 1265)}) == 1
    finally:
        tracker.close()
    # 1265 x 0.01 = 12.65 V with the edited mapping (the original FMC920 mapping would give 1.265 V).
    expect(page.get_by_test_id("diag-external_voltage")).to_have_text("12.65 V", timeout=10_000)


def test_step5_disabling_in_the_ui_closes_the_connection_within_5_seconds(page: Page) -> None:
    imei = new_imei()
    sign_in(page, "admin@demo.local")
    add_tracker_via_wizard(page, "Teltonika FMC920", imei, f"Disable {imei[-4:]}")
    tracker = Tracker(imei)
    assert tracker.accepted and tracker.send() == 1
    page.goto(f"{BASE}/app/devices")
    page.get_by_placeholder("Search").fill(imei)
    row = page.get_by_role("row").filter(has_text=imei)
    row.get_by_role("button", name="Disable", exact=True).click()
    clicked = time.monotonic()
    expect(row).to_contain_text("Disabled")
    try:
        assert tracker.closed_by_server(within=5), "connection still open 5 s after disabling"
        assert time.monotonic() - clicked < 5
    finally:
        tracker.close()
    retry = Tracker(imei)
    try:
        assert retry.accepted is False                                      # further data refused
    finally:
        retry.close()
