"""Unit/mock tests for bookscraper.scraping.search_utils.

The Leanpub JSON-API path is covered thoroughly. The Playwright/selector-heavy
``get_search_results_via_playwright`` (the ~150-line amazon ``match/case`` branch) is
covered at a pragmatic level per the project's coverage plan - see the module-level note
near those tests for exactly which deep DOM branches are and are not exercised.
"""

import copy
import json
from contextlib import AbstractContextManager
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from playwright.async_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError

from bookscraper.scraping import search_utils
from bookscraper.scraping.parameters import DEFAULT_RATE_LIMIT, RATE_LIMITS
from bookscraper.scraping.politeness import (
    DomainCircuitBreakers,
    DomainRateLimiters,
    RobotsCache,
    RobotsResponse,
)
from bookscraper.scraping.search_utils import (
    extract_amazon_asin,
    get_leanpub_search_results_via_api,
    get_search_results_via_playwright,
    parse_rating,
    parse_rating_count,
    resolve_amazon_max_pages,
    unwrap_amazon_detail_url,
)

from .playwright_fakes import (
    FakeBrowser,
    FakeContext,
    FakeLocator,
    FakePage,
    FakeResponse,
    build_async_client_factory,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _search_book(book_id, title="A Book", slug="a-book", author_id="a1") -> dict[str, Any]:
    """Build a minimal Leanpub search-result book dict."""
    return {
        "id": book_id,
        "attributes": {"title": title, "slug": slug},
        "relationships": {"accepted_authors": {"data": [{"id": author_id}]}},
    }


def _search_payload(books, author_name="Jane") -> dict[str, Any]:
    """Build a Leanpub search API response payload containing the given books."""
    return {
        "data": books,
        "included": [
            {"type": "SimpleAuthor", "id": "a1", "attributes": {"name": author_name}},
        ],
    }


def _patch_httpx(responses=None, get_side_effect=None) -> AbstractContextManager[Any]:
    """Context manager: patch httpx.AsyncClient with a FakeResponse sequence."""
    factory, _client = build_async_client_factory(responses=responses, get_side_effect=get_side_effect)
    return patch.object(search_utils.httpx, "AsyncClient", factory)


def _fast_limiters() -> DomainRateLimiters:
    """Build a per-call DomainRateLimiters whose sleep is a no-op so throttling never waits.

    The random inter-page sleep was removed in favour of the per-host rate limiter; injecting a
    no-op sleep keeps the pagination tests from ever waiting in real time and isolates them from
    the shared module-level limiter's state.
    """
    sleep = AsyncMock()
    return DomainRateLimiters(RATE_LIMITS, DEFAULT_RATE_LIMIT, sleep=sleep)


@pytest.fixture(autouse=True)
def _no_wait_default_limiters():
    """Replace the shared default rate limiter with a never-throttling one for every test.

    The Playwright search path uses the module-level default when no limiter is passed; an
    enormous burst means ``acquire`` always has a token and never sleeps, so no test waits on the
    real clock while the limiter integration stays exercised.
    """
    unbounded = DomainRateLimiters({}, (1_000_000.0, 1_000_000))
    with patch.object(search_utils, "default_limiters", return_value=unbounded):
        yield


@pytest.fixture(autouse=True)
def _fresh_default_breakers():
    """Give every test its own circuit-breaker registry so bot-wall state never leaks across tests.

    The Playwright search path falls back to the module-level ``default_breakers`` when no registry
    is passed; the breaker's open state has a 900 s cooldown, so without isolation a test that trips
    the amazon.com breaker would make every later amazon test short-circuit. A fresh registry per
    test keeps the integration exercised without cross-test contamination.
    """
    with patch.object(search_utils, "default_breakers", return_value=DomainCircuitBreakers()):
        yield


def _disallow_all_robots() -> RobotsCache:
    """Build a RobotsCache whose canned robots.txt disallows every path on the host."""

    async def _fetch(robots_url: str) -> RobotsResponse:
        """Return a robots.txt that disallows everything."""
        return RobotsResponse(status_code=200, text="User-agent: *\nDisallow: /\n")

    return RobotsCache(_fetch, DomainRateLimiters({}, (1_000_000.0, 1_000_000)))


# ---------------------------------------------------------------------------
# get_leanpub_search_results_via_api
# ---------------------------------------------------------------------------


async def test_leanpub_api_search_skipped_when_robots_disallows():
    """
    [Local] search utils: verifies a robots.txt-disallowed Leanpub API is never fetched.

    Scenario:
        Given a RobotsCache that disallows the Leanpub search API path
        When get_leanpub_search_results_via_api is called with that cache
        Then it returns an empty list and httpx.AsyncClient is never constructed (no request).

    Boundaries:
        - The Leanpub JSON:API path goes through the same robots check as every other URL; the
          check wins over fetching.

    On failure, first check:
        - The robots.allowed gate at the top of get_leanpub_search_results_via_api.
    """
    client_factory = MagicMock()
    with patch.object(search_utils.httpx, "AsyncClient", client_factory):
        books = await get_leanpub_search_results_via_api(
            "python", limiters=_fast_limiters(), robots=_disallow_all_robots()
        )

    assert books == []
    client_factory.assert_not_called()


async def test_leanpub_api_single_short_page_stops_after_page_one():
    """
    [Local] search utils: verifies that a single short page returns exactly that page's books.

    Scenario:
        Given one API response page carrying a single book (fewer than the page-size threshold)
        When get_leanpub_search_results_via_api is called
        Then exactly one book is returned with all fields (title, slug, book_id, site,
        authors) correctly populated, and no second request is made.

    Boundaries:
        - Covers the single-page termination path; multi-page accumulation is a separate test.

    On failure, first check:
        - Field-extraction logic and the loop-stop condition (short page < page size).
    """
    payload = _search_payload([_search_book("b1", title="Python 101", slug="python-101")])
    response = FakeResponse(json_data=payload)
    with _patch_httpx(responses=[response]):
        books = await get_leanpub_search_results_via_api("python")

    assert len(books) == 1
    book = books[0]
    assert book["title"] == "Python 101"
    assert book["slug"] == "python-101"
    assert book["book_id"] == "b1"
    assert book["site"] == "leanpub"
    assert book["authors"] == ["Jane"]


async def test_leanpub_api_full_page_then_short_page_combines_results():
    """
    [Local] search utils: verifies that results from two pages are combined into one list.

    Scenario:
        Given a first response with 100 books (full page) and a second with 2 books
        When get_leanpub_search_results_via_api is called
        Then 102 books are returned in total.

    Boundaries:
        - Covers only the accumulation count, not per-field extraction (covered separately).

    On failure, first check:
        - The pagination loop and result-accumulation logic in get_leanpub_search_results_via_api.
    """
    page1_books = [_search_book(f"b{i}", title=f"Book {i}") for i in range(100)]
    page2_books = [_search_book("x1"), _search_book("x2")]
    responses = [
        FakeResponse(json_data=_search_payload(page1_books)),
        FakeResponse(json_data=_search_payload(page2_books)),
    ]
    with _patch_httpx(responses=responses):
        books = await get_leanpub_search_results_via_api("python", limiters=_fast_limiters())

    assert len(books) == 102


async def test_leanpub_api_max_pages_caps_pagination():
    """
    [Local] search utils: verifies that max_pages stops pagination after the capped page count.

    Scenario:
        Given two full (100-book) pages that would otherwise both be fetched
        When get_leanpub_search_results_via_api is called with max_pages=1
        Then only the first page is fetched (100 books) and the second page is never requested.

    Boundaries:
        - Covers the --max-search-pages cap wiring; the "short page stops early" path is separate.

    On failure, first check:
        - The "if max_pages is not None and current_page > max_pages" guard in the pagination loop.
    """
    page1_books = [_search_book(f"b{i}", title=f"Book {i}") for i in range(100)]
    page2_books = [_search_book(f"c{i}", title=f"More {i}") for i in range(100)]
    responses = [
        FakeResponse(json_data=_search_payload(page1_books)),
        FakeResponse(json_data=_search_payload(page2_books)),
    ]
    with _patch_httpx(responses=responses):
        books = await get_leanpub_search_results_via_api("python", limiters=_fast_limiters(), max_pages=1)

    assert len(books) == 100


async def test_leanpub_api_empty_data_returns_empty_immediately():
    """
    [Local] search utils: verifies that an empty "data" array terminates the search immediately.

    Scenario:
        Given an API response whose "data" list is empty
        When get_leanpub_search_results_via_api is called
        Then an empty list is returned and no further pages are requested.

    Boundaries:
        - Covers the empty-page early-exit; error paths are separate tests.

    On failure, first check:
        - The empty-data guard in the pagination loop.
    """
    response = FakeResponse(json_data={"data": [], "included": []})
    with _patch_httpx(responses=[response]):
        books = await get_leanpub_search_results_via_api("python")

    assert books == []


async def test_leanpub_api_request_error_breaks_and_returns_accumulated():
    """
    [Local] search utils: verifies that a RequestError on page 1 breaks the loop and returns [].

    Scenario:
        Given the mock httpx client raises RequestError on the first request
        When get_leanpub_search_results_via_api is called
        Then an empty list is returned (nothing accumulated before the error).

    Boundaries:
        - Error on the first page; partial-accumulation behavior is tested separately.

    On failure, first check:
        - The except httpx.RequestError handler in the pagination loop.
    """
    request = httpx.Request("GET", "https://leanpub.com/api")
    exc = httpx.RequestError("network down", request=request)
    with _patch_httpx(get_side_effect=[exc]):
        books = await get_leanpub_search_results_via_api("python")

    assert books == []


async def test_leanpub_api_request_error_on_second_page_keeps_first_page():
    """
    [Local] search utils: verifies that a page-2 error keeps page-1 results already accumulated.

    Scenario:
        Given a first response with 100 books (full page) followed by a RequestError on page 2
        When get_leanpub_search_results_via_api is called
        Then the 100 books from page 1 are returned despite the error.

    Boundaries:
        - Covers partial-accumulation on mid-stream network error; single-page error is separate.

    On failure, first check:
        - Whether the pagination loop discards or preserves previously accumulated results on error.
    """
    page1_books = [_search_book(f"b{i}") for i in range(100)]
    request = httpx.Request("GET", "https://leanpub.com/api")
    exc = httpx.RequestError("boom", request=request)
    responses = [FakeResponse(json_data=_search_payload(page1_books)), exc]
    with _patch_httpx(get_side_effect=responses):
        books = await get_leanpub_search_results_via_api("python", limiters=_fast_limiters())

    assert len(books) == 100


async def test_leanpub_api_json_decode_error_breaks_and_returns_empty():
    """
    [Local] search utils: verifies that a JSONDecodeError breaks the loop and returns [].

    Scenario:
        Given the mock response's .json() raises json.JSONDecodeError
        When get_leanpub_search_results_via_api is called
        Then an empty list is returned.

    Boundaries:
        - JSON-parse failure only; network errors are a separate test.

    On failure, first check:
        - The except json.JSONDecodeError branch in the pagination loop.
    """
    decode_exc = json.JSONDecodeError("Expecting value", "", 0)
    response = FakeResponse(json_exc=decode_exc, text="<html>nope</html>")
    with _patch_httpx(responses=[response]):
        books = await get_leanpub_search_results_via_api("python")

    assert books == []


async def test_leanpub_api_generic_exception_breaks_and_returns_empty():
    """
    [Local] search utils: verifies that an unexpected RuntimeError breaks the loop and returns [].

    Scenario:
        Given the mock httpx client raises RuntimeError("unexpected") on .get()
        When get_leanpub_search_results_via_api is called
        Then an empty list is returned.

    Boundaries:
        - Broad except fallback; typed errors (RequestError, JSONDecodeError) are separate.

    On failure, first check:
        - The bare except / outer exception handler in the pagination loop.
    """
    with _patch_httpx(get_side_effect=[RuntimeError("unexpected")]):
        books = await get_leanpub_search_results_via_api("python")

    assert books == []


# ---------------------------------------------------------------------------
# get_search_results_via_playwright
#
# Coverage note: the amazon happy path, the sponsored-link URL/ASIN extraction (with and
# without a usable ``url`` param), the "no books found" short-circuit, the empty-card-list
# break, the no-ASIN-attribute / publication-date (present / absent-selector / raising)
# branches, the per-card exception-recovery ``continue``, the navigation-failure handler,
# and the two early-return guards + non-amazon default (``pass``) are all exercised. Multi-
# page pagination and the real live-markup card extraction are covered by the Chromium
# golden tier (``tests/mock/scraping/html/test_search_goldens.py``) against the captured
# corpus. The only remaining uncovered lines in the amazon ``case`` are the ``else`` legs
# for *other* sites (non-amazon URL join, the leanpub author split) which are structurally
# unreachable while that whole block lives under ``case "amazon"`` - subtask 03.3 is what
# untangles the per-site branches.
# ---------------------------------------------------------------------------

AMAZON_LINK = "a.a-link-normal.s-line-clamp-2.s-link-style.a-text-normal"
AMAZON_TITLE = "h2.a-size-medium.a-spacing-none.a-color-base.a-text-normal > span"
AMAZON_AUTHORS = "div.a-row.a-size-base.a-color-secondary div.a-row > a.a-link-normal"
AMAZON_CARD = '[data-component-type="s-search-result"]'
AMAZON_NEXT = ".s-pagination-next"


async def test_playwright_unknown_site_returns_empty_without_touching_browser():
    """
    [Local] search utils: verifies that an unknown site name returns [] without opening a page.

    Scenario:
        Given a site name ("nonexistent-site") that has no entry in site_constants
        When get_search_results_via_playwright is called
        Then an empty list is returned and browser.new_page is never called.

    Boundaries:
        - Only the site-config guard is exercised; actual Amazon extraction is a separate test.

    On failure, first check:
        - The site_config guard at the top of get_search_results_via_playwright.
    """
    browser = FakeBrowser()
    result = await get_search_results_via_playwright(browser, "nonexistent-site", "python")

    assert result == []
    browser.new_page.assert_not_called()


async def test_playwright_missing_search_base_url_returns_empty(monkeypatch):
    # Truthy site config (so the site_config guard passes) but no SEARCH_BASE_URL key.
    """
    [Local] search utils: verifies that a site config with no SEARCH_BASE_URL returns [].

    Scenario:
        Given site_constants patched so "fakesite" has a truthy config but no SEARCH_BASE_URL
        When get_search_results_via_playwright is called for "fakesite"
        Then an empty list is returned and browser.new_page is never called.

    Boundaries:
        - Covers the SEARCH_BASE_URL guard; the unknown-site guard is a separate test.

    On failure, first check:
        - The SEARCH_BASE_URL key lookup guard in get_search_results_via_playwright.
    """
    monkeypatch.setattr(search_utils, "site_constants", {"fakesite": {"FOO": "bar"}})
    browser = FakeBrowser()

    result = await get_search_results_via_playwright(browser, "fakesite", "python")

    assert result == []
    browser.new_page.assert_not_called()


async def test_playwright_amazon_happy_path_extracts_one_book():
    """
    [Local] search utils: verifies that a single amazon search card is fully extracted.

    Scenario:
        Given a FakePage with one card containing ASIN, href, title, and authors
        When get_search_results_via_playwright is called for amazon
        Then one result is returned with all fields (title, site, query, url, asin,
        authors_from_search) correctly set.

    Boundaries:
        - Single card, no pagination, no sponsored link; each edge case is a separate test.

    On failure, first check:
        - The amazon case-branch in get_search_results_via_playwright and selector constants.
    """
    card = {
        "text": "",
        "attrs": {"data-asin": "B0ABCDEFGH"},
        "sub": {
            AMAZON_LINK: FakeLocator([{"attrs": {"href": "/dp/B0ABCDEFGH/ref=sr_1_1"}}]),
            AMAZON_TITLE: FakeLocator("Great Python Book"),
            AMAZON_AUTHORS: FakeLocator([{"text": "Jane Doe"}, {"text": "John Smith"}]),
        },
    }
    page = FakePage(locators={AMAZON_CARD: FakeLocator([card])}, title="Amazon")
    browser = FakeBrowser(page=page)

    result = await get_search_results_via_playwright(browser, "amazon", "python")

    assert len(result) == 1
    book = result[0]
    assert book["title"] == "Great Python Book"
    assert book["site"] == "amazon"
    assert book["query"] == "python"
    assert book["url"] == "https://www.amazon.com/dp/B0ABCDEFGH/ref=sr_1_1"
    assert book["asin"] == "B0ABCDEFGH"
    assert book["authors_from_search"] == "Jane Doe, John Smith"


async def test_playwright_amazon_multipage_closes_each_context_exactly_once(monkeypatch):
    """
    [Local] search utils: verifies each per-page BrowserContext is closed exactly once.

    Scenario:
        Given page 1 exposing an enabled next-page control and page 2 none (so pagination stops
        after two pages), each served by its own FakeContext/FakePage, When
        get_search_results_via_playwright runs, Then two books are returned and every per-iteration
        context.close() was awaited exactly once (count == pages fetched), proving no context leaks.

    Boundaries:
        - Exercises the per-iteration try/finally (via _search_page); the single-page close is
          covered by the happy-path test.

    On failure, first check:
        - The ``async with _search_page(browser)`` wrapper around each amazon search-page iteration.
    """
    sc = copy.deepcopy(search_utils.site_constants)
    sc["amazon"]["SEARCH_MAXIMUM_PAGES"] = 5  # high cap; the next-button state decides where it stops
    monkeypatch.setattr(search_utils, "site_constants", sc)

    def _page_with_card(asin: str, *, has_next: bool) -> FakePage:
        """Build a FakePage serving one amazon card, optionally exposing an enabled next-page link."""
        card = {
            "text": "",
            "attrs": {"data-asin": asin},
            "sub": {
                AMAZON_LINK: FakeLocator([{"attrs": {"href": f"/dp/{asin}/ref=sr"}}]),
                AMAZON_TITLE: FakeLocator(f"Book {asin}"),
                AMAZON_AUTHORS: FakeLocator([{"text": "Jane Doe"}]),
            },
        }
        locators = {AMAZON_CARD: FakeLocator([card])}
        if has_next:
            # An enabled next control (no s-pagination-disabled class) drives one more page.
            locators[AMAZON_NEXT] = FakeLocator([{"attrs": {"class": "s-pagination-item s-pagination-next"}}])
        return FakePage(locators=locators, title="Amazon")

    # Page 1 exposes an enabled "next"; page 2 does not, so pagination stops after exactly two pages.
    contexts = [
        FakeContext(page=_page_with_card("B0PAGE0001", has_next=True)),
        FakeContext(page=_page_with_card("B0PAGE0002", has_next=False)),
    ]
    context_iter = iter(contexts)

    browser = FakeBrowser()
    browser.new_context = AsyncMock(side_effect=lambda **kwargs: next(context_iter))

    result = await get_search_results_via_playwright(browser, "amazon", "python")

    assert len(result) == 2
    assert browser.new_context.await_count == 2
    for context in contexts:
        assert context.close.await_count == 1


async def test_playwright_amazon_no_books_found_short_circuits(monkeypatch):
    # amazon has no SEARCH_NO_BOOKS_FOUND in real config; inject one for this case.
    """
    [Local] search utils: verifies that a matching no-results selector short-circuits to [].

    Scenario:
        Given site_constants patched to add SEARCH_NO_BOOKS_FOUND with a matching selector,
        and a page whose locator for that selector returns a non-empty list
        When get_search_results_via_playwright is called
        Then an empty list is returned and wait_for_selector is never called (no card scraping).

    Boundaries:
        - SEARCH_NO_BOOKS_FOUND is not in the real amazon config; monkeypatched in here only.

    On failure, first check:
        - The no-books-found guard before the card-scraping loop.
    """
    sc = copy.deepcopy(search_utils.site_constants)
    sc["amazon"]["SEARCH_NO_BOOKS_FOUND"] = "div.no-results"
    monkeypatch.setattr(search_utils, "site_constants", sc)

    page = FakePage(
        locators={"div.no-results": FakeLocator([{"text": "No results found"}])},
        title="Amazon",
    )
    browser = FakeBrowser(page=page)

    result = await get_search_results_via_playwright(browser, "amazon", "python")

    assert result == []
    # No card scraping was attempted.
    page.wait_for_selector.assert_not_called()


async def test_playwright_amazon_sponsored_link_extracts_real_url_and_asin():
    """
    [Local] search utils: verifies that a sponsored /sspa/click URL is decoded to the real dp URL.

    Scenario:
        Given a card whose href is an /sspa/click URL with a URL-encoded "url" query param
        pointing to a real /dp/B0SPONSOR1 path
        When get_search_results_via_playwright is called
        Then the result's url is the decoded dp URL and asin is the ASIN extracted from it.

    Boundaries:
        - Covers the sponsored-link decode path; the "no url param" skip is a separate test.

    On failure, first check:
        - The /sspa/click URL-decoding logic and the ASIN extraction from the decoded path.
    """
    sponsored_href = "/sspa/click?ie=UTF8&url=%2Fdp%2FB0SPONSOR1%2Fref%3Dabc&qual=1"
    card = {
        "text": "",
        "attrs": {},
        "sub": {
            AMAZON_LINK: FakeLocator([{"attrs": {"href": sponsored_href}}]),
            AMAZON_TITLE: FakeLocator("Sponsored Python Book"),
            AMAZON_AUTHORS: FakeLocator([{"text": "Ada Lovelace"}]),
        },
    }
    page = FakePage(locators={AMAZON_CARD: FakeLocator([card])}, title="Amazon")
    browser = FakeBrowser(page=page)

    result = await get_search_results_via_playwright(browser, "amazon", "python")

    assert len(result) == 1
    book = result[0]
    assert book["url"] == "https://www.amazon.com/dp/B0SPONSOR1/ref=abc"
    assert book["asin"] == "B0SPONSOR1"


async def test_playwright_non_amazon_default_case_returns_empty_cleanly():
    # leanpub has a SEARCH_BASE_URL so it passes the guards, then hits `case _: pass`.
    """
    [Local] search utils: verifies that a non-amazon site with a SEARCH_BASE_URL returns [] cleanly.

    Scenario:
        Given the "leanpub" site (which has SEARCH_BASE_URL so both guards pass) passed
        to get_search_results_via_playwright
        When the function runs
        Then the match/case default branch (case _: pass) fires, an empty list is returned,
        and browser.new_page is never called.

    Boundaries:
        - Exercises the case _: pass branch; the amazon case is a separate test.

    On failure, first check:
        - The match/case dispatch in get_search_results_via_playwright.
    """
    browser = FakeBrowser()

    result = await get_search_results_via_playwright(browser, "leanpub", "python")

    assert result == []
    # The default branch never creates a page.
    browser.new_page.assert_not_called()


async def test_playwright_amazon_empty_card_list_breaks_and_returns_empty():
    """
    [Local] search utils: verifies playwright amazon empty card list breaks and returns empty.

    Scenario:
        wait_for_selector resolves but the card locator is empty -> break with no books.

    Boundaries:
        - see test body

    On failure, first check:
        - see test body
    """
    page = FakePage(locators={AMAZON_CARD: FakeLocator([])}, title="Amazon")
    browser = FakeBrowser(page=page)

    result = await get_search_results_via_playwright(browser, "amazon", "python")

    assert result == []


async def test_playwright_amazon_navigation_failure_returns_empty(monkeypatch):
    """
    [Local] search utils: verifies playwright amazon navigation failure returns empty.

    Scenario:
        A goto() failure raises PlaywrightError, which the inner navigation handler logs cleanly and
        then ``continue``s to the next search-page URL instead of touching the dead page. With the
        page cap set to one, the loop then ends and the search returns []. This locks in that a
        failed navigation skips that page and degrades to an empty result rather than crashing the
        caller or scraping a dead page.

    Boundaries:
        - Single page (SEARCH_MAXIMUM_PAGES capped to 1); the multi-page close-on-every-exit
          behaviour is a separate test.

    On failure, first check:
        - The ``except PlaywrightError`` handler in the amazon search loop (clean log + continue).
    """
    sc = copy.deepcopy(search_utils.site_constants)
    sc["amazon"]["SEARCH_MAXIMUM_PAGES"] = 1
    monkeypatch.setattr(search_utils, "site_constants", sc)
    card = {
        "attrs": {"data-asin": "B0ABCDEFGH"},
        "sub": {
            AMAZON_LINK: FakeLocator([{"attrs": {"href": "/dp/B0ABCDEFGH"}}]),
            AMAZON_TITLE: FakeLocator("Recovered Book"),
            AMAZON_AUTHORS: FakeLocator([{"text": "Jane Doe"}]),
        },
    }
    page = FakePage(locators={AMAZON_CARD: FakeLocator([card])}, title="Amazon")
    page.goto.side_effect = PlaywrightTimeoutError("navigation timeout")
    browser = FakeBrowser(page=page)

    result = await get_search_results_via_playwright(browser, "amazon", "python")

    assert result == []


async def test_playwright_amazon_sponsored_without_url_param_skips_card(monkeypatch):
    """
    [Local] search utils: verifies playwright amazon sponsored without url param skips card.

    Scenario:
        A sponsored /sspa/click link with no 'url' param is skipped (continue), not stored.

    Boundaries:
        - see test body

    On failure, first check:
        - see test body
    """
    bad_sponsored = {
        "attrs": {},
        "sub": {
            AMAZON_LINK: FakeLocator([{"attrs": {"href": "/sspa/click?ie=UTF8&foo=1"}}]),
            AMAZON_TITLE: FakeLocator("No URL Sponsored"),
            AMAZON_AUTHORS: FakeLocator([{"text": "Nobody"}]),
        },
    }
    page = FakePage(locators={AMAZON_CARD: FakeLocator([bad_sponsored])}, title="Amazon")
    browser = FakeBrowser(page=page)

    result = await get_search_results_via_playwright(browser, "amazon", "python")

    assert result == []


async def test_playwright_amazon_without_asin_attribute_still_extracts_url(monkeypatch):
    """
    [Local] search utils: verifies playwright amazon without asin attribute still extracts url.

    Scenario:
        When SEARCH_ASIN_ATTRIBUTE is absent, the book is kept with a URL but asin None.

    Boundaries:
        - see test body

    On failure, first check:
        - see test body
    """
    sc = copy.deepcopy(search_utils.site_constants)
    del sc["amazon"]["SEARCH_ASIN_ATTRIBUTE"]
    monkeypatch.setattr(search_utils, "site_constants", sc)

    card = {
        "attrs": {},
        "sub": {
            AMAZON_LINK: FakeLocator([{"attrs": {"href": "/dp/B0ABCDEFGH"}}]),
            AMAZON_TITLE: FakeLocator("No Asin Attr Book"),
            AMAZON_AUTHORS: FakeLocator([{"text": "Jane Doe"}]),
        },
    }
    page = FakePage(locators={AMAZON_CARD: FakeLocator([card])}, title="Amazon")
    browser = FakeBrowser(page=page)

    result = await get_search_results_via_playwright(browser, "amazon", "python")

    assert len(result) == 1
    assert result[0]["url"] == "https://www.amazon.com/dp/B0ABCDEFGH"
    assert result[0]["asin"] is None


async def test_playwright_amazon_publication_date_selector_missing_sets_none(monkeypatch):
    """
    [Local] search utils: verifies playwright amazon publication date selector missing sets none.

    Scenario:
        With no SEARCH_PUBLICATION_DATE selector, publication_date is set to None.

    Boundaries:
        - see test body

    On failure, first check:
        - see test body
    """
    sc = copy.deepcopy(search_utils.site_constants)
    del sc["amazon"]["SEARCH_PUBLICATION_DATE"]
    monkeypatch.setattr(search_utils, "site_constants", sc)

    card = {
        "attrs": {"data-asin": "B0ABCDEFGH"},
        "sub": {
            AMAZON_LINK: FakeLocator([{"attrs": {"href": "/dp/B0ABCDEFGH"}}]),
            AMAZON_TITLE: FakeLocator("No Date Selector"),
            AMAZON_AUTHORS: FakeLocator([{"text": "Jane Doe"}]),
        },
    }
    page = FakePage(locators={AMAZON_CARD: FakeLocator([card])}, title="Amazon")
    browser = FakeBrowser(page=page)

    result = await get_search_results_via_playwright(browser, "amazon", "python")

    assert len(result) == 1
    assert result[0]["publication_date"] is None


async def test_playwright_amazon_publication_date_locator_error_sets_none():
    """
    [Local] search utils: verifies playwright amazon publication date locator error sets none.

    Scenario:
        A publication-date locator that raises is caught; the field becomes None, book kept.

    Boundaries:
        - see test body

    On failure, first check:
        - see test body
    """
    date_selector = search_utils.site_constants["amazon"]["SEARCH_PUBLICATION_DATE"]
    card = {
        "attrs": {"data-asin": "B0ABCDEFGH"},
        "sub": {
            AMAZON_LINK: FakeLocator([{"attrs": {"href": "/dp/B0ABCDEFGH"}}]),
            AMAZON_TITLE: FakeLocator("Date Error Book"),
            AMAZON_AUTHORS: FakeLocator([{"text": "Jane Doe"}]),
            date_selector: FakeLocator(raises=PlaywrightError("bad date locator")),
        },
    }
    page = FakePage(locators={AMAZON_CARD: FakeLocator([card])}, title="Amazon")
    browser = FakeBrowser(page=page)

    result = await get_search_results_via_playwright(browser, "amazon", "python")

    assert len(result) == 1
    assert result[0]["publication_date"] is None


async def test_playwright_amazon_card_error_is_isolated_and_skips_only_that_card():
    """
    [Local] search utils: verifies playwright amazon card error is isolated and skips only that card.

    Scenario:
        A card whose link locator raises is skipped (continue); a good card still returns.

    Boundaries:
        - see test body

    On failure, first check:
        - see test body
    """
    bad_card = {
        "attrs": {"data-asin": "B0BADBADBAD"},
        "sub": {AMAZON_LINK: FakeLocator(raises=RuntimeError("boom"))},
    }
    good_card = {
        "attrs": {"data-asin": "B0GOODGOOD1"},
        "sub": {
            AMAZON_LINK: FakeLocator([{"attrs": {"href": "/dp/B0GOODGOOD1"}}]),
            AMAZON_TITLE: FakeLocator("Survivor Book"),
            AMAZON_AUTHORS: FakeLocator([{"text": "Jane Doe"}]),
        },
    }
    page = FakePage(locators={AMAZON_CARD: FakeLocator([bad_card, good_card])}, title="Amazon")
    browser = FakeBrowser(page=page)

    result = await get_search_results_via_playwright(browser, "amazon", "python")

    assert len(result) == 1
    assert result[0]["title"] == "Survivor Book"


async def test_leanpub_search_no_longer_sleeps_randomly():
    """
    [Local] search utils: verifies the Leanpub API loop throttles via the limiter, not a random sleep.

    Scenario:
        Given a two-page Leanpub search driven through a recording limiter
        When get_leanpub_search_results_via_api runs to completion
        Then one acquire is recorded per page and the module no longer imports ``random`` at all
        (the ad hoc inter-page ``random.uniform`` sleep is gone; inter-page politeness is the
        limiter now, and the UA rotation that was the other ``random`` use moved to browser.py)

    Boundaries:
        - Asserts the replacement of the random sleep by the limiter, not per-field extraction
        - ``asyncio`` is imported again (03.2/03) but only for ``retry_async``'s sleep argument,
          which never fires on this all-success path, so no asyncio-presence assertion is made

    On failure, first check:
        - The removed ``asyncio.sleep(random.uniform(...))`` inter-page sleep was reintroduced, or
          the per-page ``acquire`` before ``client.get`` was dropped
    """
    events: list[str] = []

    class _RecordingLimiter:
        """A limiter whose acquire records each per-page call instead of throttling."""

        async def acquire(self):
            """Record that a token was acquired for this page request."""
            events.append("acquire")

    class _RecordingLimiters:
        """A registry handing out the recording limiter for any URL."""

        def for_url(self, url: str) -> "_RecordingLimiter":
            """Return the recording limiter regardless of host."""
            return _RecordingLimiter()

    page1_books = [_search_book(f"b{i}") for i in range(100)]
    page2_books = [_search_book("x1")]
    responses = [
        FakeResponse(json_data=_search_payload(page1_books)),
        FakeResponse(json_data=_search_payload(page2_books)),
    ]

    with _patch_httpx(responses=responses):
        books = await get_leanpub_search_results_via_api("python", limiters=_RecordingLimiters())

    assert len(books) == 101
    assert events == ["acquire", "acquire"]
    # The module no longer imports ``random`` at all - stronger proof the ad hoc random sleep is gone.
    assert not hasattr(search_utils, "random")


async def test_leanpub_search_retries_on_429_then_succeeds():
    """
    [Local] search utils: verifies a Leanpub search page retries a 429 then succeeds.

    Scenario:
        Given the first httpx GET's raise_for_status raises HTTP 429 and the second returns a
        one-book page payload, with asyncio.sleep patched so no real backoff waits
        When get_leanpub_search_results_via_api runs for the query
        Then the 429 is retried rather than ending pagination, so the one book is returned and the
        client was called twice

    Boundaries:
        - httpx and the backoff sleep are mocked; asserts the retry wrapper, not per-field parsing
        - The single page returns <100 books, so pagination stops after it (no second page fetch)

    On failure, first check:
        - The retry_async wrapper around _fetch_page, or is_transient's HTTPStatusError(429) branch
    """
    request = httpx.Request("GET", "https://leanpub.com/api/v1/bookstore/search")
    response_429 = httpx.Response(429, request=request)
    status_error = httpx.HTTPStatusError("HTTP 429", request=request, response=response_429)
    responses = [
        FakeResponse(raise_status_exc=status_error),
        FakeResponse(json_data=_search_payload([_search_book("b1")])),
    ]

    with _patch_httpx(responses=responses), patch.object(search_utils.asyncio, "sleep", new=AsyncMock()):
        books = await get_leanpub_search_results_via_api("python", limiters=_fast_limiters())

    assert len(books) == 1
    assert books[0]["title"] == "A Book"


# ---------------------------------------------------------------------------
# Amazon search provider refactor (task 03.3/02): pagination, pure helpers, rating
# ---------------------------------------------------------------------------


def _amazon_card(
    asin: str, title: str = "A Book", href: Optional[str] = None, sub_extra: Optional[dict] = None
) -> dict:
    """Build one amazon search-card match dict (data-asin attr + title/authors/link sub-locators).

    :param asin: The card's ``data-asin`` value.
    :param title: The card title text.
    :param href: The detail-link href; defaults to a ``/dp/<asin>`` path.
    :param sub_extra: Extra ``selector -> FakeLocator`` entries merged into the card's sub-locators.
    :returns: A match dict consumable by ``FakeLocator`` as one search-result card.
    """
    sub = {
        AMAZON_LINK: FakeLocator([{"attrs": {"href": href if href is not None else f"/dp/{asin}/ref=sr"}}]),
        AMAZON_TITLE: FakeLocator(title),
        AMAZON_AUTHORS: FakeLocator([{"text": "Jane Doe"}]),
    }
    if sub_extra:
        sub.update(sub_extra)
    return {"text": "", "attrs": {"data-asin": asin}, "sub": sub}


def _amazon_page(card: dict, *, has_next: bool, next_disabled: bool = False) -> FakePage:
    """Build a FakePage serving one amazon card, optionally with an enabled/disabled next control.

    :param card: The single card match dict (see :func:`_amazon_card`).
    :param has_next: Whether a ``.s-pagination-next`` element is present at all.
    :param next_disabled: Whether that element carries the ``s-pagination-disabled`` marker.
    :returns: A :class:`FakePage` wired for the amazon search path.
    """
    locators = {AMAZON_CARD: FakeLocator([card])}
    if has_next:
        cls = "s-pagination-item s-pagination-next" + (" s-pagination-disabled" if next_disabled else "")
        locators[AMAZON_NEXT] = FakeLocator([{"attrs": {"class": cls}}])
    return FakePage(locators=locators, title="Amazon")


async def test_amazon_search_fetches_exactly_max_pages():
    """
    [Local] search utils: verifies amazon pagination fetches exactly max_pages (inclusive) pages.

    Scenario:
        Given a page that always exposes one card and an enabled next-page control
        When get_search_results_via_playwright runs with max_pages=2
        Then exactly two pages are scraped (one book each, numbered 1 then 2), proving the loop
        paginates 1..max_pages INCLUSIVE (the old range(1, MAX) off-by-one fetched one fewer).

    Boundaries:
        - The next control stays enabled, so only the max_pages cap stops the loop; the
          disabled-next early stop is a separate test.

    On failure, first check:
        - The ``for page_num in range(1, effective_max + 1)`` loop bound in the amazon case.
    """
    page = _amazon_page(_amazon_card("B0AAAAAAA1"), has_next=True)
    browser = FakeBrowser(page=page)

    result = await get_search_results_via_playwright(browser, "amazon", "python", max_pages=2)

    assert len(result) == 2
    assert [b["search_page_number"] for b in result] == [1, 2]
    assert all(b["asin"] == "B0AAAAAAA1" for b in result)


async def test_amazon_search_stops_on_disabled_next_button():
    """
    [Local] search utils: verifies pagination stops when the next-page control is disabled.

    Scenario:
        Given a first page whose ``.s-pagination-next`` carries the ``s-pagination-disabled`` marker
        When get_search_results_via_playwright runs with a generous max_pages=5
        Then only page 1 is scraped (one book) because the disabled next control ends pagination
        before the cap is reached.

    Boundaries:
        - A disabled control (not merely absent) is the terminal signal here; the absent-control
          case is covered by the happy-path/single-page tests.

    On failure, first check:
        - ``_amazon_has_next_page``'s ``s-pagination-disabled`` / ``aria-disabled`` handling.
    """
    page = _amazon_page(_amazon_card("B0AAAAAAA1"), has_next=True, next_disabled=True)
    browser = FakeBrowser(page=page)

    result = await get_search_results_via_playwright(browser, "amazon", "python", max_pages=5)

    assert len(result) == 1
    assert result[0]["search_page_number"] == 1


async def test_amazon_search_no_results_page_returns_empty():
    """
    [Local] search utils: verifies a no-results marker page yields no candidates.

    Scenario:
        Given a page whose SEARCH_NO_RESULTS_TEXT selector matches (Amazon's empty-results banner)
        When get_search_results_via_playwright runs
        Then an empty list is returned and the card selector is never waited on (no card scraping).

    Boundaries:
        - Exercises the no-results early stop before wait_for_selector; the exhausted-last-page
          (card-selector timeout) stop is covered by the Chromium search goldens.

    On failure, first check:
        - The SEARCH_NO_RESULTS_TEXT / SEARCH_NO_BOOKS_FOUND marker loop in the amazon case.
    """
    no_results_selector = search_utils.site_constants["amazon"]["SEARCH_NO_RESULTS_TEXT"]
    page = FakePage(
        locators={no_results_selector: FakeLocator([{"text": "No results for python"}])},
        title="Amazon",
    )
    browser = FakeBrowser(page=page)

    result = await get_search_results_via_playwright(browser, "amazon", "python", max_pages=2)

    assert result == []
    page.wait_for_selector.assert_not_called()


async def test_amazon_search_rating_parsed_from_card():
    """
    [Local] search utils: verifies rating and rating_count are parsed from a card when present.

    Scenario:
        Given a card whose SEARCH_RATING element reads "4.6 out of 5 stars" and whose
        SEARCH_RATING_COUNT element reads "1,234"
        When get_search_results_via_playwright runs for amazon
        Then the single result carries rating 4.6 (float) and rating_count 1234 (int).

    Boundaries:
        - Pins the card-scoped rating extraction and numeric parsing; a card with no rating element
          leaves both None (covered implicitly by the other amazon card tests).

    On failure, first check:
        - The SEARCH_RATING / SEARCH_RATING_COUNT reads in the amazon case and parse_rating(_count).
    """
    rating_selector = search_utils.site_constants["amazon"]["SEARCH_RATING"]
    count_selector = search_utils.site_constants["amazon"]["SEARCH_RATING_COUNT"]
    card = _amazon_card(
        "B0RATING001",
        sub_extra={
            rating_selector: FakeLocator("4.6 out of 5 stars"),
            count_selector: FakeLocator("1,234"),
        },
    )
    page = _amazon_page(card, has_next=False)
    browser = FakeBrowser(page=page)

    result = await get_search_results_via_playwright(browser, "amazon", "python")

    assert len(result) == 1
    assert result[0]["rating"] == 4.6
    assert result[0]["rating_count"] == 1234


def test_amazon_sponsored_link_unwrapped_to_dp_url():
    """
    [Unit] search utils: verifies sponsored /sspa/click links unwrap to the real /dp URL and ASIN.

    Scenario:
        Given a sponsored ``/sspa/click?...&url=<encoded /dp path>`` href and a base URL
        When unwrap_amazon_detail_url and extract_amazon_asin run
        Then the decoded absolute product URL is returned and the 10-char ASIN is recovered from it;
        a plain href joins onto the base URL; a sponsored link with no ``url`` param yields None.

    Boundaries:
        - Pure helpers (no browser); this is the unit coverage the spec asks for of the existing
          sponsored-unwrap logic.

    On failure, first check:
        - unwrap_amazon_detail_url's /sspa/click branch and extract_amazon_asin's regex.
    """
    base = "https://www.amazon.com"
    sponsored = "/sspa/click?ie=UTF8&url=%2Fdp%2FB0SPONSOR1%2Fref%3Dabc&qualifier=1"
    unwrapped = unwrap_amazon_detail_url(sponsored, base)
    assert unwrapped == "https://www.amazon.com/dp/B0SPONSOR1/ref=abc"
    assert extract_amazon_asin(unwrapped) == "B0SPONSOR1"

    assert unwrap_amazon_detail_url("/dp/B0PLAIN0001", base) == "https://www.amazon.com/dp/B0PLAIN0001"
    assert unwrap_amazon_detail_url("/sspa/click?ie=UTF8&foo=1", base) is None
    assert extract_amazon_asin(None) is None
    assert extract_amazon_asin("https://www.amazon.com/no-asin-here") is None


def test_amazon_rating_parse_helpers_handle_absent_and_malformed():
    """
    [Unit] search utils: verifies parse_rating / parse_rating_count tolerate absent and junk input.

    Scenario:
        Given assorted rating / count strings (well-formed, comma decimal, parenthesised count,
        empty, and number-less)
        When parse_rating and parse_rating_count run
        Then numbers are extracted as float/int and absent/number-less input yields None.

    Boundaries:
        - Pure parsing; no browser. Guards the "rating when present" contract so a missing rating
          never becomes a fabricated zero.

    On failure, first check:
        - The _RATING_RE / _RATING_COUNT_RE regexes and the None-guards in the parse helpers.
    """
    assert parse_rating("4.6 out of 5 stars") == 4.6
    assert parse_rating("4,6 von 5") == 4.6
    assert parse_rating(None) is None
    assert parse_rating("no stars here") is None
    assert parse_rating_count("1,234") == 1234
    assert parse_rating_count("(1,234 ratings)") == 1234
    assert parse_rating_count(None) is None
    assert parse_rating_count("no digits") is None


def test_amazon_max_pages_clamped_with_log():
    """
    [Unit] search utils: verifies resolve_amazon_max_pages clamps to the site limit and logs it.

    Scenario:
        Given a requested page count above the site limit, below it, and None
        When resolve_amazon_max_pages runs
        Then an over-limit request is clamped to the site limit AND emits one clamp log line
        (console via print_log), an under-limit request is returned unchanged with no clamp log, and
        None falls back to the site limit.

    Boundaries:
        - Pure clamp helper; the pagination loop consuming its result is covered separately.

    On failure, first check:
        - resolve_amazon_max_pages's min()/log branch.
    """
    with patch.object(search_utils, "print_log") as mock_log:
        assert resolve_amazon_max_pages(100, 75, "python") == 75
        clamp_calls = [c for c in mock_log.call_args_list if "clamping" in c.args[0].lower()]
        assert len(clamp_calls) == 1

    with patch.object(search_utils, "print_log") as mock_log:
        assert resolve_amazon_max_pages(2, 75, "python") == 2
        assert not any("clamping" in c.args[0].lower() for c in mock_log.call_args_list)

    assert resolve_amazon_max_pages(None, 75, "python") == 75
