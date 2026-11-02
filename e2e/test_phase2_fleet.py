"""Phase 2 browser acceptance tests: SIM, vehicle, tracker registration, installation, setup instructions,
drivers, assignment history, CSV import, cross-tenant IMEI uniqueness, catalog cloning, mobile layout."""
from __future__ import annotations

import os
import random
import re
import uuid

from conftest import BASE, add_tracker_via_wizard, shot, sign_in
from playwright.sync_api import Browser, Page, expect


def imei() -> str:
    """A random IMEI with a valid Luhn check digit (fresh per run; the dev database is persistent)."""
    body = "35" + "".join(random.choice("0123456789") for _ in range(12))
    for check in "0123456789":
        digits = body + check
        total = sum((d * 2 - 9 if d * 2 > 9 else d * 2) if i % 2 else d
                    for i, d in enumerate(int(c) for c in reversed(digits)))
        if total % 10 == 0:
            return digits
    raise AssertionError("unreachable")


def open_add(page: Page):  # type: ignore[no-untyped-def]
    page.get_by_role("button", name="Add", exact=True).click()
    return page.get_by_role("dialog")


def row(page: Page, text: str):  # type: ignore[no-untyped-def]
    return page.get_by_role("row").filter(has_text=text)


def find(page: Page, text: str):  # type: ignore[no-untyped-def]
    """Search the (paginated) list for ``text`` and return its row, as a user would."""
    expect(page.get_by_role("dialog")).to_have_count(0)   # saving resets the list; search only once it has closed
    page.get_by_placeholder("Search").fill(text)
    return row(page, text)


