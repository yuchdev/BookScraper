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
from bookscraper.scraping.results import ScrapeResult, ScrapeStatus
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
    """Build a minimal Leanpub JSON detail payload for scrape-details tests."""
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
    """
    [Local] scrape details: verifies that a document request is forwarded, not aborted.

    Scenario:
        Given a mock route whose resource_type is "document"
        When route_handler is called
        Then route.continue_() is awaited once and route.abort() is never called.

    Boundaries:
        - Only the "document" branch is covered; all other resource types are in the
          parametrized abort test.

    On failure, first check:
        - The condition in route_handler that dispatches on resource_type.
    """
    route = MagicMock()
    route.request.resource_type = "document"
    route.continue_ = AsyncMock()
    route.abort = AsyncMock()

    await route_handler(route)

    route.continue_.assert_awaited_once()
    route.abort.assert_not_called()


@pytest.mark.parametrize("resource_type", ["image", "font", "stylesheet", "script"])
async def test_route_handler_aborts_non_document_requests(resource_type):
    """
    [Local] scrape details: verifies that non-document requests are aborted.

    Scenario:
        Given a mock route whose resource_type is one of image/font/stylesheet/script
        When route_handler is called
        Then route.abort() is awaited once and route.continue_() is never called.

    Boundaries:
        - Parametrized over the four non-document resource types used in production;
          "document" itself is covered by the separate allow test.

    On failure, first check:
        - The condition in route_handler that dispatches on resource_type.
    """
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
    """
    [Local] scrape details: verifies get leanpub book details happy path.

    Scenario:
        Given a well-formed Leanpub JSON payload served by the mock httpx client
        When get_leanpub_book_details is called
        Then the result has status SUCCESS and all book fields (title, book_id, slug,
        authors, categories, last_published_at, site, hash) are correctly populated.

    Boundaries:
        - httpx is mocked; no network. Error branches are covered by separate tests.

    On failure, first check:
        - Field extraction logic in get_leanpub_book_details (JSON path, date parsing,
          hash_book call).
    """
    response = FakeResponse(json_data=_leanpub_payload())
    with _patch_httpx(responses=[response]):
        result = await get_leanpub_book_details(LEANPUB_URL)

    assert result.status is ScrapeStatus.SUCCESS
    book = result.book
    assert book["title"] == "Example Book"
    assert book["book_id"] == "book-123"
    assert book["slug"] == "example-book"
    assert book["authors"] == ["Jane Doe"]
    assert book["categories"] == ["Python"]
    assert book["last_published_at"] == "2023-05-01"
    assert book["site"] == "leanpub.com"
    assert isinstance(book["hash"], str) and book["hash"]


async def test_get_leanpub_book_details_cleans_html_description():
    """
    [Local] scrape details: verifies that HTML markup is stripped from about_the_book.

    Scenario:
        Given a Leanpub payload whose about_the_book contains <p> and <ul>/<li> tags
        When get_leanpub_book_details is called
        Then all angle-bracket tags are removed, three-or-more consecutive newlines do
        not appear, and the plain text content is preserved.

    Boundaries:
        - Only the description-cleaning pipeline is tested; other fields are covered
          by the happy-path test.

    On failure, first check:
        - The HTML-to-text conversion step and newline-collapse logic.
    """
    response = FakeResponse(json_data=_leanpub_payload())
    with _patch_httpx(responses=[response]):
        result = await get_leanpub_book_details(LEANPUB_URL)

    about = result.book["about_the_book"]
    # All markup tags stripped.
    assert "<" not in about and ">" not in about
    # Paragraph/list breaks collapsed - never three+ consecutive newlines.
    assert "\n\n\n" not in about
    # Content survived the cleaning.
    assert "Hello" in about
    assert "Item one" in about
    assert "Item two" in about


