"""Mock-tier tests for scripts/capture_fixtures.py's logged-out capture path.

The capture script is a dev tool outside the package (and coverage), so it is loaded
by path with importlib. Playwright is faked (see playwright_fakes). The point of
these tests is the FIX-1 merge gate: capture must abort when the ephemeral context is
not cookie-less, with no file written.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from .playwright_fakes import FakeBrowser, FakeContext, FakePage

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "capture_fixtures.py"


def _load_capture_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("capture_fixtures", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


capture = _load_capture_module()


async def test_assert_logged_out_passes_for_cookieless_context() -> None:
    """Scenario: a fresh context has no cookies and no stored origins.

    Boundaries: the assertion returns without raising.
    On failure: a clean context would be wrongly rejected.
    """
    context = FakeContext(cookies=[], origins=[])
    await capture._assert_logged_out(context)


async def test_assert_logged_out_aborts_when_cookies_persist() -> None:
    """FIX-1: cookies that survive clear_cookies abort the capture.

    Scenario: the context carries a session cookie even after clearing.
    Boundaries: CaptureError is raised; no capture proceeds.
    On failure: session cookies would be captured into git history.
    """
    context = FakeContext(
        cookies=[{"name": "session-id", "value": "x"}],
        cookies_after_clear=[{"name": "session-id", "value": "x"}],
    )
    with pytest.raises(capture.CaptureError):
        await capture._assert_logged_out(context)


async def test_assert_logged_out_aborts_when_storage_state_has_origins() -> None:
    """FIX-1: a context with stored origins (localStorage) aborts the capture."""
    context = FakeContext(cookies=[], origins=[{"origin": "https://x.test"}])
    with pytest.raises(capture.CaptureError):
        await capture._assert_logged_out(context)


async def test_capture_document_uses_fresh_context_and_reasserts_before_content() -> None:
    """FIX-1: capture_document creates a new_context and returns its page content.

    Scenario: a well-behaved logged-out context yields the DOM snapshot.
    Boundaries: new_context is used (not a persistent profile) and content is returned.
    On failure: capture would read a page from a shared/persistent context.
    """
    page = FakePage(content="<html><h1>T</h1></html>")
    context = FakeContext(page=page, cookies=[])
    browser = FakeBrowser(context=context)

    html = await capture.capture_document(browser, "https://x.test/dp/1", "amazon")

    assert html == "<html><h1>T</h1></html>"
    browser.new_context.assert_awaited_once()
    context.close.assert_awaited_once()


async def test_capture_document_aborts_on_dirty_context_without_reading_content() -> None:
    """FIX-1: a dirty context aborts before page.content() and closes the context."""
    page = FakePage(content="<html>secret</html>")
    context = FakeContext(
        page=page,
        cookies=[{"name": "at-main", "value": "x"}],
        cookies_after_clear=[{"name": "at-main", "value": "x"}],
    )
    browser = FakeBrowser(context=context)

    with pytest.raises(capture.CaptureError):
        await capture.capture_document(browser, "https://x.test/dp/1", "amazon")

    page.content.assert_not_awaited()
    context.close.assert_awaited_once()


def test_keep_selectors_excludes_description() -> None:
    """The keep-selector set carries factual selectors but never the description."""
    selectors = capture.keep_selectors_for("amazon")
    from bookscraper.scraping.parameters import site_constants

    assert site_constants["amazon"]["BOOK_TITLE"] in selectors
    assert site_constants["amazon"]["DESCRIPTION"] not in selectors
    assert site_constants["amazon"]["SEARCH_BASE_URL"] not in selectors