def test_register_install_and_configure_a_tracker(page: Page) -> None:
    suffix = str(random.randint(100, 999))
    n = random.randint(10_000_000, 99_999_999)          # 8 digits: never collides with earlier runs' data
    reg, tracker_imei = f"T {n % 1_000_000:06d} QA", imei()
    phone = f"+255 7{n // 1_000_000:02d} {n // 1000 % 1000:03d} {n % 1000:03d}"
    sign_in(page, "admin@demo.local")
    nav = page.get_by_role("navigation").first

    nav.get_by_role("link", name="SIM cards").click()
    dialog = open_add(page)
    dialog.get_by_label("Phone number *").fill(phone)
    dialog.get_by_label("Operator").fill("Vodacom")
    dialog.get_by_label("APN", exact=True).fill("internet")
    dialog.get_by_role("button", name="Add").click()
    expect(find(page, phone)).to_be_visible()

    nav.get_by_role("link", name="Vehicles").click()
    dialog = open_add(page)
    dialog.get_by_label("Registration number *").fill(reg.lower())
    dialog.get_by_label("Make").fill("Isuzu")
    dialog.get_by_role("button", name="Add").click()
    expect(find(page, reg)).to_be_visible()  # normalized to upper case by the API
    row(page, reg).get_by_role("button", name=f"More actions for {reg}").click()
    page.get_by_role("menuitem", name="Edit").click()
    dialog = page.get_by_role("dialog")
    expect(dialog.get_by_label("Registration number *")).to_have_value(reg)
    dialog.get_by_label("Model", exact=True).fill("FVR 34")
    dialog.get_by_role("button", name="Save changes").click()
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(row(page, reg)).to_contain_text("Isuzu FVR 34")

    # The wizard refuses a mistyped IMEI before contacting the server, then registers the real one.
    page.goto(f"{BASE}/app/devices/new")
    page.get_by_label(re.compile("^Teltonika FMC920")).check()
    page.get_by_role("button", name="Next").click()
    page.get_by_label("IMEI or identifier *").fill(tracker_imei[:-1] + str((int(tracker_imei[-1]) + 1) % 10))
    page.get_by_label("Name *").fill(f"Tracker {reg}")
    page.get_by_role("button", name="Next").click()
    expect(page.get_by_text("check digit is wrong").first).to_be_visible()
    add_tracker_via_wizard(page, "Teltonika FMC920", tracker_imei, f"Tracker {reg}", sim=f"{phone} · Vodacom")
    expect(page.get_by_test_id("diagnostics")).to_contain_text("No connection yet")
    page.get_by_role("button", name="Done").click()
    page.get_by_placeholder("Search").fill(tracker_imei)    # the list is paginated; find it like a user would
    tracker = row(page, tracker_imei)
    expect(tracker).to_be_visible()
    expect(tracker).to_contain_text("Not connected yet")

    tracker.get_by_role("button", name="Install in vehicle").click()
    page.get_by_role("dialog").get_by_role("combobox").select_option(label=reg)
    page.get_by_role("dialog").get_by_role("button", name="Assign").click()
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(row(page, tracker_imei)).to_contain_text(reg)
    expect(row(page, tracker_imei)).to_contain_text("Installed")

    row(page, tracker_imei).get_by_role("button", name="Setup").click()
    setup = page.get_by_role("dialog")
    # The stack's TRACKER_SERVER_HOST (.env.example ships gps.example.com, which CI uses).
    expect(setup).to_contain_text(os.environ.get("E2E_SERVER_HOST", "gps.example.com"))
    expect(setup).to_contain_text("5027")
    expect(setup).to_contain_text("internet")
    expect(setup.get_by_role("heading", name=re.compile("Connect a Teltonika FMC920"))).to_be_visible()
    shot(page, "p2-01-setup-instructions")
    setup.get_by_role("button", name="Close").click()

    row(page, tracker_imei).get_by_role("button", name="Disable", exact=True).click()
    expect(row(page, tracker_imei)).to_contain_text("Disabled")
    row(page, tracker_imei).get_by_role("button", name="Enable", exact=True).click()
    expect(row(page, tracker_imei)).to_contain_text("Enabled")
    shot(page, "p2-02-trackers")

    nav.get_by_role("link", name="Drivers").click()
    dialog = open_add(page)
    dialog.get_by_label("First name *").fill("Baraka")
    dialog.get_by_label("Last name *").fill(f"Driver{suffix}")
    dialog.get_by_label("Phone", exact=True).fill("not a phone")
    dialog.get_by_role("button", name="Add").click()
    expect(dialog.get_by_text("Enter a phone number like +255 712 345 678.")).to_be_visible()
    dialog.get_by_label("Phone", exact=True).fill("+255 754 000 111")
    dialog.get_by_role("button", name="Add").click()
    expect(find(page, f"Driver{suffix}")).to_be_visible()

    nav.get_by_role("link", name="Vehicles").click()
    find(page, reg).get_by_role("button", name="Assign driver").click()
    assert page.evaluate("document.querySelector('dialog').matches(':modal')"), "dialogs must be modal"
    page.get_by_role("dialog").get_by_role("combobox").select_option(label=f"Baraka Driver{suffix}")
    page.get_by_role("dialog").get_by_role("button", name="Assign").click()
    expect(page.get_by_role("dialog")).to_have_count(0)   # closes only once the request has succeeded
    expect(row(page, reg)).to_contain_text(f"Baraka Driver{suffix}")
    row(page, reg).get_by_role("button", name="History").click()
    history = page.get_by_role("dialog")
    expect(history).to_contain_text(tracker_imei)
    expect(history).to_contain_text(f"Baraka Driver{suffix}")
    shot(page, "p2-03-history")


def test_csv_import_preview_and_commit(page: Page) -> None:
    good, bad = imei(), "12345"
    sign_in(page, "admin@demo.local")
    page.get_by_role("navigation").first.get_by_role("link", name="Trackers").click()
    page.get_by_role("button", name="Import CSV").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Or paste the CSV").fill(
        f"model,identifier,name\nteltonika-fmc920,{good},Imported {good[-4:]}\nteltonika-fmc920,{bad},Broken row")
    dialog.get_by_role("button", name="Check file").click()
    expect(dialog.get_by_text("1 ready to import, 1 with problems (skipped).")).to_be_visible()
    expect(dialog.get_by_text("An IMEI has exactly 15 digits", exact=False)).to_be_visible()
    shot(page, "p2-04-import-preview")
    dialog.get_by_role("button", name="Import 1 trackers").click()
    expect(page.get_by_role("status").filter(has_text="Imported 1 trackers.")).to_be_visible()
    page.get_by_placeholder("Search").fill(good)             # the list is paginated; find it like a user would
    expect(row(page, good)).to_be_visible()