async def test_get_leanpub_book_details_missing_data_key_returns_failed():
    """
    [Local] scrape details: verifies that a response without a "data" key returns FAILED.

    Scenario:
        Given a mock HTTP response whose JSON body has no top-level "data" key
        When get_leanpub_book_details is called
        Then the result has status FAILED, book is None, and url equals the request URL.

    Boundaries:
        - Covers the KeyError/missing-data branch only; other error types are separate.

    On failure, first check:
        - The guard that checks for "data" in the response JSON.
    """
    response = FakeResponse(json_data={"meta": "no data here"})
    with _patch_httpx(responses=[response]):
        result = await get_leanpub_book_details(LEANPUB_URL)

    assert result.status is ScrapeStatus.FAILED
    assert result.book is None
    assert result.url == LEANPUB_URL


async def test_get_leanpub_book_details_malformed_date_yields_none_date():
    """
    [Local] scrape details: verifies that an unparseable date string yields None without failing.

    Scenario:
        Given a Leanpub payload with last_published_at set to "not-a-real-date"
        When get_leanpub_book_details is called
        Then the result status is SUCCESS, last_published_at is None, and hash is still
        computed (a bad date must not abort hashing).

    Boundaries:
        - Covers only the date-parse failure branch; other error paths are separate.

    On failure, first check:
        - The date-parsing and hash_book calls in get_leanpub_book_details.
    """
    payload = _leanpub_payload(last_published_at="not-a-real-date")
    response = FakeResponse(json_data=payload)
    with _patch_httpx(responses=[response]):
        result = await get_leanpub_book_details(LEANPUB_URL)

    assert result.status is ScrapeStatus.SUCCESS
    assert result.book["last_published_at"] is None
    # Bad date must not blow up hashing.
    assert isinstance(result.book["hash"], str) and result.book["hash"]


async def test_leanpub_failure_returns_failed_result_not_none():
    """
    [Local] scrape details: verifies leanpub failure returns failed result not none.

    Scenario:
        a Leanpub HTTP error that used to return a bare ``None``.

    Boundaries:
        - the httpx client raises an ``HTTPStatusError``; the helper must now surface a typed ``ScrapeResult`` (FAILED, with the real url and a secret-free error) rather than ``None``, which ``scrape_urls`` once filed as ``UNKNOWN_ERROR`` (defect (d)).

    On failure, first check:
        - a Leanpub failure again returns ``None`` and the opaque-error regression returns.
    """
    request = httpx.Request("GET", LEANPUB_URL)
    resp = httpx.Response(404, request=request, text="not found")
    status_exc = httpx.HTTPStatusError("boom", request=request, response=resp)
    response = FakeResponse(raise_status_exc=status_exc)
    with _patch_httpx(responses=[response]):
        result = await get_leanpub_book_details(LEANPUB_URL)

    assert isinstance(result, ScrapeResult)
    assert result.status is ScrapeStatus.FAILED
    assert result.book is None
    assert result.url == LEANPUB_URL
    assert result.error is not None
    # Only the status code is surfaced, never the raw response body.
    assert "not found" not in result.error


async def test_get_leanpub_book_details_request_error_returns_failed():
    """
    [Local] scrape details: verifies that an httpx.RequestError yields a FAILED result.

    Scenario:
        Given the mock httpx client raises RequestError on .get()
        When get_leanpub_book_details is called
        Then the result has status FAILED and book is None.

    Boundaries:
        - Network-level error only; HTTP status errors and JSON errors are separate.

    On failure, first check:
        - The except httpx.RequestError branch in get_leanpub_book_details.
    """
    request = httpx.Request("GET", LEANPUB_URL)
    exc = httpx.RequestError("network down", request=request)
    with _patch_httpx(get_side_effect=[exc]):
        result = await get_leanpub_book_details(LEANPUB_URL)

    assert result.status is ScrapeStatus.FAILED
    assert result.book is None


async def test_get_leanpub_book_details_json_decode_error_returns_failed():
    """
    [Local] scrape details: verifies that a JSONDecodeError from the response yields FAILED.

    Scenario:
        Given the mock response's .json() raises json.JSONDecodeError
        When get_leanpub_book_details is called
        Then the result has status FAILED and book is None.

    Boundaries:
        - Covers the JSON-parse failure branch; network and missing-key errors are separate.

    On failure, first check:
        - The except json.JSONDecodeError branch in get_leanpub_book_details.
    """
    decode_exc = json.JSONDecodeError("Expecting value", "", 0)
    response = FakeResponse(json_exc=decode_exc, text="<html>not json</html>")
    with _patch_httpx(responses=[response]):
        result = await get_leanpub_book_details(LEANPUB_URL)

    assert result.status is ScrapeStatus.FAILED
    assert result.book is None


