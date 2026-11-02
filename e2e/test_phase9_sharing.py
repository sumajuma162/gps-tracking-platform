"""Phase 9: a share link created from the live map opens without signing in and shows only that vehicle."""
from __future__ import annotations

import re

from conftest import BASE, sign_in
from playwright.sync_api import Browser, Page, expect


def test_share_link_from_the_live_map_opens_signed_out(page: Page, browser: Browser) -> None:
    sign_in(page, "admin@demo.local")
    page.goto(f"{BASE}/app/live")
    entry = page.get_by_role("list", name="Vehicles").get_by_role("button", name=re.compile("T 123 ABC"))
    expect(entry).to_be_visible()
    entry.click()
    page.get_by_role("button", name="Share location").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Valid for").select_option("4")
    dialog.get_by_role("button", name="Create link").click()
    url = dialog.get_by_label("Link").input_value()
    assert re.search(r"/share/[\w-]{40,}$", url), url

    visitor = browser.new_context()                       # nobody signed in
    public = visitor.new_page()
    public.goto(url.replace("http://127.0.0.1", BASE))
    expect(public.get_by_role("heading", name="T 123 ABC")).to_be_visible()
    expect(public.get_by_test_id("share-meta")).to_contain_text("Link valid until")
    expect(public.get_by_text("Demo Logistics Ltd")).to_have_count(0)    # no organization or other data
    public.goto(f"{BASE}/app/vehicles")                   # the rest of the app still needs a sign-in
    expect(public).to_have_url(re.compile("/login"))
    visitor.close()
