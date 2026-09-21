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
    response = FakeResponse(json_data={"data": [], "included": []})
    with _patch_httpx(responses=[response]):
        books = await get_leanpub_search_results_via_api("python")

    assert books == []


async def test_leanpub_api_request_error_breaks_and_returns_accumulated():
    request = httpx.Request("GET", "https://leanpub.com/api")
    exc = httpx.RequestError("network down", request=request)
    with _patch_httpx(get_side_effect=[exc]):
        books = await get_leanpub_search_results_via_api("python")

    assert books == []


async def test_leanpub_api_request_error_on_second_page_keeps_first_page():
    page1_books = [_search_book(f"b{i}") for i in range(100)]
    request = httpx.Request("GET", "https://leanpub.com/api")
    exc = httpx.RequestError("boom", request=request)
    responses = [FakeResponse(json_data=_search_payload(page1_books)), exc]
    with _patch_httpx(get_side_effect=responses), _no_sleep():
        books = await get_leanpub_search_results_via_api("python")

    assert len(books) == 100


async def test_leanpub_api_json_decode_error_breaks_and_returns_empty():
    decode_exc = json.JSONDecodeError("Expecting value", "", 0)
    response = FakeResponse(json_exc=decode_exc, text="<html>nope</html>")
    with _patch_httpx(responses=[response]):
        books = await get_leanpub_search_results_via_api("python")

    assert books == []


async def test_leanpub_api_generic_exception_breaks_and_returns_empty():
    with _patch_httpx(get_side_effect=[RuntimeError("unexpected")]):
        books = await get_leanpub_search_results_via_api("python")

    assert books == []


# ---------------------------------------------------------------------------
# get_search_results_via_playwright
#
# Coverage note: the amazon happy path, the sponsored-link URL/ASIN extraction, the
# "no books found" short-circuit, the two early-return guards, and the non-amazon
# default (pass) branch are exercised below. Deliberately NOT covered (documented gap,
# allowed by the coverage plan): multi-page pagination beyond a single page, the
# per-card publication-date selector branch, and the per-card exception-recovery
# `continue`. These are deep DOM/selector branches with low corpus-integrity risk and
# high fake-DOM setup cost relative to what they verify.
# ---------------------------------------------------------------------------

AMAZON_LINK = "a.a-link-normal.s-line-clamp-2.s-link-style.a-text-normal"
AMAZON_TITLE = "h2.a-size-medium.a-spacing-none.a-color-base.a-text-normal > span"
AMAZON_AUTHORS = "div.a-row.a-size-base.a-color-secondary div.a-row > a.a-link-normal"
AMAZON_CARD = '[data-component-type="s-search-result"]'


async def test_playwright_unknown_site_returns_empty_without_touching_browser():
    browser = FakeBrowser()
    result = await get_search_results_via_playwright(browser, "nonexistent-site", "python")

    assert result == []
    browser.new_page.assert_not_called()


async def test_playwright_missing_search_base_url_returns_empty(monkeypatch):
    # Truthy site config (so the site_config guard passes) but no SEARCH_BASE_URL key.
    monkeypatch.setattr(search_utils, "site_constants", {"fakesite": {"FOO": "bar"}})
    browser = FakeBrowser()

    result = await get_search_results_via_playwright(browser, "fakesite", "python")

    assert result == []
    browser.new_page.assert_not_called()


async def test_playwright_amazon_happy_path_extracts_one_book():
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
    browser = FakeBrowser()

    result = await get_search_results_via_playwright(browser, "leanpub", "python")

    assert result == []
    # The default branch never creates a page.
    browser.new_page.assert_not_called()