async def test_get_leanpub_book_details_generic_exception_returns_failed():
    """
    [Local] scrape details: verifies that an unexpected RuntimeError yields FAILED without crashing.

    Scenario:
        Given the mock httpx client's .get() raises RuntimeError("unexpected")
        When get_leanpub_book_details is called
        Then the result has status FAILED and book is None.

    Boundaries:
        - Covers the broad except Exception fallback; typed errors are in separate tests.

    On failure, first check:
        - The bare except / outer exception handler in get_leanpub_book_details.
    """
    with _patch_httpx(get_side_effect=[RuntimeError("unexpected")]):
        result = await get_leanpub_book_details(LEANPUB_URL)

    assert result.status is ScrapeStatus.FAILED
    assert result.book is None


# ---------------------------------------------------------------------------
# scrape_book
# ---------------------------------------------------------------------------


async def test_scrape_book_leanpub_short_circuits_to_api_helper():
    """
    [Local] scrape details: verifies that a Leanpub URL skips Playwright and calls the API helper.

    Scenario:
        Given a Leanpub URL and get_leanpub_book_details patched to return a sentinel
        When scrape_book is called
        Then the API helper is awaited once with the URL, and browser.new_page is never
        opened (the Playwright loop is bypassed entirely).

    Boundaries:
        - API helper return value is a raw dict sentinel; typed ScrapeResult behavior is
          covered by test_leanpub_dispatch_opens_no_playwright_page.

    On failure, first check:
        - The Leanpub short-circuit guard at the top of scrape_book.
    """
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
    # Leanpub is dispatched before any Playwright page is created.
    browser.new_page.assert_not_awaited()


async def test_scrape_book_site_without_base_url_returns_failed():
    # packtpub has no BASE_URL key at all -> KeyError -> caught -> retried -> FAILED.
    """
    [Local] scrape details: verifies that a missing BASE_URL for a site yields FAILED after retries.

    Scenario:
        Given the "packtpub" site config has no BASE_URL key (KeyError on access)
        When scrape_book is called
        Then the result is FAILED with book=None and site="packtpub" after exhausting
        the retry loop.

    Boundaries:
        - Covers the KeyError/missing-config path; other failure modes are separate.

    On failure, first check:
        - The BASE_URL lookup in scrape_book's inner loop and how KeyError is caught.
    """
    page = FakePage(title="Some Packt Book")
    browser = FakeBrowser(page=page)

    with patch.object(scrape_details.asyncio, "sleep", new=AsyncMock()):
        result = await scrape_book("/some/book", browser, "packtpub")

    assert result.status is ScrapeStatus.FAILED
    assert result.book is None
    assert result.site == "packtpub"


async def test_scrape_book_404_returns_not_found():
    """
    [Local] scrape details: verifies scrape book 404 returns not found.

    Scenario:
        amazon serves a page whose title matches ``404_PAGE_TITLE``.

    Boundaries:
        - the 404 short-circuit fires on the first attempt; the result must be a typed NOT_FOUND (not the old opaque ``(None, "FAILED")``) so ``failed_books.csv`` can tell a dead URL from a broken selector. The page is closed exactly once.

    On failure, first check:
        - a 404 again collapses into FAILED, erasing the distinction subtask 02 adds.
    """
    page = FakePage(title="Sorry - Page Not Found")
    browser = FakeBrowser(page=page)

    result = await scrape_book("https://www.amazon.com/dp/B0ABCDEFGH/", browser, "amazon")

    assert result.status is ScrapeStatus.NOT_FOUND
    assert result.book is None
    assert result.error is not None
    page.close.assert_awaited()


