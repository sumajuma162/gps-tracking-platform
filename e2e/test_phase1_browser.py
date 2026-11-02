"""Phase 1 browser acceptance tests: landing, sign-in, users, audit, roles, organizations, mobile."""
from __future__ import annotations

import re
import uuid

from conftest import BASE, PASSWORD, shot, sign_in  # noqa: F401
from playwright.sync_api import Browser, Page, expect


def test_landing_is_public(page: Page) -> None:
    page.goto(BASE + "/")
    expect(page.get_by_role("heading", level=1)).to_have_text("Know where every vehicle is, and where it has been.")
    expect(page.get_by_role("img", name=re.compile("Not live data"))).to_be_visible()
    expect(page.get_by_text("Teltonika FMC920")).to_be_visible()
    shot(page, "01-landing")
    page.get_by_role("link", name="Sign in to your fleet").click()
    expect(page).to_have_url(f"{BASE}/login")


def test_admin_journey(page: Page) -> None:
    page.goto(f"{BASE}/login")
    page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_text("Enter a valid email address.")).to_be_visible()
    page.get_by_label("Email").fill("admin@demo.local")
    page.get_by_label("Password").fill("definitely-wrong")
    page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_role("alert")).to_contain_text("incorrect")
    shot(page, "02-login-error")

    sign_in(page, "admin@demo.local")
    nav = page.get_by_role("navigation").first
    for item in ("Dashboard", "Live map", "Users", "Roles", "Audit log", "Profile"):
        expect(nav.get_by_role("link", name=item)).to_be_visible()
    expect(nav.get_by_role("link", name="Organizations")).to_have_count(0)
    shot(page, "03-overview")

    nav.get_by_role("link", name="Users").click()
    page.get_by_placeholder("Search").fill("fleet@demo.local")      # the user list is paginated
    expect(page.get_by_role("cell", name=re.compile("fleet@demo.local"))).to_be_visible()
    page.get_by_placeholder("Search").fill("")
    email = f"e2e-{uuid.uuid4().hex[:8]}@demo.local"
    page.get_by_role("button", name="Add user").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Email").fill(email)
    dialog.get_by_label("Full name").fill("Browser Test")
    dialog.get_by_role("button", name="Add user").click()
    expect(page.get_by_role("status").filter(has_text="User added")).to_be_visible()
    page.get_by_placeholder("Search").fill(email)
    expect(page.get_by_role("cell", name=re.compile(email))).to_be_visible()
    shot(page, "04-users")

    page.reload()  # access token is in memory only: the session must come back from the httpOnly cookie
    page.get_by_placeholder("Search").fill(email)
    expect(page.get_by_role("cell", name=re.compile(email))).to_be_visible()

    nav.get_by_role("link", name="Audit log").click()
    expect(page.get_by_role("cell", name="users.added").first).to_be_visible()
    expect(page.get_by_role("cell", name="auth.login").first).to_be_visible()

    page.get_by_role("combobox").filter(has_text="English").select_option("sw")
    expect(nav.get_by_role("link", name="Watumiaji")).to_be_visible()
    shot(page, "05-audit-sw")
    page.get_by_role("combobox").filter(has_text="Kiswahili").select_option("en")

    page.get_by_role("button", name="Sign out").click()
    expect(page.get_by_role("heading", name="Sign in")).to_be_visible()
    page.goto(f"{BASE}/app/users")
    expect(page).to_have_url(re.compile(r"/login\?next=%2Fapp%2Fusers"))


def test_viewer_sees_only_what_the_role_allows(page: Page) -> None:
    sign_in(page, "viewer@demo.local")
    nav = page.get_by_role("navigation").first
    expect(nav.get_by_role("link", name="Users")).to_have_count(0)
    expect(nav.get_by_role("link", name="Audit log")).to_have_count(0)
    page.goto(f"{BASE}/app/users")
    expect(page.get_by_text("You don't have access to this page.")).to_be_visible()


def test_super_admin_manages_organizations(page: Page) -> None:
    sign_in(page, "superadmin@example.com")
    page.get_by_role("navigation").first.get_by_role("link", name="Organizations").click()
    expect(page.get_by_role("cell", name=re.compile("Demo Logistics Ltd"))).to_be_visible()
    expect(page.get_by_role("cell", name=re.compile("Coastal Transport Ltd"))).to_be_visible()
    shot(page, "06-organizations")


def test_mobile_layout(browser: Browser) -> None:
    context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
    page = context.new_page()
    sign_in(page, "admin@demo.local")
    page.get_by_role("button", name="Expand menu").click()
    page.get_by_role("link", name="Users").click()
    page.get_by_placeholder("Search").fill("fleet@demo.local")      # the user list is paginated
    expect(page.get_by_text("fleet@demo.local").first).to_be_visible()
    expect(page.get_by_role("button", name="Remove access").first).to_be_visible()
    width = page.evaluate("document.documentElement.scrollWidth")
    assert width <= 390, f"page scrolls sideways on mobile ({width}px wide)"
    shot(page, "07-mobile-users")
    context.close()
