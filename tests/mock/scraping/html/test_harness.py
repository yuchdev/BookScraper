"""Tests for the offline fixture-serving harness (task 06.0, subtask 03).

These prove the harness is default-deny (threat model FIX-8, a CRITICAL merge gate): mapped
corpus URLs are served from committed bytes, and every unmapped Playwright/httpx request is
aborted, recorded, and fails the test that made it - never continued to the network. They
also prove corpus routes take precedence over the production ``route_handler`` so an unmapped
document can never fall through ``route.continue_()`` to the network (FIX-9).

The Chromium-backed tests carry ``@pytest.mark.chromium`` (registered in pyproject.toml,
included in the default run) and ``@pytest.mark.asyncio(loop_scope="session")`` so they share
the single session-scoped browser launch. They skip cleanly when no Chromium is installed.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from _pytest.outcomes import Failed, Skipped
from playwright.async_api import Error as PlaywrightError

from bookscraper.scraping.scrape_details import get_leanpub_book_details, route_handler

from .conftest import (
    CHROMIUM_PATH_ENV,
    CorpusMapping,
    HttpxRecorder,
    OfflineNetworkError,
    PlaywrightRecorder,
    assert_no_unexpected,
    attach_corpus_context_route,
    attach_corpus_route,
    corpus_mapping,
    launch_chromium,
)

AMAZON_URL = "https://www.amazon.com/dp/0321714113"
UNMAPPED_URL = "https://www.amazon.com/dp/UNMAPPED000"


def _amazon_mapping() -> dict:
    mapping = corpus_mapping("html")
    return {AMAZON_URL: mapping[AMAZON_URL]}


@pytest.mark.chromium
@pytest.mark.asyncio(loop_scope="session")
async def test_harness_serves_mapped_url(chromium_browser, corpus_router):
    """Scenario: a page.goto to a mapped corpus URL is served from committed bytes offline.

    Boundaries: exact mapped detail URL; content served must be the fixture, not the network.
    On failure: the harness would let a real Amazon request through (live scraping from CI) or
    serve the wrong bytes, so golden tests built on it would silently characterize garbage.
    """
    browser = corpus_router(chromium_browser, _amazon_mapping())
    page = await browser.new_page()
    await page.goto(AMAZON_URL)
    content = await page.content()
    await page.close()

    assert '<span id="productTitle">C++ Primer</span>' in content
    assert "0321714113" in content


@pytest.mark.chromium
@pytest.mark.asyncio(loop_scope="session")
async def test_harness_fails_test_on_unmapped_request(chromium_browser):
    """Scenario: an unmapped page.goto is aborted, recorded, and would fail the test (FIX-8).

    Boundaries: a URL absent from the mapping; the abort must be observable (goto raises),
    recorded verbatim, and surfaced by ``assert_no_unexpected`` naming the URL.
    On failure: an unmapped document reaches the network - the exact live-scraping-from-CI
    boundary violation FIX-8 blocks. An expected-failure helper keeps this test green while
    proving the harness fails such a request.
    """
    recorder = PlaywrightRecorder(CorpusMapping(_amazon_mapping()))
    page = await chromium_browser.new_page()
    await attach_corpus_route(page, recorder)

    with pytest.raises(PlaywrightError):
        await page.goto(UNMAPPED_URL)
    await page.close()

    assert recorder.unexpected == [UNMAPPED_URL]
    assert recorder.served == []

    with pytest.raises(Failed) as excinfo:
        assert_no_unexpected([recorder], label="Playwright")
    assert UNMAPPED_URL in str(excinfo.value)


@pytest.mark.chromium
@pytest.mark.asyncio(loop_scope="session")
async def test_harness_takes_precedence_over_production_route_handler(chromium_browser):
    """Scenario: the corpus route wins over production's document-continuing route_handler.

    Boundaries: production installs ``page.route("**/*", route_handler)`` (which would
    ``route.continue_()`` a document to the network) *after* the corpus route; the corpus must
    still serve the mapped document and nothing may fall through (FIX-9).
    On failure: an unmapped document would be continued to the live site - the FIX-9 bypass.
    """
    recorder = PlaywrightRecorder(CorpusMapping(_amazon_mapping()))
    page = await chromium_browser.new_page()
    await attach_corpus_route(page, recorder)

    # Production installs its own route AFTER the corpus route; corpus must re-assert itself.
    await page.route("**/*", route_handler)

    await page.goto(AMAZON_URL)
    content = await page.content()
    await page.close()

    assert '<span id="productTitle">C++ Primer</span>' in content
    assert recorder.served == [AMAZON_URL]
    assert recorder.unexpected == []


@pytest.mark.chromium
@pytest.mark.asyncio(loop_scope="session")
async def test_harness_routes_pages_from_new_context(chromium_browser, corpus_router):
    """Scenario: pages from a wrapped browser.new_context() serve the corpus and default-deny.

    Boundaries: a mapped request from ``new_context().new_page()`` is served from fixture bytes
    (through the fixture-wrapped browser, which forces ``service_workers="block"``); an unmapped
    request from a context page is aborted, recorded, and would fail the test (FIX-8). The
    unmapped leg runs on a standalone recorder/context (bypassing the fixture wrap via the class
    method) so the fixture teardown stays green while the failure is proved.
    On failure: a context-created page (the direction task 03.2/05 moves production onto) could
    reach the live network - the FIX-8 boundary violation, undetected.
    """
    browser = corpus_router(chromium_browser, _amazon_mapping())
    context = await browser.new_context()
    page = await context.new_page()
    await page.goto(AMAZON_URL)
    content = await page.content()
    await context.close()
    assert '<span id="productTitle">C++ Primer</span>' in content

    # Unmapped leg on an unwrapped context (class-method call bypasses the fixture override).
    recorder = PlaywrightRecorder(CorpusMapping(_amazon_mapping()))
    raw_context = await type(chromium_browser).new_context(chromium_browser, service_workers="block")
    await attach_corpus_context_route(raw_context, recorder)
    unmapped_page = await raw_context.new_page()
    with pytest.raises(PlaywrightError):
        await unmapped_page.goto(UNMAPPED_URL)
    await raw_context.close()

    assert recorder.unexpected == [UNMAPPED_URL]
    with pytest.raises(Failed) as excinfo:
        assert_no_unexpected([recorder], label="Playwright")
    assert UNMAPPED_URL in str(excinfo.value)


@pytest.mark.chromium
@pytest.mark.asyncio(loop_scope="session")
async def test_harness_precedence_over_context_route(chromium_browser):
    """Scenario: the corpus context route wins over a production context-level route_handler.

    Boundaries: production installs ``context.route("**/*", route_handler)`` *after* the corpus
    context route; a page created from the context's *original* new_page (no page-level corpus
    route) must still be served from the corpus, proving the wrapped ``context.route`` re-appends
    the corpus last (FIX-9) rather than letting route_handler continue the document to the network.
    On failure: an unmapped document on a context route would reach the live site.
    """
    recorder = PlaywrightRecorder(CorpusMapping(_amazon_mapping()))
    context = await type(chromium_browser).new_context(chromium_browser, service_workers="block")
    raw_new_page = context.new_page  # capture before attach wraps it

    await attach_corpus_context_route(context, recorder)
    # Production installs its context route AFTER corpus; wrapped context.route must re-win.
    await context.route("**/*", route_handler)

    page = await raw_new_page()  # no page-level corpus route -> only context routes decide
    await page.goto(AMAZON_URL)
    content = await page.content()
    await context.close()

    assert '<span id="productTitle">C++ Primer</span>' in content
    assert recorder.served == [AMAZON_URL]
    assert recorder.unexpected == []


@pytest.mark.chromium
@pytest.mark.asyncio(loop_scope="session")
async def test_harness_blocks_web_sockets(chromium_browser):
    """Scenario: a WebSocket opened from a served page is closed and recorded as unexpected.

    Boundaries: a mapped page opens ``new WebSocket(wss://...)``; the route_web_socket handler
    must close it (never reach the network) and record it so the test fails (FIX-8).
    On failure: a live socket would bypass HTTP routing and leak traffic from CI.
    """
    recorder = PlaywrightRecorder(CorpusMapping(_amazon_mapping()))
    context = await type(chromium_browser).new_context(chromium_browser, service_workers="block")
    await attach_corpus_context_route(context, recorder)
    page = await context.new_page()
    await page.goto(AMAZON_URL)

    await page.evaluate("() => { new WebSocket('wss://blocked.test/socket'); }")
    # Event-driven wait: route_web_socket records the blocked socket asynchronously, so poll
    # the recorder with a bounded deadline instead of sleeping a fixed 300 ms (flaky/slow).
    loop = asyncio.get_event_loop()
    deadline = loop.time() + 5.0
    while not any("blocked.test" in url for url in recorder.unexpected) and loop.time() < deadline:
        await asyncio.sleep(0.02)
    await context.close()

    assert any("blocked.test" in url for url in recorder.unexpected)
    with pytest.raises(Failed) as excinfo:
        assert_no_unexpected([recorder], label="Playwright")
    assert "blocked.test" in str(excinfo.value)


@pytest.mark.asyncio
async def test_harness_skips_cleanly_without_chromium(monkeypatch):
    """Scenario: when no Chromium can launch, the tier skips (not fails) with the install hint.

    Boundaries: bundled-browser launch raises (simulated missing executable) and no override
    env var is set.
    On failure: a machine without Chromium would hard-fail the suite instead of skipping, so
    CI could never install one incrementally (task 06.2).
    """
    monkeypatch.delenv(CHROMIUM_PATH_ENV, raising=False)
    fake_playwright = MagicMock()
    fake_playwright.chromium.launch = AsyncMock(side_effect=Exception("Executable doesn't exist at /nonexistent"))

    with pytest.raises(Skipped) as excinfo:
        await launch_chromium(fake_playwright)
    assert "playwright install chromium" in str(excinfo.value)


@pytest.mark.asyncio
async def test_harness_skips_when_env_path_missing(monkeypatch):
    """Scenario: BOOKSCRAPER_CHROMIUM_PATH pointing at a nonexistent file skips with the path.

    Boundaries: env var set to a path that does not exist; launch must not be attempted.
    On failure: a stale/typo'd override would launch the bundled browser silently or crash,
    hiding the misconfiguration.
    """
    monkeypatch.setenv(CHROMIUM_PATH_ENV, "/no/such/chromium-binary")
    fake_playwright = MagicMock()
    fake_playwright.chromium.launch = AsyncMock()

    with pytest.raises(Skipped) as excinfo:
        await launch_chromium(fake_playwright)
    assert "/no/such/chromium-binary" in str(excinfo.value)
    fake_playwright.chromium.launch.assert_not_called()


async def test_httpx_corpus_serves_json_and_rejects_unknown(httpx_corpus):
    """Scenario: mapped Leanpub JSON is served through production; an unknown URL is rejected.

    Boundaries: the mapped ``?include=accepted_authors`` endpoint (query-sensitive key) parses
    into a real record; an unmapped Leanpub URL is recorded, raises OfflineNetworkError naming
    it, and would fail the test via ``assert_no_unexpected`` (FIX-8).
    On failure: a real Leanpub API request would leave CI (live scraping) or the parser would
    be fed live, non-deterministic JSON.
    """
    mapping = corpus_mapping("json")
    httpx_corpus(mapping)

    mapped_url = next(url for url in mapping if "cpp17indetail" in url)
    base_url = mapped_url.split("?")[0]

    result = await get_leanpub_book_details(base_url)

    assert result is not None
    assert result["title"] == "C++17 in Detail"
    assert result["slug"] == "cpp17indetail"
    assert result["book_id"] == "101"
    assert result["authors"] == ["Bartlomiej Filipek"]
    assert result["last_published_at"] == "2019-08-15"

    # Reject-unknown proof via a standalone recorder so the fixture teardown stays green.
    unknown = "https://leanpub.com/api/v1/cache/books/does_not_exist.json?include=accepted_authors"
    recorder = HttpxRecorder(CorpusMapping(mapping))
    request = httpx.Request("GET", unknown)

    with pytest.raises(OfflineNetworkError) as excinfo:
        recorder.handle(request)
    assert unknown in str(excinfo.value)
    assert recorder.unexpected == [unknown]

    with pytest.raises(Failed) as failinfo:
        assert_no_unexpected([recorder], label="httpx")
    assert unknown in str(failinfo.value)