async def test_scrape_book_amazon_happy_path_returns_full_book():
    """
    [Local] scrape details: verifies that a fully populated amazon page yields a complete book.

    Scenario:
        Given a FakePage whose locators cover all amazon selectors
        When scrape_book is called
        Then the result has status SUCCESS and the book dict contains title, authors,
        isbn10, isbn13, description, asin, site, url, and hash.

    Boundaries:
        - No storage backend; duplicate-check behavior is covered by separate tests.

    On failure, first check:
        - The field-extraction selectors for amazon in scrape_book and site_constants.
    """
    url = "https://www.amazon.com/dp/B0ABCDEFGH/ref=sr_1_1"
    page = _amazon_page()
    browser = FakeBrowser(page=page)

    result = await scrape_book(url, browser, "amazon")

    assert result.status is ScrapeStatus.SUCCESS
    book = result.book
    assert book["title"] == "Great Python Book"
    assert book["authors"] == ["Jane Doe", "John Smith"]
    assert book["isbn10"] == "1234567890"
    assert book["isbn13"] == "978-1234567890"
    assert book["description"] == "A great book about Python."
    assert book["asin"] == "B0ABCDEFGH"
    assert book["site"] == "amazon"
    assert book["url"] == url
    assert isinstance(book["hash"], str) and book["hash"]


async def test_scrape_book_amazon_missing_isbn_defaults_to_na():
    """
    [Local] scrape details: verifies that absent ISBN selectors produce "N/A" defaults.

    Scenario:
        Given a FakePage whose locators include no ISBN selectors
        When scrape_book is called for amazon
        Then the result is SUCCESS with isbn10 == "N/A" and isbn13 == "N/A".

    Boundaries:
        - Covers the ISBN-fallback branch only; other field defaults are not asserted.

    On failure, first check:
        - The isbn10/isbn13 default value logic in scrape_book.
    """
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

    assert result.status is ScrapeStatus.SUCCESS
    assert result.book["isbn10"] == "N/A"
    assert result.book["isbn13"] == "N/A"


async def test_scrape_book_duplicate_hash_returns_duplicate():
    """
    [Local] scrape details: verifies that a book already in the backend yields DUPLICATE.

    Scenario:
        Given a storage backend whose book_exists_by_hash returns True
        When scrape_book is called for an amazon URL
        Then the result has status DUPLICATE, book is None, and the page is closed.

    Boundaries:
        - book_exists_by_hash is the only dedup check exercised here.

    On failure, first check:
        - The duplicate-check branch in scrape_book and the call to storage.book_exists_by_hash.
    """
    url = "https://www.amazon.com/dp/B0ABCDEFGH/"
    page = _amazon_page()
    browser = FakeBrowser(page=page)

    storage = MagicMock()
    storage.book_exists_by_hash = MagicMock(return_value=True)

    result = await scrape_book(url, browser, "amazon", storage_backend=storage)

    assert result.status is ScrapeStatus.DUPLICATE
    assert result.book is None
    storage.book_exists_by_hash.assert_called_once()
    page.close.assert_awaited()


async def test_scrape_book_non_duplicate_returns_book():
    """
    [Local] scrape details: verifies that a new book passes the hash check and is returned.

    Scenario:
        Given a storage backend whose book_exists_by_hash returns False
        When scrape_book is called for an amazon URL
        Then the result is SUCCESS and book_exists_by_hash is called with the book's
        actual computed hash (not an empty string).

    Boundaries:
        - Covers the non-duplicate SUCCESS path; the DUPLICATE path is a separate test.

    On failure, first check:
        - The hash computation in scrape_book and the argument passed to book_exists_by_hash.
    """
    url = "https://www.amazon.com/dp/B0ABCDEFGH/"
    page = _amazon_page()
    browser = FakeBrowser(page=page)

    storage = MagicMock()
    storage.book_exists_by_hash = MagicMock(return_value=False)

    result = await scrape_book(url, browser, "amazon", storage_backend=storage)

    assert result.status is ScrapeStatus.SUCCESS
    assert result.book["title"] == "Great Python Book"
    # The hash actually checked against the backend is the book's own hash, not "".
    storage.book_exists_by_hash.assert_called_once_with(result.book["hash"])
    assert result.book["hash"] != ""


