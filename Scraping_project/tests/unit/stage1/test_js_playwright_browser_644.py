"""Opt-in Chromium smoke for the javascript spider (#644).

Default CI does not install a browser, so this skips. The manual
``ci:playwright`` workflow sets ``PLAYWRIGHT_REQUIRE_BROWSER=1`` after
``python -m playwright install --with-deps chromium`` and this test must
then run, not skip. ``about:blank`` is local; it is not a crawl.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_TRUTHY = {"1", "true", "yes", "on"}


def _require_browser() -> bool:
    return os.environ.get("PLAYWRIGHT_REQUIRE_BROWSER", "").strip().lower() in _TRUTHY


def _missing(message: str) -> None:
    if _require_browser():
        pytest.fail(message)
    pytest.skip(message)


@pytest.mark.playwright
@pytest.mark.stage1
def test_chromium_smoke_about_blank() -> None:
    try:
        import scrapy_playwright
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        _missing(f"Requires scrapy-playwright installation ({exc})")

    assert scrapy_playwright.__version__

    try:
        with sync_playwright() as playwright:
            executable = Path(playwright.chromium.executable_path)
            installed = executable.is_file()
    except Exception as exc:
        _missing(f"Requires Playwright Chromium ({type(exc).__name__}: {exc})")

    if not installed:
        _missing(
            "Requires Playwright Chromium "
            f"(missing {executable}). "
            "Local: python -m playwright install chromium. "
            "Image: docker build --target playwright. "
            "CI label: ci:playwright."
        )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, timeout=30_000)
        try:
            page = browser.new_page()
            page.goto("about:blank", timeout=15_000)
            assert page.url == "about:blank"
        finally:
            browser.close()
