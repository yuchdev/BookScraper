"""Unit/mock tests for bookscraper.scraping.scrape_details.

Every external collaborator is mocked: Playwright (via playwright_fakes), httpx, and
asyncio.sleep. No network, no browser, no disk.
"""

import json
from contextlib import AbstractContextManager
from typing import Any
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
) -> dict[str, Any]:
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


def _patch_httpx(responses=None, get_side_effect=None) -> AbstractContextManager[Any]:
    """Patch scrape_details.httpx.AsyncClient; returns the patch context manager."""
    factory, _client = build_async_client_factory(responses=responses, get_side_effect=get_side_effect)
    return patch.object(scrape_details.httpx, "AsyncClient", factory)


def _amazon_page(title="Great Python Book - Amazon") -> FakePage:
    """A FakePage configured for a successful amazon detail scrape."""
    locators = {
        "#productTitle": FakeLocator("  Great Python Book  "),
        ".author a.a-link-normal": FakeLocator([{"text": "Jane Doe"}, {"text": "John Smith"}]),
        "#rpi-attribute-book_details-isbn10 .rpi-attribute-value span": FakeLocator("1234567890"),
        "#rpi-attribute-book_details-isbn13 .rpi-attribute-value span": FakeLocator("978-1234567890"),
        "#bookDescription_feature_div .a-expander-content": FakeLocator("  A great book about Python.  "),
    }
    return FakePage(locators=locators, title=title)


class _RaisingLocator(FakeLocator):
    """A FakeLocator whose text read raises a given Playwright error (missing-field resilience)."""

    def __init__(self, exc: BaseException):
        """Store the exception to raise on every text read."""
        super().__init__([])
        self._exc = exc

    @property
    def first(self) -> "_RaisingLocator":
        """Return self so a ``.first.text_content()`` chain also raises."""
        return self

    async def text_content(self, *args: Any, **kwargs: Any) -> str:
        """Raise the configured Playwright error instead of returning text."""
        raise self._exc