async def test_scrape_book_exhausted_timeouts_return_timeout_with_attempt_count():
    """
    [Local] scrape details: verifies scrape book exhausted timeouts return timeout with attempt count.

    Scenario:
        every navigation times out, exhausting all three attempts.

    Boundaries:
        - the final-timeout path must return a typed TIMEOUT (not the old ``(None, "FAILED")``) and record ``attempts == 3`` so the summary and CSV reflect the retry budget that was spent.

    On failure, first check:
        - an all-timeouts run again reads as a generic FAILED with no attempt count.
    """
    page = _amazon_page()
    page.goto = AsyncMock(side_effect=PlaywrightTimeoutError("nav timeout"))
    browser = FakeBrowser(page=page)

    with patch.object(scrape_details.asyncio, "sleep", new=AsyncMock()) as mock_sleep:
        result = await scrape_book("https://www.amazon.com/dp/B0ABCDEFGH/", browser, "amazon")

    assert result.status is ScrapeStatus.TIMEOUT
    assert result.book is None
    assert result.attempts == 3
    assert page.goto.await_count == 3
    assert browser.new_page.await_count == 3
    # Sleeps only between attempts (after attempts 1 and 2, not after the last).
    assert mock_sleep.await_count == 2


async def test_scrape_book_retries_three_times_on_generic_exception_then_failed():
    """
    [Local] scrape details: verifies that three consecutive generic exceptions yield FAILED after 2 sleeps.

    Scenario:
        Given a FakePage whose goto raises ValueError on every attempt
        When scrape_book is called for an amazon URL
        Then the result is FAILED, goto is awaited exactly 3 times, and asyncio.sleep
        is awaited exactly 2 times (between attempts, not after the last).

    Boundaries:
        - Covers the generic-exception retry path; the TimeoutError path (TIMEOUT status)
          is covered by test_scrape_book_exhausted_timeouts_return_timeout_with_attempt_count.

    On failure, first check:
        - The retry loop in scrape_book and the sleep placement between attempts.
    """
    page = _amazon_page()
    page.goto = AsyncMock(side_effect=ValueError("boom"))
    browser = FakeBrowser(page=page)

    with patch.object(scrape_details.asyncio, "sleep", new=AsyncMock()) as mock_sleep:
        result = await scrape_book("https://www.amazon.com/dp/B0ABCDEFGH/", browser, "amazon")

    assert result.status is ScrapeStatus.FAILED
    assert result.book is None
    assert page.goto.await_count == 3
    assert mock_sleep.await_count == 2


# ---------------------------------------------------------------------------
# scrape_book - page lifecycle & leak fixes (subtask 03.0/01)
# ---------------------------------------------------------------------------


async def test_leanpub_dispatch_opens_no_playwright_page():
    """
    [Local] scrape details: verifies leanpub dispatch opens no playwright page.

    Scenario:
        ``scrape_book`` is called for a Leanpub URL.

    Boundaries:
        - exercises only the pre-loop Leanpub dispatch; the httpx detail helper is stubbed, so no network and no Playwright page are involved.

    On failure, first check:
        - the Leanpub branch would again open (and leak) a browser page before short-circuiting to the API helper - defect (a) from the parent task.
    """
    page = FakePage()
    browser = FakeBrowser(page=page)

    with patch.object(
        scrape_details,
        "get_leanpub_book_details",
        new=AsyncMock(return_value={"title": "From API"}),
    ) as mock_details:
        result = await scrape_book("https://leanpub.com/x", browser, "leanpub")

    assert result == {"title": "From API"}
    mock_details.assert_awaited_once_with("https://leanpub.com/x")
    # No Playwright resource is created for Leanpub, so nothing can leak.
    browser.new_page.assert_not_awaited()
    page.close.assert_not_awaited()