def test_imei_cannot_be_registered_by_two_organizations(page: Page) -> None:
    shared = imei()
    sign_in(page, "admin@demo.local")
    add_tracker_via_wizard(page, "Teltonika FMC920", shared, "Demo tracker")
    page.goto(f"{BASE}/app/devices")
    page.get_by_placeholder("Search").fill(shared)           # the list is paginated; find it like a user would
    expect(row(page, shared)).to_be_visible()
    page.get_by_role("button", name="Sign out").click()

    sign_in(page, "admin@coastal.local")
    page.get_by_role("navigation").first.get_by_role("link", name="Trackers").click()
    expect(page.get_by_text(shared)).to_have_count(0)                      # other tenant's tracker invisible
    add_tracker_via_wizard(page, "Teltonika FMC920", shared, "Same IMEI", expect_registered=False)
    alert = page.get_by_role("alert").filter(has_text="already registered").first
    expect(alert).to_be_visible()
    expect(page.get_by_label("IMEI or identifier *")).to_be_visible()     # sent back to the identity step
    expect(page.locator("main")).not_to_contain_text("Demo Logistics")     # never reveals the owner


def test_viewer_can_look_but_not_change(page: Page) -> None:
    sign_in(page, "viewer@demo.local")
    nav = page.get_by_role("navigation").first
    nav.get_by_role("link", name="Trackers").click()
    expect(page.get_by_role("heading", name="Trackers")).to_be_visible()
    expect(page.get_by_role("link", name="Add tracker")).to_have_count(0)
    expect(page.get_by_role("button", name="Import CSV")).to_have_count(0)
    expect(nav.get_by_role("link", name="SIM cards")).to_have_count(0)    # viewer lacks sims.view


def test_super_admin_clones_a_model_and_sees_ports(page: Page) -> None:
    slug = f"teltonika-fmb{uuid.uuid4().hex[:8]}"         # wide enough never to collide across test runs
    sign_in(page, "superadmin@example.com")
    nav = page.get_by_role("navigation").first
    nav.get_by_role("link", name="Device catalog").click()
    fmc = find(page, "teltonika-fmc920")
    expect(fmc).to_contain_text("Not verified")
    fmc.get_by_role("link", name="IO mapping").click()       # opens the mapping editor
    expect(page.get_by_label("Scale for IO 66")).to_have_value("0.001")
    expect(page.locator('[data-io="239"]')).to_be_visible()
    page.go_back()
    fmc = find(page, "teltonika-fmc920")
    fmc.get_by_role("button", name="Clone").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Name").fill(f"FMB {slug[-8:]}")
    dialog.get_by_label("Slug").fill(slug)
    dialog.get_by_role("button", name="Clone").click()
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(find(page, slug)).to_be_visible()
    shot(page, "p2-05-catalog")
    nav.get_by_role("link", name="Listener ports").click()
    expect(row(page, "5027/TCP")).to_be_visible()
    expect(row(page, "5027/UDP")).to_be_visible()


def test_mobile_fleet_pages_fit_the_screen(browser: Browser) -> None:
    context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
    page = context.new_page()
    sign_in(page, "admin@demo.local")
    for link, heading in (("Vehicles", "Vehicles"), ("Trackers", "Trackers")):
        page.get_by_role("button", name="Expand menu").click()
        page.get_by_role("link", name=link).click()
        expect(page.get_by_role("heading", name=heading)).to_be_visible()
        expect(page.get_by_role("button", name=re.compile("^More actions")).first).to_be_visible()
        width = page.evaluate("document.documentElement.scrollWidth")
        assert width <= 390, f"{link} scrolls sideways on mobile ({width}px)"
    shot(page, "p2-06-mobile-trackers")
    context.close()