class _RecordingLocator:
    """A locator wrapper that records the ``timeout`` kwarg of every text/attribute read."""

    def __init__(self, inner: FakeLocator, reads: list):
        """Wrap ``inner`` and append each read's timeout to the shared ``reads`` list."""
        self._inner = inner
        self._reads = reads

    @property
    def first(self) -> "_RecordingLocator":
        """Return a recording wrapper around the inner locator's first match."""
        return _RecordingLocator(self._inner.first, self._reads)

    async def text_content(self, *args: Any, **kwargs: Any) -> Any:
        """Record the timeout kwarg, then delegate to the inner locator."""
        self._reads.append(kwargs.get("timeout"))
        return await self._inner.text_content(*args, **kwargs)

    async def get_attribute(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """Record the timeout kwarg, then delegate to the inner locator."""
        self._reads.append(kwargs.get("timeout"))
        return await self._inner.get_attribute(name, *args, **kwargs)

    async def all(self) -> list:
        """Return recording wrappers around each inner match."""
        return [_RecordingLocator(child, self._reads) for child in await self._inner.all()]

    async def count(self) -> int:
        """Delegate the match count to the inner locator."""
        return await self._inner.count()

    async def is_visible(self, *args: Any, **kwargs: Any) -> bool:
        """Delegate the visibility check to the inner locator."""
        return await self._inner.is_visible(*args, **kwargs)

    async def click(self, *args: Any, **kwargs: Any) -> Any:
        """Delegate the click to the inner locator."""
        return await self._inner.click(*args, **kwargs)

    def locator(self, selector: str) -> "_RecordingLocator":
        """Return a recording wrapper around a nested locator lookup."""
        return _RecordingLocator(self._inner.locator(selector), self._reads)


class _RecordingPage(FakePage):
    """A FakePage whose ``locator()`` returns recording wrappers, capturing every read's timeout."""

    def __init__(self, reads: list, **kwargs: Any):
        """Set up the underlying FakePage and attach the shared ``reads`` recorder."""
        super().__init__(**kwargs)
        self._reads = reads

    def locator(self, selector: str) -> _RecordingLocator:
        """Return a recording wrapper around the pre-configured FakeLocator for ``selector``."""
        return _RecordingLocator(super().locator(selector), self._reads)


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


async def test_missing_base_url_is_no_longer_fatal():
    """
    [Local] scrape details: verifies a site without a BASE_URL still scrapes an absolute URL.

    Scenario:
        the "packtpub" site config has no BASE_URL key, and scrape_book is called with an
        already-absolute detail URL and a page whose title element is present.

    Boundaries:
        - single attempt, no storage backend; ``urljoin`` leaves the absolute URL untouched so
          the scrape proceeds to a SUCCESS record instead of the old KeyError-driven FAILED
          that wasted all three retries (defect (c)); ``new_page`` is opened exactly once.

    On failure, first check:
        - the BASE_URL lookup in scrape_book again subscripts ``site_constants[site]["BASE_URL"]``
          directly (raising KeyError) rather than using ``.get("BASE_URL") or ""``.
    """
    url = "https://www.packtpub.com/product/x/9781838554590"
    page = FakePage(
        locators={
            "h1.product-title": FakeLocator("Some Packt Book"),
            "div.authors span": FakeLocator([{"text": "Adrian Ostrowski"}]),
        },
        title="Some Packt Book | Packt",
    )
    browser = FakeBrowser(page=page)

    result = await scrape_book(url, browser, "packtpub")

    assert result.status is ScrapeStatus.SUCCESS
    assert result.book["title"] == "Some Packt Book"
    assert result.book["authors"] == ["Adrian Ostrowski"]
    # packtpub has no ISBN10 selector, so the sentinel survives (03.1 replaces it).
    assert result.book["isbn10"] == "N/A"
    assert result.site == "packtpub"
    assert browser.new_page.await_count == 1


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


async def test_keyerror_like_bug_is_not_retried():
    """
    [Local] scrape details: verifies a deterministic bug fails immediately with no retry.

    Scenario:
        a FakePage whose ``goto`` raises ``KeyError`` on every attempt (a deterministic
        programming bug, not a transient fault).

    Boundaries:
        - the new retry policy retries ONLY transient failures; a KeyError-class bug must go
          straight to FAILED - ``goto`` and ``new_page`` are each awaited exactly once and
          ``asyncio.sleep`` is never awaited, so a bug that can never succeed no longer burns
          3x the latency (defect (g)).

    On failure, first check:
        - the retry loop again retries non-transient exceptions instead of consulting
          ``_is_transient_error`` and returning FAILED on the first attempt.
    """
    page = _amazon_page()
    page.goto = AsyncMock(side_effect=KeyError("boom"))
    browser = FakeBrowser(page=page)

    with patch.object(scrape_details.asyncio, "sleep", new=AsyncMock()) as mock_sleep:
        result = await scrape_book("https://www.amazon.com/dp/B0ABCDEFGH/", browser, "amazon")

    assert result.status is ScrapeStatus.FAILED
    assert result.book is None
    assert page.goto.await_count == 1
    assert browser.new_page.await_count == 1
    assert mock_sleep.await_count == 0


async def test_timeout_is_retried_up_to_max_attempts():
    """
    [Local] scrape details: verifies a transient timeout is retried up to MAX_ATTEMPTS.

    Scenario:
        every navigation raises a Playwright ``TimeoutError`` (a transient fault).

    Boundaries:
        - a transient failure IS retried: ``goto`` is awaited ``MAX_ATTEMPTS`` times with a
          sleep between each attempt (``MAX_ATTEMPTS - 1`` sleeps), and the exhausted result is
          a typed TIMEOUT recording ``attempts == MAX_ATTEMPTS``.

    On failure, first check:
        - ``_is_transient_error`` no longer classifies a Playwright TimeoutError as transient,
          or the retry budget stopped honouring ``MAX_ATTEMPTS``.
    """
    page = _amazon_page()
    page.goto = AsyncMock(side_effect=PlaywrightTimeoutError("nav timeout"))
    browser = FakeBrowser(page=page)

    with patch.object(scrape_details.asyncio, "sleep", new=AsyncMock()) as mock_sleep:
        result = await scrape_book("https://www.amazon.com/dp/B0ABCDEFGH/", browser, "amazon")

    assert result.status is ScrapeStatus.TIMEOUT
    assert result.attempts == scrape_details.MAX_ATTEMPTS
    assert page.goto.await_count == scrape_details.MAX_ATTEMPTS
    assert mock_sleep.await_count == scrape_details.MAX_ATTEMPTS - 1


async def test_missing_title_selector_returns_parse_error_without_retry():
    """
    [Local] scrape details: verifies a missing title element yields PARSE_ERROR with no retry.

    Scenario:
        an amazon page loads but has no ``#productTitle`` element (the required title is absent).

    Boundaries:
        - a missing required field after the page loads is a parse error, not a transient
          fault: the result is a typed PARSE_ERROR carrying ``error == "title not found"`` and
          ``new_page`` is opened exactly once (no retry burned on a deterministically empty page).

    On failure, first check:
        - the required-title guard no longer short-circuits to PARSE_ERROR before the retry
          loop, or it is wrongly treated as a transient failure and retried.
    """
    page = FakePage(locators={}, title="Some Title - Amazon")
    browser = FakeBrowser(page=page)

    with patch.object(scrape_details.asyncio, "sleep", new=AsyncMock()) as mock_sleep:
        result = await scrape_book("https://www.amazon.com/dp/B0ABCDEFGH/", browser, "amazon")

    assert result.status is ScrapeStatus.PARSE_ERROR
    assert result.book is None
    assert result.error == "title not found"
    assert browser.new_page.await_count == 1
    assert mock_sleep.await_count == 0


async def test_empty_title_text_returns_parse_error():
    """
    [Local] scrape details: verifies a whitespace-only title yields PARSE_ERROR.

    Scenario:
        the ``#productTitle`` element is present but its text is only whitespace.

    Boundaries:
        - an empty title is treated identically to a missing one - stripped to empty, it fails
          the required-field rule and returns PARSE_ERROR rather than a book with a blank title.

    On failure, first check:
        - the title emptiness check uses ``if not title`` after stripping, not merely a
          presence check on the element.
    """
    page = FakePage(locators={"#productTitle": FakeLocator("   ")}, title="x - Amazon")
    browser = FakeBrowser(page=page)

    result = await scrape_book("https://www.amazon.com/dp/B0ABCDEFGH/", browser, "amazon")

    assert result.status is ScrapeStatus.PARSE_ERROR
    assert result.error == "title not found"


async def test_publication_date_exception_still_returns_book_with_none_year():
    """
    [Local] scrape details: verifies a failing publication-date read never aborts the scrape.

    Scenario:
        the publication-date locator's ``text_content`` raises a Playwright error while every
        other field reads normally.

    Boundaries:
        - a missing/slow optional field resolves to ``None`` through the ``_text`` helper, so the
          book is still returned SUCCESS with ``publication_date``/``publication_year`` left
          ``None`` instead of raising out of the loop.

    On failure, first check:
        - the publication-date read bypasses ``_text`` (so the Playwright error escapes) or the
          neutral ``None`` initialisation of the date keys was dropped.
    """
    pub_selector = scrape_details.site_constants["amazon"]["PUBLICATION_DATE"]
    locators = {
        "#productTitle": FakeLocator("Great Python Book"),
        ".author a.a-link-normal": FakeLocator([{"text": "Jane Doe"}]),
        pub_selector: _RaisingLocator(scrape_details.PlaywrightError("boom")),
    }
    page = FakePage(locators=locators, title="Great Python Book - Amazon")
    browser = FakeBrowser(page=page)

    result = await scrape_book("https://www.amazon.com/dp/B0ABCDEFGH/", browser, "amazon")

    assert result.status is ScrapeStatus.SUCCESS
    assert result.book["title"] == "Great Python Book"
    assert result.book["publication_date"] is None
    assert result.book["publication_year"] is None


async def test_meta_date_without_content_attribute_is_none_not_crash():
    """
    [Local] scrape details: verifies a meta date tag with no content attribute yields None.

    Scenario:
        a site configured with a ``PUBLICATION_DATE_META`` selector whose matched ``<meta>`` tag
        carries no ``content`` attribute.

    Boundaries:
        - reading the absent attribute returns ``None`` through ``_attr`` and the subsequent
          ``.split("T")`` is never reached, so the scrape succeeds with a ``None`` date rather
          than crashing with ``AttributeError`` on ``None.split``.

    On failure, first check:
        - the meta-date branch calls ``.split("T")`` on an unchecked value instead of guarding
          the ``_attr`` result first.
    """
    locators = {
        "#productTitle": FakeLocator("Great Python Book"),
        ".author a.a-link-normal": FakeLocator([{"text": "Jane Doe"}]),
        'meta[name="date"]': FakeLocator([{"attrs": {}}]),
    }
    page = FakePage(locators=locators, title="Great Python Book - Amazon")
    browser = FakeBrowser(page=page)

    with patch.dict(scrape_details.site_constants["amazon"], {"PUBLICATION_DATE_META": 'meta[name="date"]'}):
        result = await scrape_book("https://www.amazon.com/dp/B0ABCDEFGH/", browser, "amazon")

    assert result.status is ScrapeStatus.SUCCESS
    assert result.book["publication_date"] is None
    assert result.book["publication_year"] is None


async def test_text_helper_returns_none_on_timeout():
    """
    [Local] scrape details: verifies the _text helper swallows a timeout and returns None.

    Scenario:
        the located element's ``text_content`` raises a Playwright ``TimeoutError``.

    Boundaries:
        - ``_text`` is the single per-field extraction primitive; it must never propagate a
          timeout (or any Playwright error) - it returns ``None`` so a slow optional field can
          never abort a scrape or trigger a retry.

    On failure, first check:
        - ``_text`` catches too narrow an exception type, letting the Playwright error escape.
    """
    page = FakePage(locators={".slow": _RaisingLocator(PlaywrightTimeoutError("slow"))})

    result = await scrape_details._text(page, ".slow", timeout_ms=1000)

    assert result is None


async def test_every_locator_read_passes_explicit_timeout():
    """
    [Local] scrape details: verifies every field read passes an explicit positive timeout.

    Scenario:
        a fully-populated amazon scrape is run against a page that records the ``timeout`` kwarg
        of every ``text_content`` / ``get_attribute`` call.

    Boundaries:
        - a missing title/ISBN/description element must never block on Playwright's 30 s default;
          every recorded read carries one of the module's explicit timeout constants and no read
          is left unbounded (``timeout is None``).

    On failure, first check:
        - a field extraction reads a locator without passing an explicit ``timeout``/``timeout_ms``.
    """
    reads: list = []
    src = _amazon_page()
    page = _RecordingPage(reads, locators=dict(src._locators), title="Great Python Book - Amazon")
    browser = FakeBrowser(page=page)

    result = await scrape_book("https://www.amazon.com/dp/B0ABCDEFGH/", browser, "amazon")

    assert result.status is ScrapeStatus.SUCCESS
    assert reads  # at least one bounded read happened
    allowed = {scrape_details.FIELD_TIMEOUT_MS, scrape_details.OPTIONAL_FIELD_TIMEOUT_MS}
    assert all(timeout in allowed for timeout in reads)


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


# ---------------------------------------------------------------------------
# scrape_book - field-extraction branches & retry classification (subtask 03.0/03)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (PlaywrightTimeoutError("slow"), True),
        (httpx.ConnectError("refused"), True),
        (scrape_details.PlaywrightError("net::ERR_NAME_NOT_RESOLVED at x"), True),
        (scrape_details.PlaywrightError("Target closed"), True),
        (scrape_details.PlaywrightError("Navigation failed because page crashed"), True),
        (scrape_details.PlaywrightError("strict mode violation: resolved to 2 elements"), False),
        (KeyError("title"), False),
        (ValueError("boom"), False),
    ],
)
def test_is_transient_error_classifies_failures(exc, expected):
    """
    [Local] scrape details: verifies the transient/non-transient classifier.

    Scenario:
        a representative exception is handed to ``_is_transient_error``.

    Boundaries:
        - a Playwright timeout, an httpx transport error, and a Playwright error naming a
          navigation/network fault are transient (retry-worthy); a non-navigation Playwright
          error and plain bugs (KeyError/ValueError) are not.

    On failure, first check:
        - the marker list ``_TRANSIENT_PLAYWRIGHT_MARKERS`` or the isinstance ladder in
          ``_is_transient_error`` changed.
    """
    assert scrape_details._is_transient_error(exc) is expected


