"""Mock-tier tests for the shared Playwright browser-context factory (task 03.2 subtask 05).

Covers ``scraping/browser.new_scrape_context`` (native vs rotate user-agent) and the invariants
it is supposed to give the two consumers: ``scrape_details`` closes the context with the page on
every path and no longer sets a ``User-Agent`` header, and ``schema_detection`` creates its sample
pages through the same factory so its snapshots match what the scraper sees. Every external
collaborator is faked - no real browser, no network.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from bookscraper.scraping import browser as browser_mod, parameters, schema_detection as sd
from bookscraper.scraping.results import ScrapeStatus
from bookscraper.scraping.scrape_details import scrape_book

from .playwright_fakes import FakeBrowser, FakeLocator, FakePage, build_playwright_factory


def _amazon_page(title: str = "Great Python Book - Amazon") -> FakePage:
    """A minimal FakePage that drives a successful amazon detail scrape (title present)."""
    return FakePage(locators={"#productTitle": FakeLocator("Great Python Book")}, title=title)


async def test_native_mode_does_not_override_user_agent(monkeypatch):
    """
    [Local] browser: verifies native mode opens a context with no user-agent override.

    Scenario:
        Given USER_AGENT_MODE is the default "native"
        When new_scrape_context(browser) is awaited
        Then browser.new_context is called with locale and viewport but NO user_agent kwarg
        (the honest default: Playwright's bundled Chromium identity is left intact)

    Boundaries:
        - Only the kwargs passed to new_context are inspected; no page is opened, no navigation.
        - browser.version is never consulted in native mode.

    On failure, first check:
        - new_scrape_context started passing a user_agent in native mode, or dropped locale/viewport.
    """
    monkeypatch.setattr(parameters, "USER_AGENT_MODE", "native")
    browser = FakeBrowser()

    await browser_mod.new_scrape_context(browser)

    browser.new_context.assert_awaited_once()
    kwargs = browser.new_context.await_args.kwargs
    assert "user_agent" not in kwargs
    assert kwargs["locale"] == "en-US"
    assert kwargs["viewport"] == {"width": 1366, "height": 900}


async def test_rotate_mode_uses_ua_matching_installed_chromium_major(monkeypatch):
    """
    [Local] browser: verifies rotate mode sets a UA whose Chrome major matches browser.version.

    Scenario:
        Given USER_AGENT_MODE is "rotate" and browser.version reports Chromium 120
        When new_scrape_context(browser) is awaited
        Then browser.new_context receives a user_agent carrying "Chrome/120.0.0.0", so the header
        agrees with the installed engine rather than a stale hard-coded version

    Boundaries:
        - Only the user_agent kwarg is inspected; the specific template chosen is not asserted.
        - No cross-engine (Firefox/Safari) string is ever produced - all templates are Chromium.

    On failure, first check:
        - chromium_major mis-parsed browser.version, or a template dropped its {major} placeholder.
    """
    monkeypatch.setattr(parameters, "USER_AGENT_MODE", "rotate")
    browser = FakeBrowser(version="120.0.6099.109")

    await browser_mod.new_scrape_context(browser)

    kwargs = browser.new_context.await_args.kwargs
    assert "Chrome/120.0.0.0" in kwargs["user_agent"]
    assert kwargs["locale"] == "en-US"


def test_chromium_major_falls_back_on_unparseable_version():
    """
    [Local] browser: verifies chromium_major falls back to the default on an unparseable version.

    Scenario:
        Given a browser.version string with no leading integer
        When chromium_major is called
        Then it returns parameters.DEFAULT_CHROMIUM_MAJOR instead of raising

    Boundaries:
        - Pure parsing; no browser or network.

    On failure, first check:
        - the version regex started matching a non-leading digit, or the fallback was removed.
    """
    assert browser_mod.chromium_major("not-a-version") == parameters.DEFAULT_CHROMIUM_MAJOR
    assert browser_mod.chromium_major("120.0.6099.109") == 120


async def test_context_closed_with_page_on_every_path(monkeypatch):
    """
    [Local] browser: verifies scrape_book closes the per-attempt context on success and on failure.

    Scenario:
        Given a happy-path amazon scrape and, separately, a scrape whose every navigation times out
        When scrape_book runs
        Then the context is opened and closed once per attempt on both paths (1/1 on success,
        3/3 across the exhausted retries), so an attempt never leaks a context

    Boundaries:
        - The default FakeContext's close counter is compared to the new_context open counter.
        - asyncio.sleep is patched so the retry backoff does not really wait.

    On failure, first check:
        - _attempt_page stopped closing the context in its finally, or closed it off the error path.
    """
    page = _amazon_page()
    browser = FakeBrowser(page=page)

    result = await scrape_book("https://www.amazon.com/dp/B0ABCDEFGH/", browser, "amazon")

    assert result.status is ScrapeStatus.SUCCESS
    context = browser.new_context.return_value
    assert browser.new_context.await_count == 1
    assert context.close.await_count == 1

    timeout_page = _amazon_page()
    timeout_page.goto = AsyncMock(side_effect=PlaywrightTimeoutError("nav timeout"))
    failing_browser = FakeBrowser(page=timeout_page)
    with patch("bookscraper.scraping.scrape_details.asyncio.sleep", new=AsyncMock()):
        failed = await scrape_book("https://www.amazon.com/dp/B0ABCDEFGH/", failing_browser, "amazon")

    assert failed.status is ScrapeStatus.TIMEOUT
    failing_context = failing_browser.new_context.return_value
    assert failing_browser.new_context.await_count == 3
    assert failing_context.close.await_count == 3


async def test_no_set_extra_http_headers_user_agent_calls():
    """
    [Local] browser: verifies scrape_book never sets a User-Agent via set_extra_http_headers.

    Scenario:
        Given a happy-path amazon scrape
        When scrape_book runs
        Then page.set_extra_http_headers is never awaited (the UA now lives on the context, set by
        new_scrape_context, not on a per-page header)

    Boundaries:
        - Only the page's set_extra_http_headers stub is inspected; the scrape result is asserted
          only to confirm the page was actually driven.

    On failure, first check:
        - a set_extra_http_headers({"User-Agent": ...}) call crept back into scrape_book or _attempt.
    """
    page = _amazon_page()
    browser = FakeBrowser(page=page)

    result = await scrape_book("https://www.amazon.com/dp/B0ABCDEFGH/", browser, "amazon")

    assert result.status is ScrapeStatus.SUCCESS
    page.set_extra_http_headers.assert_not_awaited()


async def test_schema_detection_uses_shared_context_factory(monkeypatch):
    """
    [Local] schema detection: verifies detect_schema loads live sample pages via new_scrape_context.

    Scenario:
        Given a one-field tiny site and a live URL sample in --check-current-only mode (no AI)
        When detect_schema runs
        Then it opens the sample page through browser.new_context (i.e. new_scrape_context), so the
        snapshot's identity matches what scrape_details presents - new_context is awaited once

    Boundaries:
        - check-current-only skips the AI step entirely; no key, no client, no replay file.
        - A single URL sample, so exactly one context is opened.

    On failure, first check:
        - detect_schema reverted to browser.new_page() for live URLs instead of the shared factory.
    """
    monkeypatch.setattr(sd, "site_constants", {"tiny": {"BASE_URL": "https://x", "BOOK_TITLE": "#t"}})
    page = FakePage(locators={"#t": FakeLocator("Title")}, content="<h1 id='t'>Title</h1>")
    browser = FakeBrowser(page_factory=lambda *a, **k: page)

    with patch.object(sd, "async_playwright", build_playwright_factory(browser)):
        _report, _usage, _exit = await sd.detect_schema("tiny", urls=["https://x/book"], check_current_only=True)

    browser.new_context.assert_awaited_once()
    browser.close.assert_awaited_once()
