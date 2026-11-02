"""Shared fixtures for the browser acceptance tests (real Chromium against a running stack).

Run:  E2E_BASE_URL=http://localhost E2E_PASSWORD=<SEED_DEMO_PASSWORD> pytest e2e -q
Requires the demo seed (`python manage.py seed_demo`). Every test fails on any browser console error
(which includes Content-Security-Policy violations) or any 5xx response.
"""
from __future__ import annotations

import os
import re
from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, Page, expect, sync_playwright

BASE = os.environ.get("E2E_BASE_URL", "http://127.0.0.1").rstrip("/")
PASSWORD = os.environ.get("E2E_PASSWORD", "Demo-Pass-2026!")
SHOTS = os.environ.get("E2E_SCREENSHOTS", "")


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1280, "height": 860}, locale="en-GB")
    pg = context.new_page()
    problems: list[str] = []
    pg.on("console", lambda m: problems.append(f"console {m.type}: {m.text}") if m.type == "error" else None)
    # Server errors always fail. A 404 fails for anything except the API, whose 404s are handled UI states
    # ("Vehicle not found"); a missing script, style or image is a real defect.
    pg.on("response", lambda r: problems.append(f"{r.status} {r.url}")
          if r.status >= 500 or (r.status == 404 and "/api/" not in r.url) else None)
    pg.problems = problems  # type: ignore[attr-defined]
    yield pg
    context.close()
    # Client errors the UI handles and shows (validation 400, signed-out 401, forbidden 403) are logged by
    # Chromium as console errors; ignore exactly those. Server errors and script errors still fail the test.
    real = [p for p in problems if not re.search(r"status of 40[0134]", p)]
    assert not real, "Browser problems:\n" + "\n".join(real)


def shot(page: Page, name: str) -> None:
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/{name}.png", full_page=True)


def sign_in(page: Page, email: str) -> None:
    page.goto(f"{BASE}/login")
    page.get_by_label("Email").fill(email)
    page.get_by_label("Password").fill(PASSWORD)
    page.get_by_role("button", name="Sign in").click()
    # Users with the dashboard permission land on the dashboard, drivers on their vehicle, others on the welcome page.
    expect(page.get_by_role("heading", level=1)).to_have_text(re.compile(r"^(Welcome|Dashboard|My vehicle)"))




@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):  # type: ignore[no-untyped-def]
    """Save a screenshot of the page when a browser test fails (set E2E_SCREENSHOTS to a folder)."""
    outcome = yield
    report = outcome.get_result()
    page = item.funcargs.get("page")
    if report.when == "call" and report.failed and page is not None and SHOTS:
        page.screenshot(path=f"{SHOTS}/FAILED-{item.name}.png", full_page=True)


def add_tracker_via_wizard(page: Page, model: str, imei: str, name: str, sim: str | None = None,
                           vehicle: str | None = None, expect_registered: bool = True) -> None:
    """Walk the Add Device wizard (spec A7)."""
    page.goto(f"{BASE}/app/devices/new")
    page.get_by_label(re.compile(rf"^{re.escape(model)}")).check()
    page.get_by_role("button", name="Next").click()
    page.get_by_label("IMEI or identifier *").fill(imei)
    page.get_by_label("Name *").fill(name)
    page.get_by_role("button", name="Next").click()
    if sim:
        page.get_by_label("Use a SIM card already registered").check()
        page.get_by_label("SIM card *").select_option(label=sim)
    page.get_by_role("button", name="Next").click()
    if vehicle:   # options may read "T 123 ABC (replaces ...)": match the start, select by value
        option = page.locator("#w-vehicle option").filter(has_text=re.compile(rf"^{re.escape(vehicle)}")).first
        page.get_by_label("Vehicle").select_option(value=option.get_attribute("value"))
    page.get_by_role("button", name="Next").click()
    page.get_by_role("button", name="Register tracker").click()
    if expect_registered:
        expect(page.get_by_test_id("diagnostics")).to_be_visible()


MAIL_DIR = os.environ.get("E2E_MAIL_DIR", "/tmp/e2e-mail")


def invitation_link(email: str, timeout: float = 10.0) -> str:
    """The set-password link from the newest email to ``email`` (the backend writes mail to MAIL_DIR with
    django's file-based email backend in the test stack)."""
    import glob
    import time as _time
    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        files = sorted(glob.glob(os.path.join(MAIL_DIR, "*.log")), key=os.path.getmtime, reverse=True)
        for path in files:
            text = open(path, encoding="utf-8", errors="replace").read()
            if f"To: {email}" in text:
                m = re.search(r"(https?://\S+/reset-password\S+)", text.replace("=\n", ""))
                if m:
                    return m.group(1).replace("=3D", "=")
        _time.sleep(0.5)
    raise AssertionError(f"no invitation email for {email} in {MAIL_DIR}")