async def test_attr_helper_returns_none_on_error():
    """
    [Local] scrape details: verifies the _attr helper swallows a Playwright error and returns None.

    Scenario:
        the located element's attribute read raises a Playwright error.

    Boundaries:
        - ``_attr`` mirrors ``_text`` for attribute reads; it must never propagate a Playwright
          error, returning ``None`` so an absent metadata attribute cannot abort a scrape.

    On failure, first check:
        - ``_attr`` catches too narrow an exception type.
    """

    class _RaisingAttr(FakeLocator):
        """A FakeLocator whose get_attribute raises a Playwright error."""

        @property
        def first(self) -> "_RaisingAttr":
            """Return self so the ``.first.get_attribute`` chain also raises."""
            return self

        async def get_attribute(self, name: str, *args: Any, **kwargs: Any) -> Any:
            """Raise a Playwright error instead of returning an attribute value."""
            raise scrape_details.PlaywrightError("boom")

    page = FakePage(locators={".m": _RaisingAttr([])})

    result = await scrape_details._attr(page, ".m", "content", timeout_ms=1000)

    assert result is None


async def test_collect_texts_handles_errors():
    """
    [Local] scrape details: verifies _collect_texts skips unreadable elements and empties on error.

    Scenario:
        one matched element reads normally while a second raises on ``text_content``.

    Boundaries:
        - the helper collects the readable text and silently skips the failing element, never
          raising for a detached/unreadable element in a multi-valued field.

    On failure, first check:
        - the per-element try/except in ``_collect_texts`` narrowed or widened its catch.
    """

    class _PartlyRaising(FakeLocator):
        """A FakeLocator whose ``all()`` yields one good and one raising element."""

        async def all(self) -> list:
            """Return a readable locator and one whose text read raises."""
            good = FakeLocator("Jane Doe")
            return [good, _RaisingLocator(scrape_details.PlaywrightError("boom"))]

    page = FakePage(locators={".a": _PartlyRaising([{"text": "x"}])})

    result = await scrape_details._collect_texts(page, ".a", timeout_ms=1000)

    assert result == ["Jane Doe"]


