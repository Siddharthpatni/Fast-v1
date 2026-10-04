"""Headless browser fixtures for the end-to-end suite (runs in the official Playwright image)."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5173")
ARTIFACTS = Path(__file__).parent / "artifacts"
ARTIFACTS.mkdir(exist_ok=True)


@pytest.fixture(scope="session")
def browser() -> Browser:
    with sync_playwright() as p:
        browser = p.chromium.launch()  # Headless by default
        yield browser
        browser.close()


@pytest.fixture
def page(browser: Browser, request: pytest.FixtureRequest) -> Page:
    """A desktop page that fails the test on any browser console error."""
    viewport = getattr(request, "param", {"width": 1440, "height": 900})
    context = browser.new_context(
        viewport=viewport, timezone_id="Europe/Berlin", locale="de-DE", base_url=BASE_URL
    )
    # The suite's selectors are English; the German UI has its own test
    context.add_init_script("localStorage.setItem('fastmile.lang', 'en')")
    page = context.new_page()
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(BASE_URL, wait_until="networkidle")
    yield page
    context.close()
    assert not errors, f"Browser console errors: {errors}"


def shot(page: Page, name: str) -> None:
    page.wait_for_timeout(1200)  # Let map tiles and the route fit settle
    page.screenshot(path=str(ARTIFACTS / f"{name}.png"))
