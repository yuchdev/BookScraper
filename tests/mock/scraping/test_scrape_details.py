"""Unit/mock tests for bookscraper.scraping.scrape_details.

Every external collaborator is mocked: Playwright (via playwright_fakes), httpx, and
asyncio.sleep. No network, no browser, no disk.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from bookscraper.scraping import scrape_details
from bookscraper.scraping.scrape_details import (
    get_leanpub_book_details,
    route_handler,
    scrape_book,
)

from .playwright_fakes import (
    FakeBrowser,
    FakeLocator,
    FakePage,
    FakeResponse,
    build_async_client_factory,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

LEANPUB_URL = "https://leanpub.com/api/v1/cache/books/example.json"


def _leanpub_payload(
    about="<p>Hello</p><ul><li>Item one</li><li>Item two</li></ul>",
    last_published_at="2023-05-01T00:00:00Z",
    slug="example-book",
):
    return {
        "data": {
            "id": "book-123",
            "attributes": {
                "title": "Example Book",
                "slug": slug,
                "about_the_book": about,
                "last_published_at": last_published_at,
                "categories": [{"name": "Python"}],
            },
            "relationships": {"accepted_authors": {"data": [{"id": "a1"}]}},
        },
        "included": [
            {"type": "Author", "id": "a1", "attributes": {"name": "Jane Doe"}},
        ],
    }


def _patch_httpx(responses=None, get_side_effect=None):
    """Patch scrape_details.httpx.AsyncClient; returns the patch context manager."""
    factory, _client = build_async_client_factory(responses=responses, get_side_effect=get_side_effect)
    return patch.object(scrape_details.httpx, "AsyncClient", factory)


def _amazon_page(title="Great Python Book - Amazon"):
    """A FakePage configured for a successful amazon detail scrape."""
    locators = {
        "#productTitle": FakeLocator("  Great Python Book  "),
        ".author a.a-link-normal": FakeLocator([{"text": "Jane Doe"}, {"text": "John Smith"}]),
        "#rpi-attribute-book_details-isbn10 .rpi-attribute-value span": FakeLocator("1234567890"),
        "#rpi-attribute-book_details-isbn13 .rpi-attribute-value span": FakeLocator("978-1234567890"),
        "#bookDescription_feature_div .a-expander-content": FakeLocator("  A great book about Python.  "),
    }
    return FakePage(locators=locators, title=title)


# ---------------------------------------------------------------------------
# route_handler
# ---------------------------------------------------------------------------


async def test_route_handler_allows_document_requests():
    route = MagicMock()
    route.request.resource_type = "document"
    route.continue_ = AsyncMock()
    route.abort = AsyncMock()

    await route_handler(route)

    route.continue_.assert_awaited_once()
    route.abort.assert_not_called()


@pytest.mark.parametrize("resource_type", ["image", "font", "stylesheet", "script"])
async def test_route_handler_aborts_non_document_requests(resource_type):
    route = MagicMock()
    route.request.resource_type = resource_type
    route.continue_ = AsyncMock()
    route.abort = AsyncMock()

    await route_handler(route)

    route.abort.assert_awaited_once()
    route.continue_.assert_not_called()


# ---------------------------------------------------------------------------
# get_leanpub_book_details
# ---------------------------------------------------------------------------


async def test_get_leanpub_book_details_happy_path():
    response = FakeResponse(json_data=_leanpub_payload())
    with _patch_httpx(responses=[response]):
        result = await get_leanpub_book_details(LEANPUB_URL)

    assert result is not None
    assert result["title"] == "Example Book"
    assert result["book_id"] == "book-123"
    assert result["slug"] == "example-book"
    assert result["authors"] == ["Jane Doe"]
    assert result["categories"] == ["Python"]
    assert result["last_published_at"] == "2023-05-01"
    assert result["site"] == "leanpub.com"
    assert isinstance(result["hash"], str) and result["hash"]


async def test_get_leanpub_book_details_cleans_html_description():
    response = FakeResponse(json_data=_leanpub_payload())
    with _patch_httpx(responses=[response]):
        result = await get_leanpub_book_details(LEANPUB_URL)

    about = result["about_the_book"]
    # All markup tags stripped.
    assert "<" not in about and ">" not in about
    # Paragraph/list breaks collapsed - never three+ consecutive newlines.
    assert "\n\n\n" not in about
    # Content survived the cleaning.
    assert "Hello" in about
    assert "Item one" in about
    assert "Item two" in about


async def test_get_leanpub_book_details_missing_data_key_returns_none():
    response = FakeResponse(json_data={"meta": "no data here"})
    with _patch_httpx(responses=[response]):
        result = await get_leanpub_book_details(LEANPUB_URL)

    assert result is None


async def test_get_leanpub_book_details_malformed_date_yields_none_date():
    payload = _leanpub_payload(last_published_at="not-a-real-date")
    response = FakeResponse(json_data=payload)
    with _patch_httpx(responses=[response]):
        result = await get_leanpub_book_details(LEANPUB_URL)

    assert result is not None
    assert result["last_published_at"] is None
    # Bad date must not blow up hashing.
    assert isinstance(result["hash"], str) and result["hash"]


async def test_get_leanpub_book_details_http_status_error_returns_none():
    request = httpx.Request("GET", LEANPUB_URL)
    resp = httpx.Response(404, request=request, text="not found")
    status_exc = httpx.HTTPStatusError("boom", request=request, response=resp)
    response = FakeResponse(raise_status_exc=status_exc)
    with _patch_httpx(responses=[response]):
        result = await get_leanpub_book_details(LEANPUB_URL)

    assert result is None


async def test_get_leanpub_book_details_request_error_returns_none():
    request = httpx.Request("GET", LEANPUB_URL)
    exc = httpx.RequestError("network down", request=request)
    with _patch_httpx(get_side_effect=[exc]):
        result = await get_leanpub_book_details(LEANPUB_URL)

    assert result is None


async def test_get_leanpub_book_details_json_decode_error_returns_none():
    decode_exc = json.JSONDecodeError("Expecting value", "", 0)
    response = FakeResponse(json_exc=decode_exc, text="<html>not json</html>")
    with _patch_httpx(responses=[response]):
        result = await get_leanpub_book_details(LEANPUB_URL)

    assert result is None


async def test_get_leanpub_book_details_generic_exception_returns_none():
    with _patch_httpx(get_side_effect=[RuntimeError("unexpected")]):
        result = await get_leanpub_book_details(LEANPUB_URL)

    assert result is None


# ---------------------------------------------------------------------------
# scrape_book
# ---------------------------------------------------------------------------


async def test_scrape_book_leanpub_short_circuits_to_api_helper():
    sentinel = {"title": "From API", "hash": "abc"}
    page = FakePage()
    browser = FakeBrowser(page=page)

    with patch.object(
        scrape_details,
        "get_leanpub_book_details",
        new=AsyncMock(return_value=sentinel),
    ) as mock_details:
        result = await scrape_book("https://leanpub.com/x", browser, "leanpub")

    mock_details.assert_awaited_once_with("https://leanpub.com/x")
    assert result is sentinel
    browser.new_page.assert_awaited_once()


async def test_scrape_book_site_without_base_url_returns_failed():
    # packtpub has no BASE_URL key at all -> KeyError -> caught -> retried -> FAILED.
    page = FakePage(title="Some Packt Book")
    browser = FakeBrowser(page=page)

    with patch.object(scrape_details.asyncio, "sleep", new=AsyncMock()):
        result = await scrape_book("/some/book", browser, "packtpub")

    assert result == (None, "FAILED")


async def test_scrape_book_detects_404_page():
    # amazon's 404_PAGE_TITLE is "page not found".
    page = FakePage(title="Sorry - Page Not Found")
    browser = FakeBrowser(page=page)

    result = await scrape_book("https://www.amazon.com/dp/B0ABCDEFGH/", browser, "amazon")

    assert result == (None, "FAILED")
    page.close.assert_awaited()


async def test_scrape_book_amazon_happy_path_returns_full_book():
    url = "https://www.amazon.com/dp/B0ABCDEFGH/ref=sr_1_1"
    page = _amazon_page()
    browser = FakeBrowser(page=page)

    result = await scrape_book(url, browser, "amazon")

    assert isinstance(result, dict)
    assert result["title"] == "Great Python Book"
    assert result["authors"] == ["Jane Doe", "John Smith"]
    assert result["isbn10"] == "1234567890"
    assert result["isbn13"] == "978-1234567890"
    assert result["description"] == "A great book about Python."
    assert result["asin"] == "B0ABCDEFGH"
    assert result["site"] == "amazon"
    assert result["url"] == url
    assert isinstance(result["hash"], str) and result["hash"]


async def test_scrape_book_amazon_missing_isbn_defaults_to_na():
    url = "https://www.amazon.com/dp/B0ABCDEFGH/"
    # No ISBN selectors configured -> empty locators -> "N/A" default.
    page = FakePage(
        locators={
            "#productTitle": FakeLocator("Title Only"),
            ".author a.a-link-normal": FakeLocator([{"text": "Solo Author"}]),
            "#bookDescription_feature_div .a-expander-content": FakeLocator("Desc"),
        },
        title="Title Only - Amazon",
    )
    browser = FakeBrowser(page=page)

    result = await scrape_book(url, browser, "amazon")

    assert isinstance(result, dict)
    assert result["isbn10"] == "N/A"
    assert result["isbn13"] == "N/A"


async def test_scrape_book_duplicate_hash_returns_duplicate():
    url = "https://www.amazon.com/dp/B0ABCDEFGH/"
    page = _amazon_page()
    browser = FakeBrowser(page=page)

    storage = MagicMock()
    storage.book_exists_by_hash = MagicMock(return_value=True)

    result = await scrape_book(url, browser, "amazon", storage_backend=storage)

    assert result == (None, "DUPLICATE")
    storage.book_exists_by_hash.assert_called_once()
    page.close.assert_awaited()


async def test_scrape_book_non_duplicate_returns_book():
    url = "https://www.amazon.com/dp/B0ABCDEFGH/"
    page = _amazon_page()
    browser = FakeBrowser(page=page)

    storage = MagicMock()
    storage.book_exists_by_hash = MagicMock(return_value=False)

    result = await scrape_book(url, browser, "amazon", storage_backend=storage)

    assert isinstance(result, dict)
    assert result["title"] == "Great Python Book"
    # The hash actually checked against the backend is the book's own hash, not "".
    storage.book_exists_by_hash.assert_called_once_with(result["hash"])
    assert result["hash"] != ""


async def test_scrape_book_retries_three_times_on_timeout_then_failed():
    page = _amazon_page()
    page.goto = AsyncMock(side_effect=PlaywrightTimeoutError("nav timeout"))
    browser = FakeBrowser(page=page)

    with patch.object(scrape_details.asyncio, "sleep", new=AsyncMock()) as mock_sleep:
        result = await scrape_book("https://www.amazon.com/dp/B0ABCDEFGH/", browser, "amazon")

    assert result == (None, "FAILED")
    assert page.goto.await_count == 3
    assert browser.new_page.await_count == 3
    # Sleeps only between attempts (after attempts 1 and 2, not after the last).
    assert mock_sleep.await_count == 2


async def test_scrape_book_retries_three_times_on_generic_exception_then_failed():
    page = _amazon_page()
    page.goto = AsyncMock(side_effect=ValueError("boom"))
    browser = FakeBrowser(page=page)

    with patch.object(scrape_details.asyncio, "sleep", new=AsyncMock()) as mock_sleep:
        result = await scrape_book("https://www.amazon.com/dp/B0ABCDEFGH/", browser, "amazon")

    assert result == (None, "FAILED")
    assert page.goto.await_count == 3
    assert mock_sleep.await_count == 2