async def test_publication_date_text_is_extracted():
    """
    [Local] scrape details: verifies the page-text publication date populates date and year.

    Scenario:
        an amazon page whose PUBLICATION_DATE element carries a human date string.

    Boundaries:
        - the non-meta date branch reads the text, stores it verbatim (03.1 normalizes later),
          and derives the publication year from it.

    On failure, first check:
        - the PUBLICATION_DATE branch stopped reading via ``_text`` or stopped calling
          ``extract_year_from_date``.
    """
    pub_selector = scrape_details.site_constants["amazon"]["PUBLICATION_DATE"]
    locators = {
        "#productTitle": FakeLocator("Great Python Book"),
        ".author a.a-link-normal": FakeLocator([{"text": "Jane Doe"}]),
        pub_selector: FakeLocator("August 16, 2012"),
    }
    page = FakePage(locators=locators, title="Great Python Book - Amazon")
    browser = FakeBrowser(page=page)

    result = await scrape_book("https://www.amazon.com/dp/B0ABCDEFGH/", browser, "amazon")

    assert result.status is ScrapeStatus.SUCCESS
    assert result.book["publication_date"] == "August 16, 2012"
    assert result.book["publication_year"] == 2012


async def test_meta_publication_date_parsed_from_content():
    """
    [Local] scrape details: verifies an ISO meta date is split to a date and year.

    Scenario:
        a site configured with PUBLICATION_DATE_META whose ``<meta>`` carries an ISO
        ``content`` timestamp.

    Boundaries:
        - the meta branch reads the attribute, keeps the date portion before the ``T``, and
          derives the year; this is the positive counterpart to the no-content crash test.

    On failure, first check:
        - the meta-date branch no longer splits on ``T`` or skips year extraction.
    """
    locators = {
        "#productTitle": FakeLocator("Great Python Book"),
        ".author a.a-link-normal": FakeLocator([{"text": "Jane Doe"}]),
        'meta[name="date"]': FakeLocator([{"attrs": {"content": "2014-11-01T00:00:00Z"}}]),
    }
    page = FakePage(locators=locators, title="Great Python Book - Amazon")
    browser = FakeBrowser(page=page)

    with patch.dict(scrape_details.site_constants["amazon"], {"PUBLICATION_DATE_META": 'meta[name="date"]'}):
        result = await scrape_book("https://www.amazon.com/dp/B0ABCDEFGH/", browser, "amazon")

    assert result.status is ScrapeStatus.SUCCESS
    assert result.book["publication_date"] == "2014-11-01"
    assert result.book["publication_year"] == 2014