async def test_page_closed_exactly_once_on_success():
    """
    [Local] scrape details: verifies page closed exactly once on success.

    Scenario:
        a happy-path Amazon scrape returns a full book dict.

    Boundaries:
        - single attempt, no duplicate check; asserts the page-open and page-close counts match exactly.

    On failure, first check:
        - a page was opened without a matching close (a leak) or closed more than once (spurious ``Target closed`` errors).
    """
    page = _amazon_page()
    browser = FakeBrowser(page=page)

    result = await scrape_book("https://www.amazon.com/dp/B0ABCDEFGH/", browser, "amazon")

    assert result.status is ScrapeStatus.SUCCESS
    assert browser.new_page.await_count == 1
    assert page.close.await_count == 1
    assert browser.new_page.await_count == page.close.await_count


async def test_page_closed_exactly_once_on_duplicate():
    """
    [Local] scrape details: verifies page closed exactly once on duplicate.

    Scenario:
        a scraped book is already present in the storage backend.

    Boundaries:
        - single attempt that returns ``(None, "DUPLICATE")`` from inside the page context.

    On failure, first check:
        - the duplicate short-circuit leaked its page or double-closed it.
    """
    page = _amazon_page()
    browser = FakeBrowser(page=page)
    storage = MagicMock()
    storage.book_exists_by_hash = MagicMock(return_value=True)

    result = await scrape_book("https://www.amazon.com/dp/B0ABCDEFGH/", browser, "amazon", storage_backend=storage)

    assert result.status is ScrapeStatus.DUPLICATE
    assert browser.new_page.await_count == 1
    assert page.close.await_count == 1


async def test_page_closed_exactly_once_on_404():
    """
    [Local] scrape details: verifies page closed exactly once on 404.

    Scenario:
        the target serves a 404 page (title matches ``404_PAGE_TITLE``).

    Boundaries:
        - single attempt that returns ``(None, "FAILED")`` from the 404 short-circuit inside the page context.

    On failure, first check:
        - the 404 branch leaked its page or double-closed it.
    """
    page = FakePage(title="Sorry - Page Not Found")
    browser = FakeBrowser(page=page)

    result = await scrape_book("https://www.amazon.com/dp/B0ABCDEFGH/", browser, "amazon")

    assert result.status is ScrapeStatus.NOT_FOUND
    assert browser.new_page.await_count == 1
    assert page.close.await_count == 1


async def test_page_closed_exactly_once_per_attempt_on_repeated_timeout():
    """
    [Local] scrape details: verifies page closed exactly once per attempt on repeated timeout.

    Scenario:
        every navigation times out, so all three attempts fail.

    Boundaries:
        - three attempts hand out the same fake page; asserts exactly one open and one close per attempt (3 == 3), never a fourth close from a stale post-loop cleanup.

    On failure, first check:
        - the removed post-loop ``if page: await page.close()`` block (or a double-close on the final attempt) would push the close count to 4+.
    """
    page = _amazon_page()
    page.goto = AsyncMock(side_effect=PlaywrightTimeoutError("nav timeout"))
    browser = FakeBrowser(page=page)

    with patch.object(scrape_details.asyncio, "sleep", new=AsyncMock()):
        result = await scrape_book("https://www.amazon.com/dp/B0ABCDEFGH/", browser, "amazon")

    assert result.status is ScrapeStatus.TIMEOUT
    assert browser.new_page.await_count == 3
    assert page.close.await_count == 3
    assert browser.new_page.await_count == page.close.await_count


async def test_close_failure_does_not_mask_original_error():
    """
    [Local] scrape details: verifies close failure does not mask original error.

    Scenario:
        ``page.close()`` itself raises (e.g. the browser already crashed).

    Boundaries:
        - a happy-path scrape whose page close raises; the close failure must be swallowed so the successful result still propagates.

    On failure, first check:
        - the close exception masks the real return value, turning a good scrape into a crash.
    """
    page = _amazon_page()
    page.close = AsyncMock(side_effect=RuntimeError("Target page, context or browser has been closed"))
    browser = FakeBrowser(page=page)

    result = await scrape_book("https://www.amazon.com/dp/B0ABCDEFGH/", browser, "amazon")

    assert result.status is ScrapeStatus.SUCCESS
    assert result.book["title"] == "Great Python Book"
    page.close.assert_awaited_once()
