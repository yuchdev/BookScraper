"""Unit/mock tests for bookscraper.scraping.search_utils.

The Leanpub JSON-API path is covered thoroughly. The Playwright/selector-heavy
``get_search_results_via_playwright`` (the ~150-line amazon ``match/case`` branch) is
covered at a pragmatic level per the project's coverage plan - see the module-level note
near those tests for exactly which deep DOM branches are and are not exercised.
"""

import copy
import json
from unittest.mock import AsyncMock, patch

import httpx
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from bookscraper.scraping import search_utils
from bookscraper.scraping.search_utils import (
    get_leanpub_search_results_via_api,
    get_search_results_via_playwright,
)

from .playwright_fakes import (
    FakeBrowser,
    FakeLocator,
    FakePage,
    FakeResponse,
    build_async_client_factory,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _search_book(book_id, title="A Book", slug="a-book", author_id="a1"):
    return {
        "id": book_id,
        "attributes": {"title": title, "slug": slug},
        "relationships": {"accepted_authors": {"data": [{"id": author_id}]}},
    }


def _search_payload(books, author_name="Jane"):
    return {
        "data": books,
        "included": [
            {"type": "SimpleAuthor", "id": "a1", "attributes": {"name": author_name}},
        ],
    }


def _patch_httpx(responses=None, get_side_effect=None):
    factory, _client = build_async_client_factory(responses=responses, get_side_effect=get_side_effect)
    return patch.object(search_utils.httpx, "AsyncClient", factory)


def _no_sleep():
    return patch.object(search_utils.asyncio, "sleep", new=AsyncMock())


# ---------------------------------------------------------------------------
# get_leanpub_search_results_via_api
# ---------------------------------------------------------------------------


async def test_leanpub_api_single_short_page_stops_after_page_one():
    """
    [Local] search utils: verifies leanpub api single short page stops after page one.

    Scenario:
        Given test inputs and fixtures for leanpub api single short page stops after page one
        When test_leanpub_api_single_short_page_stops_after_page_one executes the target flow
        Then the expected outcome for leanpub api single short page stops after page one is confirmed.

    Boundaries:
        - Focus: leanpub api single short page stops after page one
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to leanpub api single short page stops after page one
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search utils
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
    [Local] search utils: verifies leanpub api full page then short page combines results.

    Scenario:
        Given test inputs and fixtures for leanpub api full page then short page combines results
        When test_leanpub_api_full_page_then_short_page_combines_results executes the target flow
        Then the expected outcome for leanpub api full page then short page combines results is confirmed.

    Boundaries:
        - Focus: leanpub api full page then short page combines results
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to leanpub api full page then short page combines results
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search utils
    """
    page1_books = [_search_book(f"b{i}", title=f"Book {i}") for i in range(100)]
    page2_books = [_search_book("x1"), _search_book("x2")]
    responses = [
        FakeResponse(json_data=_search_payload(page1_books)),
        FakeResponse(json_data=_search_payload(page2_books)),
    ]
    with _patch_httpx(responses=responses), _no_sleep():
        books = await get_leanpub_search_results_via_api("python")

    assert len(books) == 102


async def test_leanpub_api_empty_data_returns_empty_immediately():
    """
    [Local] search utils: verifies leanpub api empty data returns empty immediately.

    Scenario:
        Given test inputs and fixtures for leanpub api empty data returns empty immediately
        When test_leanpub_api_empty_data_returns_empty_immediately executes the target flow
        Then the expected outcome for leanpub api empty data returns empty immediately is confirmed.

    Boundaries:
        - Focus: leanpub api empty data returns empty immediately
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to leanpub api empty data returns empty immediately
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search utils
    """
    response = FakeResponse(json_data={"data": [], "included": []})
    with _patch_httpx(responses=[response]):
        books = await get_leanpub_search_results_via_api("python")

    assert books == []


async def test_leanpub_api_request_error_breaks_and_returns_accumulated():
    """
    [Local] search utils: verifies leanpub api request error breaks and returns accumulated.

    Scenario:
        Given test inputs and fixtures for leanpub api request error breaks and returns accumulated
        When test_leanpub_api_request_error_breaks_and_returns_accumulated executes the target flow
        Then the expected outcome for leanpub api request error breaks and returns accumulated is confirmed.

    Boundaries:
        - Focus: leanpub api request error breaks and returns accumulated
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to leanpub api request error breaks and returns accumulated
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search utils
    """
    request = httpx.Request("GET", "https://leanpub.com/api")
    exc = httpx.RequestError("network down", request=request)
    with _patch_httpx(get_side_effect=[exc]):
        books = await get_leanpub_search_results_via_api("python")

    assert books == []


async def test_leanpub_api_request_error_on_second_page_keeps_first_page():
    """
    [Local] search utils: verifies leanpub api request error on second page keeps first page.

    Scenario:
        Given test inputs and fixtures for leanpub api request error on second page keeps first page
        When test_leanpub_api_request_error_on_second_page_keeps_first_page executes the target flow
        Then the expected outcome for leanpub api request error on second page keeps first page is confirmed.

    Boundaries:
        - Focus: leanpub api request error on second page keeps first page
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to leanpub api request error on second page keeps first page
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search utils
    """
    page1_books = [_search_book(f"b{i}") for i in range(100)]
    request = httpx.Request("GET", "https://leanpub.com/api")
    exc = httpx.RequestError("boom", request=request)
    responses = [FakeResponse(json_data=_search_payload(page1_books)), exc]
    with _patch_httpx(get_side_effect=responses), _no_sleep():
        books = await get_leanpub_search_results_via_api("python")

    assert len(books) == 100


async def test_leanpub_api_json_decode_error_breaks_and_returns_empty():
    """
    [Local] search utils: verifies leanpub api json decode error breaks and returns empty.

    Scenario:
        Given test inputs and fixtures for leanpub api json decode error breaks and returns empty
        When test_leanpub_api_json_decode_error_breaks_and_returns_empty executes the target flow
        Then the expected outcome for leanpub api json decode error breaks and returns empty is confirmed.

    Boundaries:
        - Focus: leanpub api json decode error breaks and returns empty
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to leanpub api json decode error breaks and returns empty
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search utils
    """
    decode_exc = json.JSONDecodeError("Expecting value", "", 0)
    response = FakeResponse(json_exc=decode_exc, text="<html>nope</html>")
    with _patch_httpx(responses=[response]):
        books = await get_leanpub_search_results_via_api("python")

    assert books == []


async def test_leanpub_api_generic_exception_breaks_and_returns_empty():
    """
    [Local] search utils: verifies leanpub api generic exception breaks and returns empty.

    Scenario:
        Given test inputs and fixtures for leanpub api generic exception breaks and returns empty
        When test_leanpub_api_generic_exception_breaks_and_returns_empty executes the target flow
        Then the expected outcome for leanpub api generic exception breaks and returns empty is confirmed.

    Boundaries:
        - Focus: leanpub api generic exception breaks and returns empty
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to leanpub api generic exception breaks and returns empty
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search utils
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


async def test_playwright_unknown_site_returns_empty_without_touching_browser():
    """
    [Local] search utils: verifies playwright unknown site returns empty without touching browser.

    Scenario:
        Given test inputs and fixtures for playwright unknown site returns empty without touching browser
        When test_playwright_unknown_site_returns_empty_without_touching_browser executes the target flow
        Then the expected outcome for playwright unknown site returns empty without touching browser is confirmed.

    Boundaries:
        - Focus: playwright unknown site returns empty without touching browser
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to playwright unknown site returns empty without touching browser
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search utils
    """
    browser = FakeBrowser()
    result = await get_search_results_via_playwright(browser, "nonexistent-site", "python")

    assert result == []
    browser.new_page.assert_not_called()


async def test_playwright_missing_search_base_url_returns_empty(monkeypatch):
    # Truthy site config (so the site_config guard passes) but no SEARCH_BASE_URL key.
    """
    [Local] search utils: verifies playwright missing search base url returns empty.

    Scenario:
        Given test inputs and fixtures for playwright missing search base url returns empty
        When test_playwright_missing_search_base_url_returns_empty executes the target flow
        Then the expected outcome for playwright missing search base url returns empty is confirmed.

    Boundaries:
        - Focus: playwright missing search base url returns empty
        - Fixtures/params: monkeypatch
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to playwright missing search base url returns empty
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search utils
    """
    monkeypatch.setattr(search_utils, "site_constants", {"fakesite": {"FOO": "bar"}})
    browser = FakeBrowser()

    result = await get_search_results_via_playwright(browser, "fakesite", "python")

    assert result == []
    browser.new_page.assert_not_called()


async def test_playwright_amazon_happy_path_extracts_one_book():
    """
    [Local] search utils: verifies playwright amazon happy path extracts one book.

    Scenario:
        Given test inputs and fixtures for playwright amazon happy path extracts one book
        When test_playwright_amazon_happy_path_extracts_one_book executes the target flow
        Then the expected outcome for playwright amazon happy path extracts one book is confirmed.

    Boundaries:
        - Focus: playwright amazon happy path extracts one book
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to playwright amazon happy path extracts one book
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search utils
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


async def test_playwright_amazon_no_books_found_short_circuits(monkeypatch):
    # amazon has no SEARCH_NO_BOOKS_FOUND in real config; inject one for this case.
    """
    [Local] search utils: verifies playwright amazon no books found short circuits.

    Scenario:
        Given test inputs and fixtures for playwright amazon no books found short circuits
        When test_playwright_amazon_no_books_found_short_circuits executes the target flow
        Then the expected outcome for playwright amazon no books found short circuits is confirmed.

    Boundaries:
        - Focus: playwright amazon no books found short circuits
        - Fixtures/params: monkeypatch
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to playwright amazon no books found short circuits
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search utils
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
    [Local] search utils: verifies playwright amazon sponsored link extracts real url and asin.

    Scenario:
        Given test inputs and fixtures for playwright amazon sponsored link extracts real url and asin
        When test_playwright_amazon_sponsored_link_extracts_real_url_and_asin executes the target flow
        Then the expected outcome for playwright amazon sponsored link extracts real url and asin is confirmed.

    Boundaries:
        - Focus: playwright amazon sponsored link extracts real url and asin
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to playwright amazon sponsored link extracts real url and asin
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search utils
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
    [Local] search utils: verifies playwright non amazon default case returns empty cleanly.

    Scenario:
        Given test inputs and fixtures for playwright non amazon default case returns empty cleanly
        When test_playwright_non_amazon_default_case_returns_empty_cleanly executes the target flow
        Then the expected outcome for playwright non amazon default case returns empty cleanly is confirmed.

    Boundaries:
        - Focus: playwright non amazon default case returns empty cleanly
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to playwright non amazon default case returns empty cleanly
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search utils
    """
    browser = FakeBrowser()

    result = await get_search_results_via_playwright(browser, "leanpub", "python")

    assert result == []
    # The default branch never creates a page.
    browser.new_page.assert_not_called()


async def test_playwright_amazon_empty_card_list_breaks_and_returns_empty():
    """wait_for_selector resolves but the card locator is empty -> break with no books."""
    page = FakePage(locators={AMAZON_CARD: FakeLocator([])}, title="Amazon")
    browser = FakeBrowser(page=page)

    result = await get_search_results_via_playwright(browser, "amazon", "python")

    assert result == []


async def test_playwright_amazon_navigation_failure_returns_empty():
    """Characterization: a goto() failure aborts the amazon search and returns [].

    The inner handler for a navigation error calls ``logger.error(msg, "error")`` (search_utils
    line 249) with a stray positional ``"error"`` - a real product bug (subtask 03.3): the
    logging layer rejects the extra arg, the exception escapes the inner ``except`` into the
    function's outer handler, and the run ends with whatever was collected so far (nothing, since
    the failure is on page 1). This locks in that a failed navigation degrades to an empty result
    rather than crashing the caller; a 03.3 fix (drop the stray arg) would surface as a diff here.
    """
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
    """A sponsored /sspa/click link with no 'url' param is skipped (continue), not stored."""
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
    """When SEARCH_ASIN_ATTRIBUTE is absent, the book is kept with a URL but asin None."""
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
    """With no SEARCH_PUBLICATION_DATE selector, publication_date is set to None."""
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
    """A publication-date locator that raises is caught; the field becomes None, book kept."""
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
    """A card whose link locator raises is skipped (continue); a good card still returns."""
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