async def test_meta_authors_extracted_from_content():
    """
    [Local] scrape details: verifies authors are read from meta ``content`` attributes.

    Scenario:
        a site configured with AUTHORS_META whose ``<meta>`` tags carry author names in
        ``content``.

    Boundaries:
        - when AUTHORS_META is present it takes precedence over the linked-text AUTHORS path and
          each tag's ``content`` becomes one author.

    On failure, first check:
        - the AUTHORS_META loop stopped reading the ``content`` attribute or lost its timeout.
    """
    locators = {
        "#productTitle": FakeLocator("Great Python Book"),
        'meta[name="author"]': FakeLocator([{"attrs": {"content": "Jane Doe"}}, {"attrs": {"content": "John Smith"}}]),
    }
    page = FakePage(locators=locators, title="Great Python Book - Amazon")
    browser = FakeBrowser(page=page)

    with patch.dict(scrape_details.site_constants["amazon"], {"AUTHORS_META": 'meta[name="author"]'}):
        result = await scrape_book("https://www.amazon.com/dp/B0ABCDEFGH/", browser, "amazon")

    assert result.status is ScrapeStatus.SUCCESS
    assert result.book["authors"] == ["Jane Doe", "John Smith"]


async def test_authors_alt_fallback_used_when_primary_empty():
    """
    [Local] scrape details: verifies the AUTHORS_ALT fallback fires when AUTHORS matches nothing.

    Scenario:
        an amazon page whose primary AUTHORS selector matches no elements but whose AUTHORS_ALT
        selector does.

    Boundaries:
        - the primary selector yields an empty list, so the alternative selector is queried and
          supplies the authors (defect-free degradation, not a parse failure).

    On failure, first check:
        - the ``if not authors_list`` fallback to AUTHORS_ALT was removed or reordered.
    """
    alt_selector = scrape_details.site_constants["amazon"]["AUTHORS_ALT"]
    locators = {
        "#productTitle": FakeLocator("Great Python Book"),
        ".author a.a-link-normal": FakeLocator([]),
        alt_selector: FakeLocator([{"text": "Alt Author"}]),
    }
    page = FakePage(locators=locators, title="Great Python Book - Amazon")
    browser = FakeBrowser(page=page)

    result = await scrape_book("https://www.amazon.com/dp/B0ABCDEFGH/", browser, "amazon")

    assert result.status is ScrapeStatus.SUCCESS
    assert result.book["authors"] == ["Alt Author"]


async def test_read_more_expander_is_clicked_before_reading_description():
    """
    [Local] scrape details: verifies a visible 'Read more' expander is clicked before the read.

    Scenario:
        an oreilly page exposing a visible READ_MORE_LINK and a DESCRIPTION element.

    Boundaries:
        - the expander branch clicks the link (best-effort) and then reads the full description;
          the click is bounded and its absence/failure is non-fatal.

    On failure, first check:
        - the READ_MORE_LINK branch stopped gating on ``is_visible`` or stopped reading the
          description afterwards.
    """
    read_more = FakeLocator([{"text": "Read more", "visible": True}])
    description_selector = scrape_details.site_constants["oreilly"]["DESCRIPTION"]
    locators = {
        "h1.t-title": FakeLocator("Effective Modern C++"),
        "a.author-name": FakeLocator([{"text": "Scott Meyers"}]),
        "a.read-more-link": read_more,
        description_selector: FakeLocator("  Full description.  "),
    }
    page = FakePage(locators=locators, title="Effective Modern C++")
    browser = FakeBrowser(page=page)

    result = await scrape_book("https://www.oreilly.com/library/view/x/9/", browser, "oreilly")

    assert result.status is ScrapeStatus.SUCCESS
    assert result.book["description"] == "Full description."
    page.wait_for_timeout.assert_awaited()


# ---------------------------------------------------------------------------
# scrape_book - wired-in selectors: tags, description fallback, details expander
# (subtask 03.0/04)
# ---------------------------------------------------------------------------


async def test_tags_extracted_deduplicated_in_order():
    """
    [Local] scrape details: verifies tags are stripped, de-duplicated in order, and listed.

    Scenario:
        an amazon page whose TAGS selector matches several anchors, including a duplicate, a
        whitespace-only one, and names in a specific order.

    Boundaries:
        - the TAGS branch collects every match through ``_texts``: empty/whitespace matches are
          dropped, later duplicates are removed while first-seen order is preserved, and the
          result is stored as ``book["tags"]`` (the neutral ``[]`` shape added this subtask).

    On failure, first check:
        - the TAGS read stopped using ``_texts`` (so dedup/order/cap regressed) or the ``tags``
          key is no longer populated from it.
    """
    tags_selector = scrape_details.site_constants["amazon"]["TAGS"]
    locators = {
        "#productTitle": FakeLocator("Great Python Book"),
        ".author a.a-link-normal": FakeLocator([{"text": "Jane Doe"}]),
        tags_selector: FakeLocator(
            [{"text": "Python"}, {"text": "Data"}, {"text": "  "}, {"text": "Python"}, {"text": "Data Science"}]
        ),
    }
    page = FakePage(locators=locators, title="Great Python Book - Amazon")
    browser = FakeBrowser(page=page)

    result = await scrape_book("https://www.amazon.com/dp/B0ABCDEFGH/", browser, "amazon")

    assert result.status is ScrapeStatus.SUCCESS
    # Duplicate "Python" removed, blank dropped, first-seen order preserved.
    assert result.book["tags"] == ["Python", "Data", "Data Science"]


async def test_tags_missing_selector_yields_empty_list():
    """
    [Local] scrape details: verifies an unconfigured TAGS selector leaves tags an empty list.

    Scenario:
        an amazon scrape whose ``TAGS`` entry is patched to ``None`` (no selector configured).

    Boundaries:
        - with no TAGS selector the branch is skipped and the neutral ``[]`` initialised up front
          survives, so a site that exposes no tags still produces a well-shaped record with an
          empty ``tags`` list rather than a missing key.

    On failure, first check:
        - the ``tags`` key was dropped from the neutral init, or ``_selector`` stopped treating a
          ``None``/absent TAGS value as unconfigured.
    """
    page = FakePage(
        locators={
            "#productTitle": FakeLocator("Great Python Book"),
            ".author a.a-link-normal": FakeLocator([{"text": "Jane Doe"}]),
        },
        title="Great Python Book - Amazon",
    )
    browser = FakeBrowser(page=page)

    with patch.dict(scrape_details.site_constants["amazon"], {"TAGS": None}):
        result = await scrape_book("https://www.amazon.com/dp/B0ABCDEFGH/", browser, "amazon")

    assert result.status is ScrapeStatus.SUCCESS
    assert result.book["tags"] == []


async def test_description_alt_used_when_primary_empty():
    """
    [Local] scrape details: verifies DESCRIPTION_ALT supplies the description when DESCRIPTION is empty.

    Scenario:
        an amazon page whose primary ``DESCRIPTION`` selector matches nothing but whose
        ``DESCRIPTION_ALT`` selector carries the description text.

    Boundaries:
        - the primary read yields ``None``, so the fallback selector is queried and supplies the
          description (defect-free degradation); the primary-present case is covered by the
          amazon happy-path test.

    On failure, first check:
        - the ``if not description`` fallback to DESCRIPTION_ALT was removed or the ALT selector
          is read before the primary.
    """
    alt_selector = scrape_details.site_constants["amazon"]["DESCRIPTION_ALT"]
    locators = {
        "#productTitle": FakeLocator("Great Python Book"),
        ".author a.a-link-normal": FakeLocator([{"text": "Jane Doe"}]),
        # Primary DESCRIPTION selector intentionally absent -> empty read.
        alt_selector: FakeLocator("  A fallback description.  "),
    }
    page = FakePage(locators=locators, title="Great Python Book - Amazon")
    browser = FakeBrowser(page=page)

    result = await scrape_book("https://www.amazon.com/dp/B0ABCDEFGH/", browser, "amazon")

    assert result.status is ScrapeStatus.SUCCESS
    assert result.book["description"] == "A fallback description."


class _OrderRecordingLocator(FakeLocator):
    """A FakeLocator that appends a label to a shared list on click/text reads (order probe)."""

    def __init__(self, matches: Any, events: list, label: str):
        """Store the shared ``events`` list and this locator's ``label``."""
        super().__init__(matches)
        self._events = events
        self._label = label

    @property
    def first(self) -> "_OrderRecordingLocator":
        """Return a labelled recorder around the first match so the chain still records."""
        return _OrderRecordingLocator(self._matches[:1], self._events, self._label)

    async def click(self, *args: Any, **kwargs: Any) -> Any:
        """Record a ``"<label>:click"`` event before the no-op click."""
        self._events.append(f"{self._label}:click")

    async def text_content(self, *args: Any, **kwargs: Any) -> Any:
        """Record a ``"<label>:read"`` event, then delegate to the FakeLocator read."""
        self._events.append(f"{self._label}:read")
        return await super().text_content(*args, **kwargs)


async def test_details_button_clicked_before_isbn_read():
    """
    [Local] scrape details: verifies the DETAILS_BUTTON expander is clicked before the ISBN read.

    Scenario:
        an amazon page whose visible DETAILS_BUTTON and ISBN10 locators both append to a shared
        ordered event list when clicked / read.

    Boundaries:
        - the expander reveals the ISBN/date blocks, so the click must happen BEFORE those reads;
          the test asserts the ``details:click`` event precedes the ``isbn10:read`` event rather
          than only that both occurred.

    On failure, first check:
        - the DETAILS_BUTTON click moved after (or into) the ISBN/publication-date block, or its
          ``is_visible`` gate stopped firing.
    """
    events: list = []
    details_selector = scrape_details.site_constants["amazon"]["DETAILS_BUTTON"]
    isbn10_selector = scrape_details.site_constants["amazon"]["ISBN10"]
    locators = {
        "#productTitle": FakeLocator("Great Python Book"),
        ".author a.a-link-normal": FakeLocator([{"text": "Jane Doe"}]),
        details_selector: _OrderRecordingLocator([{"text": "Product details", "visible": True}], events, "details"),
        isbn10_selector: _OrderRecordingLocator("1234567890", events, "isbn10"),
    }
    page = FakePage(locators=locators, title="Great Python Book - Amazon")
    browser = FakeBrowser(page=page)

    result = await scrape_book("https://www.amazon.com/dp/B0ABCDEFGH/", browser, "amazon")

    assert result.status is ScrapeStatus.SUCCESS
    assert "details:click" in events
    assert "isbn10:read" in events
    assert events.index("details:click") < events.index("isbn10:read")


def test_empty_string_selector_treated_as_unconfigured():
    """
    [Local] scrape details: verifies _selector treats "" and None identically as unconfigured.

    Scenario:
        the ``_selector`` helper is called for an empty-string value (packtpub's READ_MORE_LINK
        sentinel), a ``None`` value, an absent key, and a real selector string.

    Boundaries:
        - ``""`` and ``None`` and a missing key all resolve to ``None`` ("not configured") so no
          caller has to special-case the empty-string spelling; a non-empty selector is returned
          verbatim.

    On failure, first check:
        - ``_selector`` started distinguishing ``""`` from ``None`` or dropped the falsy guard.
    """
    assert scrape_details._selector({"READ_MORE_LINK": ""}, "READ_MORE_LINK") is None
    assert scrape_details._selector({"READ_MORE_LINK": None}, "READ_MORE_LINK") is None
    assert scrape_details._selector({}, "READ_MORE_LINK") is None
    assert scrape_details._selector({"DESCRIPTION": ".about"}, "DESCRIPTION") == ".about"
