"""Unit/mock tests for bookscraper.scraping.scrape_details.

Every external collaborator is mocked: Playwright (via playwright_fakes), httpx, and
asyncio.sleep. No network, no browser, no disk.
"""

import asyncio
import json
from contextlib import AbstractContextManager
from types import SimpleNamespace
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import httpx
import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

import bookscraper.book_utils as book_utils
from bookscraper.scraping import ai_common, ai_extraction, scrape_details
from bookscraper.scraping.ai_common import UsageAccumulator
from bookscraper.scraping.parameters import DEFAULT_RATE_LIMIT, RATE_LIMITS
from bookscraper.scraping.politeness import (
    DomainCircuitBreakers,
    DomainRateLimiters,
    RobotsCache,
    RobotsResponse,
)
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


@pytest.fixture(autouse=True)
def _no_wait_default_limiters():
    """Replace the shared default rate limiter with a never-throttling one for every test.

    The production default would, across tests sharing one process-wide registry, make a second
    same-host request wait in real time (amazon is 0.5 req/s, burst 1). An enormous burst means
    ``acquire`` always has a token and never sleeps, so no test ever waits on the clock while the
    limiter integration stays exercised. The dedicated rate-limiter tests pass their own limiter
    with a fake clock and are unaffected.
    """
    unbounded = DomainRateLimiters({}, (1_000_000.0, 1_000_000))
    with patch.object(scrape_details, "default_limiters", return_value=unbounded):
        yield


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
    """Patch scrape_details.httpx.AsyncClient; returns the patch context manager.

    Since subtask 03.0/05 get_leanpub_book_details instantiates the client directly (not via
    ``async with``) and closes it with ``aclose()``, so the factory returns the fake client
    itself (with an awaitable ``aclose``) rather than an async context manager.
    """
    _factory, client = build_async_client_factory(responses=responses, get_side_effect=get_side_effect)
    client.aclose = AsyncMock()
    direct_factory = MagicMock(return_value=client)
    return patch.object(scrape_details.httpx, "AsyncClient", direct_factory)


def _amazon_page(title="Great Python Book - Amazon") -> FakePage:
    """A FakePage configured for a successful amazon detail scrape."""
    locators = {
        "#productTitle": FakeLocator("  Great Python Book  "),
        ".author a.a-link-normal": FakeLocator([{"text": "Jane Doe"}, {"text": "John Smith"}]),
        "#rpi-attribute-book_details-isbn10 .rpi-attribute-value span": FakeLocator("0136816487"),
        "#rpi-attribute-book_details-isbn13 .rpi-attribute-value span": FakeLocator("978-0136816485"),
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
        Then the result has status SUCCESS and the canonical schema-2 document is correctly
        populated (title, source_id, slug, authors, tags, publication_date/year, the bare
        "leanpub" site key, the human-facing url, schema_version and hash).

    Boundaries:
        - httpx is mocked; no network. Error branches are covered by separate tests. Verifies the
          03.1/04 v2 renames (book_id->source_id, categories->tags, last_published_at->ISO
          publication_date, "leanpub.com"->"leanpub", derived url) built by the normalizer.

    On failure, first check:
        - normalize.from_leanpub_api field mapping, or the author/category resolution feeding it.
    """
    response = FakeResponse(json_data=_leanpub_payload())
    with _patch_httpx(responses=[response]):
        result = await get_leanpub_book_details(LEANPUB_URL)

    assert result.status is ScrapeStatus.SUCCESS
    book = result.book
    assert book["schema_version"] == 2
    assert book["title"] == "Example Book"
    assert book["source_id"] == "book-123"
    assert book["slug"] == "example-book"
    assert book["asin"] is None
    assert book["authors"] == ["Jane Doe"]
    assert book["tags"] == ["Python"]
    assert book["publication_date"] == "2023-05-01"
    assert book["publication_year"] == 2023
    assert book["site"] == "leanpub"
    assert book["url"] == "https://leanpub.com/example-book"
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

    about = result.book["description"]
    # All markup tags stripped.
    assert "<" not in about and ">" not in about
    # Paragraph/list breaks collapsed - never three+ consecutive newlines.
    assert "\n\n\n" not in about
    # Content survived the cleaning.
    assert "Hello" in about
    assert "Item one" in about
    assert "Item two" in about


async def test_leanpub_missing_data_is_parse_error():
    """
    [Local] scrape details: verifies that a response without a "data" key returns PARSE_ERROR.

    Scenario:
        Given a well-formed JSON response whose body has no top-level "data" key
        When get_leanpub_book_details is called
        Then the result has status PARSE_ERROR (not the old opaque FAILED), book is None, and
        url equals the request URL.

    Boundaries:
        - Covers the missing-data branch only; a missing title is a separate PARSE_ERROR path
          and transport/decode errors are their own FAILED tests.

    On failure, first check:
        - The guard that returns PARSE_ERROR when "data" is absent from the response JSON.
    """
    response = FakeResponse(json_data={"meta": "no data here"})
    with _patch_httpx(responses=[response]):
        result = await get_leanpub_book_details(LEANPUB_URL)

    assert result.status is ScrapeStatus.PARSE_ERROR
    assert result.book is None
    assert result.url == LEANPUB_URL


async def test_leanpub_missing_title_is_parse_error():
    """
    [Local] scrape details: verifies a response with data but no title returns PARSE_ERROR.

    Scenario:
        Given a well-formed response whose data.attributes carries no title (the field the
        record cannot exist without)
        When get_leanpub_book_details is called
        Then the result is PARSE_ERROR with error "title not found", book is None, and url is
        the request URL.

    Boundaries:
        - Covers the missing-title branch only; missing-data is a separate PARSE_ERROR test.

    On failure, first check:
        - The required-title guard that returns PARSE_ERROR when data.attributes has no title.
    """
    payload = {"data": {"id": "book-123", "attributes": {"slug": "no-title"}}}
    response = FakeResponse(json_data=payload)
    with _patch_httpx(responses=[response]):
        result = await get_leanpub_book_details(LEANPUB_URL)

    assert result.status is ScrapeStatus.PARSE_ERROR
    assert result.error == "title not found"
    assert result.book is None
    assert result.url == LEANPUB_URL


async def test_leanpub_missing_about_the_book_still_succeeds():
    """
    [Local] scrape details: verifies a null about_the_book no longer fails the scrape.

    Scenario:
        Given a Leanpub payload whose about_the_book is null (the case that used to raise
        TypeError inside unescape(None) and was caught as an "unexpected error", losing the book)
        When get_leanpub_book_details is called
        Then the result is SUCCESS, the description is an empty string, and the other fields
        (title, authors, hash) are still populated.

    Boundaries:
        - Covers the missing-about regression only; HTML cleaning of a present about_the_book is
          a separate test.

    On failure, first check:
        - get_leanpub_book_details passes about_the_book through html_to_text (which maps None to
          "") rather than the old unescape(None) that raised.
    """
    payload = _leanpub_payload(about=None)
    response = FakeResponse(json_data=payload)
    with _patch_httpx(responses=[response]):
        result = await get_leanpub_book_details(LEANPUB_URL)

    assert result.status is ScrapeStatus.SUCCESS
    assert result.book["description"] == ""
    assert result.book["title"] == "Example Book"
    assert result.book["authors"] == ["Jane Doe"]
    assert isinstance(result.book["hash"], str) and result.book["hash"]


async def test_leanpub_http_error_reports_status_without_body():
    """
    [Local] scrape details: verifies an HTTP error surfaces the status code but never the body.

    Scenario:
        Given the mock client raises HTTPStatusError carrying a 404 response whose body text is
        "secret error body"
        When get_leanpub_book_details is called
        Then the result is FAILED, error is exactly "HTTP 404", and the response body text never
        appears in error.

    Boundaries:
        - Pins the SEC requirement that no Leanpub response body reaches error/log/console; the
          generic not-None regression is covered by a separate test.

    On failure, first check:
        - The HTTPStatusError branch includes exc.response.text in the error or log instead of
          only the status code.
    """
    request = httpx.Request("GET", LEANPUB_URL)
    resp = httpx.Response(404, request=request, text="secret error body")
    status_exc = httpx.HTTPStatusError("boom", request=request, response=resp)
    response = FakeResponse(raise_status_exc=status_exc)
    with _patch_httpx(responses=[response]):
        result = await get_leanpub_book_details(LEANPUB_URL)

    assert result.status is ScrapeStatus.FAILED
    assert result.error == "HTTP 404"
    assert "secret error body" not in (result.error or "")


async def test_get_leanpub_book_details_malformed_date_yields_none_date():
    """
    [Local] scrape details: verifies that an unparseable date string yields None without failing.

    Scenario:
        Given a Leanpub payload with last_published_at set to "not-a-real-date"
        When get_leanpub_book_details is called
        Then the result status is SUCCESS, publication_date and publication_year are None, and
        hash is still computed (a bad date must not abort hashing).

    Boundaries:
        - Covers only the date-parse failure branch; other error paths are separate.

    On failure, first check:
        - normalize_date / normalize.from_leanpub_api handling of an unparseable date.
    """
    payload = _leanpub_payload(last_published_at="not-a-real-date")
    response = FakeResponse(json_data=payload)
    with _patch_httpx(responses=[response]):
        result = await get_leanpub_book_details(LEANPUB_URL)

    assert result.status is ScrapeStatus.SUCCESS
    assert result.book["publication_date"] is None
    assert result.book["publication_year"] is None
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


async def test_leanpub_invalid_json_is_failed_not_unbound_error():
    """
    [Local] scrape details: verifies a JSONDecodeError yields FAILED without an unbound-name crash.

    Scenario:
        Given the mock response's .json() raises json.JSONDecodeError
        When get_leanpub_book_details is called
        Then the result is FAILED, error is exactly "invalid JSON", and no NameError is raised
        from the old branch that referenced an unbound ``response`` variable.

    Boundaries:
        - Covers the JSON-parse failure branch; network and missing-key errors are separate.

    On failure, first check:
        - The except json.JSONDecodeError branch references an unbound variable, or no longer
          reports the secret-free "invalid JSON" error string.
    """
    decode_exc = json.JSONDecodeError("Expecting value", "", 0)
    response = FakeResponse(json_exc=decode_exc, text="<html>not json</html>")
    with _patch_httpx(responses=[response]):
        result = await get_leanpub_book_details(LEANPUB_URL)

    assert result.status is ScrapeStatus.FAILED
    assert result.book is None
    assert result.error == "invalid JSON"


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

    # scrape_book forwards its (defaulted) per-run limiters registry to the API helper.
    mock_details.assert_awaited_once_with("https://leanpub.com/x", limiters=ANY, robots=None)
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
    # packtpub has no ISBN10 selector, so the raw "N/A" sentinel is normalized to None (03.1/04).
    assert result.book["isbn10"] is None
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


def _disallowing_robots(site: str = "amazon") -> RobotsCache:
    """Build a RobotsCache whose canned robots.txt disallows /gp/cart on the host.

    The fetcher returns a fixed body (no network); the cache is wired to a never-throttling
    limiter registry so the crawl-delay path is a no-op here.
    """

    async def _fetch(robots_url: str) -> RobotsResponse:
        """Return a canned robots.txt disallowing the cart path."""
        return RobotsResponse(status_code=200, text="User-agent: *\nDisallow: /gp/cart\n")

    return RobotsCache(_fetch, DomainRateLimiters({}, (1_000_000.0, 1_000_000)))


async def test_disallowed_url_yields_skipped_robots_result():
    """
    [Local] scrape details: verifies a robots.txt-disallowed URL is never fetched (SKIPPED_ROBOTS).

    Scenario:
        Given a RobotsCache that disallows /gp/cart and an amazon URL on that path
        When scrape_book is called with that cache
        Then the result is SKIPPED_ROBOTS carrying no book, and page.goto is never awaited
        (no request reaches the disallowed path).

    Boundaries:
        - The page is opened before the robots check (to know the UA/full URL), but goto - the
          only network fetch of the target path - must not fire; a single attempt, no retry.

    On failure, first check:
        - The robots.allowed gate placed before the rate-limiter acquire and goto in scrape_book.
    """
    page = _amazon_page()
    browser = FakeBrowser(page=page)

    result = await scrape_book(
        "https://www.amazon.com/gp/cart",
        browser,
        "amazon",
        None,
        None,
        _disallowing_robots(),
    )

    assert result.status is ScrapeStatus.SKIPPED_ROBOTS
    assert result.book is None
    assert result.error == "disallowed by robots.txt"
    page.goto.assert_not_awaited()


class _FakeStreamResponse:
    """A minimal stand-in for the httpx streaming response used by build_robots_fetcher."""

    def __init__(self, status_code: int, chunks: list[bytes], encoding: str = "utf-8"):
        """Record the status, body chunks, and declared encoding to replay via aiter_bytes()."""
        self.status_code = status_code
        self.encoding = encoding
        self._chunks = chunks

    async def aiter_bytes(self) -> Any:
        """Yield each recorded body chunk in order, like httpx's streaming byte iterator."""
        for chunk in self._chunks:
            yield chunk


class _FakeStreamCM:
    """An async context manager returning a fixed :class:`_FakeStreamResponse`."""

    def __init__(self, response: _FakeStreamResponse):
        """Store the response handed out on __aenter__."""
        self._response = response

    async def __aenter__(self) -> _FakeStreamResponse:
        """Enter the context, returning the canned streaming response."""
        return self._response

    async def __aexit__(self, *exc_info: Any) -> bool:
        """Exit the context without suppressing any exception."""
        return False


def _stream_client(response: _FakeStreamResponse, calls: list) -> Any:
    """Build a MagicMock httpx client whose stream() yields ``response`` and records its args.

    :param response: The canned streaming response every ``client.stream(...)`` returns.
    :param calls: A list appended with ``(method, url, kwargs)`` for each ``stream`` invocation.
    :returns: The configured mock client.
    """
    client = MagicMock()

    def _stream(method: str, url: str, **kwargs: Any) -> _FakeStreamCM:
        """Record the call and return the context manager wrapping the canned response."""
        calls.append((method, url, kwargs))
        return _FakeStreamCM(response)

    client.stream = MagicMock(side_effect=_stream)
    return client


async def test_build_robots_fetcher_returns_status_and_body():
    """
    [Local] scrape details: verifies build_robots_fetcher streams and maps status + body.

    Scenario:
        Given a fake httpx client whose stream() yields a 200 response with a robots.txt body
        When the fetcher built by build_robots_fetcher is awaited for a robots.txt URL
        Then it returns a RobotsResponse carrying that status code and decoded text, and the client
        is streamed with the GET method, the robots.txt URL, and the 10 s robots timeout.

    Boundaries:
        - Only the thin httpx->RobotsResponse adapter; parsing/policy live in RobotsCache.

    On failure, first check:
        - The stream() call and RobotsResponse construction in build_robots_fetcher._fetch.
    """
    body = "User-agent: *\nDisallow: /secret\n"
    response = _FakeStreamResponse(200, [body.encode("utf-8")])
    calls: list = []
    client = _stream_client(response, calls)

    fetcher = scrape_details.build_robots_fetcher(client)
    result = await fetcher("https://example.test/robots.txt")

    assert isinstance(result, RobotsResponse)
    assert result.status_code == 200
    assert result.text == body
    assert len(calls) == 1
    method, url, kwargs = calls[0]
    assert method == "GET"
    assert url == "https://example.test/robots.txt"
    assert "timeout" in kwargs


async def test_build_robots_fetcher_rejects_oversized_body():
    """
    [Local] scrape details: verifies an oversized robots.txt body is refused (fail-closed).

    Scenario:
        Given a fake httpx client that streams more than ROBOTS_MAX_BYTES of body,
        When the fetcher built by build_robots_fetcher is awaited,
        Then it raises ValueError (never buffering the whole body), and when that fetcher backs a
        RobotsCache the host is disallowed for the run (the same fail-closed path as any fetch
        failure), so a hostile/compression-bombed response can neither exhaust memory nor open the
        host up.

    Boundaries:
        - Exercises the streaming size cap; a normal small body is the adjacent test.

    On failure, first check:
        - The ROBOTS_MAX_BYTES cap in build_robots_fetcher._fetch and RobotsCache fail-closed path.
    """
    oversized = scrape_details.ROBOTS_MAX_BYTES + 1
    # Two chunks so the cap trips mid-stream, not only on a single giant read.
    chunks = [b"x" * (oversized // 2 + 1), b"x" * (oversized // 2 + 1)]
    response = _FakeStreamResponse(200, chunks)
    calls: list = []
    client = _stream_client(response, calls)

    fetcher = scrape_details.build_robots_fetcher(client)
    with pytest.raises(ValueError):
        await fetcher("https://example.test/robots.txt")

    # The raise is the fetch-failure signal RobotsCache fails closed on.
    cache = RobotsCache(fetcher, DomainRateLimiters({}, (1_000_000.0, 1_000_000)))
    assert await cache.allowed("https://example.test/page", "*") is False


async def test_scrape_book_amazon_happy_path_returns_full_book():
    """
    [Local] scrape details: verifies that a fully populated amazon page yields a complete book.

    Scenario:
        Given a FakePage whose locators cover all amazon selectors
        When scrape_book is called
        Then the result has status SUCCESS and the canonical schema-2 document carries title,
        authors, checksum-validated/hyphen-stripped ISBNs, description, asin (both asin and the
        shared source_id), the bare site key, url, schema_version and hash.

    Boundaries:
        - No storage backend; duplicate-check behavior is covered by separate tests. The raw
          hyphenated ISBN-13 "978-0136816485" is normalized to its canonical 13-digit form by
          the 03.1/04 normalizer (via isbn.normalize).

    On failure, first check:
        - The field-extraction selectors for amazon, or normalize.from_playwright's mapping.
    """
    url = "https://www.amazon.com/dp/B0ABCDEFGH/ref=sr_1_1"
    page = _amazon_page()
    browser = FakeBrowser(page=page)

    result = await scrape_book(url, browser, "amazon")

    assert result.status is ScrapeStatus.SUCCESS
    book = result.book
    assert book["schema_version"] == 2
    assert book["title"] == "Great Python Book"
    assert book["authors"] == ["Jane Doe", "John Smith"]
    assert book["isbn10"] == "0136816487"
    assert book["isbn13"] == "9780136816485"
    assert book["description"] == "A great book about Python."
    assert book["asin"] == "B0ABCDEFGH"
    assert book["source_id"] == "B0ABCDEFGH"
    assert book["site"] == "amazon"
    assert book["url"] == url
    assert isinstance(book["hash"], str) and book["hash"]


async def test_scrape_book_amazon_missing_isbn_is_none():
    """
    [Local] scrape details: verifies that absent ISBN selectors produce null ISBNs.

    Scenario:
        Given a FakePage whose locators include no ISBN selectors
        When scrape_book is called for amazon
        Then the result is SUCCESS with isbn10 and isbn13 both None.

    Boundaries:
        - Covers the ISBN-fallback branch only; other field defaults are not asserted. The raw
          "N/A" sentinel that used to survive is now normalized to None by the 03.1/04 normalizer.

    On failure, first check:
        - The isbn10/isbn13 fallback in scrape_book, or isbn.normalize's handling of "N/A".
    """
    url = "https://www.amazon.com/dp/B0ABCDEFGH/"
    # No ISBN selectors configured -> empty locators -> "N/A" raw sentinel -> normalized to None.
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
    assert result.book["isbn10"] is None
    assert result.book["isbn13"] is None


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
          ``is_transient`` and returning FAILED on the first attempt.
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
        - ``is_transient`` no longer classifies a Playwright TimeoutError as transient,
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
    mock_details.assert_awaited_once_with("https://leanpub.com/x", limiters=ANY, robots=None)
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


def _http_status_error(status: int) -> httpx.HTTPStatusError:
    """Build an httpx.HTTPStatusError carrying ``status`` (no body), for classifier tests.

    :param status: The HTTP status code the error should report.
    :returns: An HTTPStatusError whose ``response.status_code`` is ``status``.
    """
    request = httpx.Request("GET", "https://example.test/x")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"HTTP {status}", request=request, response=response)


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (PlaywrightTimeoutError("slow"), True),
        (httpx.ConnectError("refused"), True),
        (scrape_details.PlaywrightError("net::ERR_NAME_NOT_RESOLVED at x"), True),
        (scrape_details.PlaywrightError("Target closed"), True),
        (scrape_details.PlaywrightError("Navigation failed because page crashed"), True),
        (_http_status_error(429), True),
        (_http_status_error(502), True),
        (_http_status_error(503), True),
        (_http_status_error(504), True),
        (_http_status_error(404), False),
        (_http_status_error(400), False),
        (scrape_details.PlaywrightError("strict mode violation: resolved to 2 elements"), False),
        (KeyError("title"), False),
        (ValueError("boom"), False),
    ],
)
def test_is_transient_classifies_failures(exc, expected):
    """
    [Local] scrape details: verifies the transient/non-transient classifier.

    Scenario:
        a representative exception is handed to ``is_transient``.

    Boundaries:
        - a Playwright timeout, an httpx transport error, an httpx HTTPStatusError with a
          retryable status (429/502/503/504), and a Playwright error naming a navigation/network
          fault are transient (retry-worthy); a non-retryable HTTP status (404/400), a
          non-navigation Playwright error, and plain bugs (KeyError/ValueError) are not.

    On failure, first check:
        - the ``_TRANSIENT_HTTP_STATUS`` set, the marker list ``_TRANSIENT_PLAYWRIGHT_MARKERS``,
          or the isinstance ladder in ``is_transient`` changed.
    """
    assert scrape_details.is_transient(exc) is expected


@pytest.mark.parametrize(
    ("status", "header", "expected"),
    [
        (429, "30", 30.0),
        (503, "7", 7.0),
        (429, None, None),
        (500, "30", None),
        (503, "not-a-date", None),
    ],
)
def test_httpx_retry_after_reads_only_429_503_header(status, header, expected):
    """
    [Local] scrape details: verifies httpx_retry_after reads Retry-After only for 429/503.

    Scenario:
        an httpx.HTTPStatusError is built with the given status and optional Retry-After header.

    Boundaries:
        - a 429/503 with a delta-seconds header yields that many seconds; a missing/unparseable
          header or a non-429/503 status (500) yields None so the caller falls back to jitter. The
          HTTP-date spelling is exercised by the politeness parser's own unit test.

    On failure, first check:
        - the ``_RETRY_AFTER_HTTP_STATUS`` membership check in httpx_retry_after, or its delegation
          to politeness.parse_retry_after.
    """
    request = httpx.Request("GET", "https://example.test/x")
    headers = {} if header is None else {"Retry-After": header}
    response = httpx.Response(status, request=request, headers=headers)
    exc = httpx.HTTPStatusError(f"HTTP {status}", request=request, response=response)

    assert scrape_details.httpx_retry_after(exc) == expected


def test_httpx_retry_after_ignores_non_http_errors():
    """
    [Local] scrape details: verifies httpx_retry_after returns None for a non-HTTP exception.

    Scenario:
        a plain exception (not an httpx.HTTPStatusError) is handed to httpx_retry_after.

    Boundaries:
        - only an httpx.HTTPStatusError can carry a Retry-After; anything else yields None.

    On failure, first check:
        - the ``isinstance(exc, httpx.HTTPStatusError)`` guard in httpx_retry_after.
    """
    assert scrape_details.httpx_retry_after(KeyError("x")) is None


async def test_get_leanpub_book_details_retries_on_429_then_succeeds():
    """
    [Local] scrape details: verifies the Leanpub detail fetch retries a 429 then succeeds.

    Scenario:
        the first httpx GET's raise_for_status raises HTTP 429 (transient); the second returns a
        well-formed payload.

    Boundaries:
        - the single-shot request used to fail on the first 429; it now backs off (sleep patched,
          no real wait) and re-attempts, so the result is SUCCESS and the client was called twice.

    On failure, first check:
        - the retry_async wrapper around _fetch in get_leanpub_book_details, or is_transient's
          HTTPStatusError(429) classification.
    """
    responses = [
        FakeResponse(raise_status_exc=_http_status_error(429)),
        FakeResponse(json_data=_leanpub_payload()),
    ]
    with _patch_httpx(responses=responses), patch.object(scrape_details.asyncio, "sleep", new=AsyncMock()):
        result = await get_leanpub_book_details(LEANPUB_URL)

    assert result.status is ScrapeStatus.SUCCESS
    assert result.book["title"] == "Example Book"


async def test_get_leanpub_book_details_404_not_retried():
    """
    [Local] scrape details: verifies a 404 is a hard failure, not retried.

    Scenario:
        every httpx GET's raise_for_status raises HTTP 404 (a non-retryable status).

    Boundaries:
        - a 404 is deterministic: it fails immediately as FAILED carrying only ``HTTP 404`` with no
          retry (the GET is attempted exactly once and no sleep occurs).

    On failure, first check:
        - is_transient wrongly classifying 404 as transient, or the except-HTTPStatusError branch.
    """
    responses = [FakeResponse(raise_status_exc=_http_status_error(404))]
    with (
        _patch_httpx(responses=responses) as _patch,
        patch.object(scrape_details.asyncio, "sleep", new=AsyncMock()) as mock_sleep,
    ):
        result = await get_leanpub_book_details(LEANPUB_URL)

    assert result.status is ScrapeStatus.FAILED
    assert result.error == "HTTP 404"
    assert mock_sleep.await_count == 0


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
        - the non-meta date branch reads the raw text ("August 16, 2012"), which the 03.1/04
          normalizer turns into the ISO date "2012-08-16" with its year 2012 derived from it.

    On failure, first check:
        - the PUBLICATION_DATE branch stopped reading via ``_text``, or normalize_date stopped
          converting the month-name date to ISO precision.
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
    assert result.book["publication_date"] == "2012-08-16"
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


# ---------------------------------------------------------------------------
# Rate-limiter integration (subtask 03.2/01)
# ---------------------------------------------------------------------------


class _RateLimiterFakeClock:
    """A deterministic clock whose sleep advances virtual time, for rate-limiter integration."""

    def __init__(self):
        """Start virtual time at zero."""
        self.now = 0.0

    def time(self) -> float:
        """Return the current virtual time in seconds."""
        return self.now

    async def sleep(self, duration: float):
        """Advance virtual time by duration seconds instead of waiting."""
        self.now += duration


async def test_scrape_book_acquires_before_goto():
    """
    [Local] scrape details: verifies the per-host rate limiter is acquired before page.goto.

    Scenario:
        Given a limiters registry whose acquire records an event and a page whose goto records one
        When scrape_book runs a single amazon attempt
        Then "acquire" is recorded strictly before "goto" (throttling gates every navigation)

    Boundaries:
        - Call-order recording only; timing/spacing is covered by the fake-clock throttle test
        - Single happy-path attempt; retry/error ordering is out of scope

    On failure, first check:
        - The ``await active_limiters.for_url(full_url).acquire()`` line sits immediately before
          ``page.goto`` in scrape_book
    """
    events: list[str] = []

    class _RecordingLimiter:
        """A limiter whose acquire records the call order instead of throttling."""

        async def acquire(self):
            """Record that a token was acquired."""
            events.append("acquire")

    class _RecordingLimiters:
        """A registry handing out the recording limiter for any URL."""

        def for_url(self, url: str) -> "_RecordingLimiter":
            """Return the recording limiter regardless of host."""
            return _RecordingLimiter()

    page = _amazon_page()

    async def _record_goto(*args: Any, **kwargs: Any):
        """Record that navigation happened, for call-order assertion."""
        events.append("goto")

    page.goto.side_effect = _record_goto
    browser = FakeBrowser(page=page)

    result = await scrape_book("https://www.amazon.com/dp/1098131029", browser, "amazon", None, _RecordingLimiters())

    assert result.status is ScrapeStatus.SUCCESS
    assert events == ["acquire", "goto"]


async def test_amazon_run_throttled_to_configured_rate():
    """
    [Local] scrape details: verifies a 20-URL amazon run navigates no faster than 0.5/s.

    Scenario:
        Given a DomainRateLimiters on the real RATE_LIMITS (amazon = 0.5 req/s, burst 1) driven by
        a fake clock, and 20 amazon detail scrapes run sequentially through it
        When each scrape records the simulated time of its page.goto
        Then navigations are spaced at least 2 s apart (the 20th lands at t=38), i.e. no faster
        than the configured 0.5 requests/second (success criterion of the subtask)

    Boundaries:
        - Fake clock only; no real time passes and no browser is launched
        - Exercises amazon's configured rate specifically, not the default fallback

    On failure, first check:
        - The amazon entry in parameters.RATE_LIMITS, or the token-bucket refill in acquire
    """
    clock = _RateLimiterFakeClock()
    limiters = DomainRateLimiters(RATE_LIMITS, DEFAULT_RATE_LIMIT, clock=clock.time, sleep=clock.sleep)

    goto_times: list[float] = []

    def _make_page() -> FakePage:
        """Build a fresh amazon page whose goto records the simulated navigation time."""
        page = _amazon_page()

        async def _record_goto(*args: Any, **kwargs: Any):
            """Record the fake-clock time at which this navigation occurred."""
            goto_times.append(clock.now)

        page.goto.side_effect = _record_goto
        return page

    browser = FakeBrowser(page_factory=lambda *a, **k: _make_page())

    for i in range(20):
        result = await scrape_book(f"https://www.amazon.com/dp/109813{i:04d}", browser, "amazon", None, limiters)
        assert result.status is ScrapeStatus.SUCCESS

    assert goto_times == [2.0 * i for i in range(20)]
    # Spacing is >= 2 s everywhere (0.5 req/s), i.e. no navigation is faster than the configured rate.
    for earlier, later in zip(goto_times, goto_times[1:], strict=False):
        assert later - earlier >= 2.0


async def test_amazon_robot_check_page_returns_blocked_without_retry():
    """
    [Local] scrape details: verifies an Amazon "Robot Check" CAPTCHA returns BLOCKED with no retry.

    Scenario:
        amazon serves a 200 interstitial whose title is "Robot Check" (no #productTitle).
    Boundaries:
        - _detect_bot_wall fires right after navigation, before the 404/title reads, so the result
          is BLOCKED (error "bot wall detected") not PARSE_ERROR; exactly one page is opened and
          navigated (no retry burns the budget on a deterministic wall), attempts == 1.
        - An injected per-run circuit-breaker registry keeps the module-level default untouched.

    On failure, first check:
        - _detect_bot_wall's title_contains branch, or the BLOCKED early-return in _attempt before
          the 404 check.
    """
    url = "https://www.amazon.com/dp/1484293428"
    page = FakePage(title="Robot Check")
    browser = FakeBrowser(page=page)
    breakers = DomainCircuitBreakers()

    result = await scrape_book(url, browser, "amazon", breakers=breakers)

    assert result.status is ScrapeStatus.BLOCKED
    assert result.error == "bot wall detected"
    assert result.attempts == 1
    assert browser.new_page.await_count == 1
    page.goto.assert_awaited_once()


async def test_cloudflare_challenge_detected_on_any_site():
    """
    [Local] scrape details: verifies the generic Cloudflare challenge is detected on a non-Amazon site.

    Scenario:
        oreilly serves a page containing the shared Cloudflare "#challenge-form" element.
    Boundaries:
        - oreilly's BOT_WALL is only the generic rule (a selector, no title), so detection comes
          from the selector-count branch; the result is BLOCKED with no retry (attempts == 1).

    On failure, first check:
        - GENERIC_BOT_WALL_RULES being spread into every site's BOT_WALL, or _detect_bot_wall's
          selector-count branch.
    """
    url = "https://www.oreilly.com/library/view/x/9780000000000/"
    page = FakePage(locators={"#challenge-form, #cf-challenge-running": FakeLocator("challenge")})
    browser = FakeBrowser(page=page)
    breakers = DomainCircuitBreakers()

    result = await scrape_book(url, browser, "oreilly", breakers=breakers)

    assert result.status is ScrapeStatus.BLOCKED
    assert result.error == "bot wall detected"
    assert result.attempts == 1


async def test_cloudflare_managed_challenge_detected_by_title():
    """
    [Local] scrape details: verifies the newer Cloudflare "Just a moment..." managed challenge is detected by title.

    Scenario:
        packtpub serves the managed-challenge interstitial titled "Just a moment..." and exposes
        none of the older #challenge-form / #cf-challenge-running elements.
    Boundaries:
        - detection comes from the title_contains branch (the page has no bot-wall selector match),
          so the result is BLOCKED with no retry (attempts == 1).

    On failure, first check:
        - the widened GENERIC_BOT_WALL_RULES "Just a moment" title rule, or _detect_bot_wall's
          title_contains branch.
    """
    url = "https://www.packtpub.com/en-us/search?query=python"
    page = FakePage(title="Just a moment...")
    browser = FakeBrowser(page=page)
    breakers = DomainCircuitBreakers()

    result = await scrape_book(url, browser, "packtpub", breakers=breakers)

    assert result.status is ScrapeStatus.BLOCKED
    assert result.error == "bot wall detected"
    assert result.attempts == 1


async def test_cloudflare_managed_challenge_detected_by_script_selector():
    """
    [Local] scrape details: verifies the Cloudflare managed challenge is detected by its challenge-platform script.

    Scenario:
        packtpub serves a managed-challenge page with no telltale title, but carrying the
        challenge-platform runtime script the widened rule matches on.
    Boundaries:
        - detection comes from the selector-count branch against script[src*='challenge-platform'],
          proving the script-tag rule works independently of the title; BLOCKED, no retry.

    On failure, first check:
        - the widened GENERIC_BOT_WALL_RULES challenge-platform selector, or _detect_bot_wall's
          selector-count branch.
    """
    url = "https://www.packtpub.com/en-us/search?query=python"
    page = FakePage(locators={"script[src*='challenge-platform']": FakeLocator("challenge-platform")})
    browser = FakeBrowser(page=page)
    breakers = DomainCircuitBreakers()

    result = await scrape_book(url, browser, "packtpub", breakers=breakers)

    assert result.status is ScrapeStatus.BLOCKED
    assert result.error == "bot wall detected"
    assert result.attempts == 1


async def test_open_circuit_skips_request_and_returns_blocked():
    """
    [Local] scrape details: verifies an already-open domain breaker short-circuits before any request.

    Scenario:
        an Amazon URL whose domain breaker has already opened (threshold 1, one prior block).
    Boundaries:
        - scrape_book's pre-request gate returns BLOCKED (error "circuit open") WITHOUT opening a
          page or navigating, so no request is made; new_page is never awaited.

    On failure, first check:
        - The breaker.is_open() gate at the top of scrape_book, before the Leanpub/Playwright split.
    """
    url = "https://www.amazon.com/dp/1484293428"
    browser = FakeBrowser(page=FakePage())
    breakers = DomainCircuitBreakers(threshold=1)
    breakers.for_url(url).record_blocked()  # Opens the breaker (threshold 1).

    result = await scrape_book(url, browser, "amazon", breakers=breakers)

    assert result.status is ScrapeStatus.BLOCKED
    assert result.error == "circuit open"
    browser.new_page.assert_not_awaited()


# ---------------------------------------------------------------------------
# LLM extraction fallback (task 04.1, subtask 05) - scrape_book hook
# ---------------------------------------------------------------------------


AMAZON_ISBN10_SEL = "#rpi-attribute-book_details-isbn10 .rpi-attribute-value span"
AMAZON_ISBN13_SEL = "#rpi-attribute-book_details-isbn13 .rpi-attribute-value span"
AMAZON_DATE_SEL = (
    "#rpi-attribute-book_details-publication_date .rpi-attribute-value span, "
    "#rpi-attribute-audiobook_details-release-date .rpi-attribute-value span"
)


def _fake_ai_message(text: str, stop_reason: str = "end_turn") -> SimpleNamespace:
    """A stand-in anthropic Message carrying one text block and a usage object."""
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        stop_details=None,
        usage=SimpleNamespace(
            input_tokens=11,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
            output_tokens=3,
        ),
    )


def _fake_ai_client(message: SimpleNamespace) -> MagicMock:
    """A mocked anthropic client whose count_tokens is under budget and create returns message."""
    client = MagicMock()
    client.messages.count_tokens.return_value = SimpleNamespace(input_tokens=50)
    client.messages.create.return_value = message
    return client


def _amazon_page_without_description(content: str) -> FakePage:
    """An amazon detail page with title/authors/ISBNs/date present but NO description match.

    ``content`` is what ``page.content()`` returns - the untrusted HTML the fallback prompt is
    built from and the evidence is checked against.
    """
    locators = {
        "#productTitle": FakeLocator("C++ Primer"),
        ".author a.a-link-normal": FakeLocator([{"text": "Stanley B. Lippman"}]),
        AMAZON_ISBN10_SEL: FakeLocator("0321714113"),
        AMAZON_ISBN13_SEL: FakeLocator("978-0321714114"),
        AMAZON_DATE_SEL: FakeLocator("September 24, 2022"),
    }
    return FakePage(locators=locators, title="C++ Primer - Amazon", content=content)


def _only_description_replay(content: str, description: str) -> dict[str, str]:
    """Build a replay map keyed by the exact prompt hash for a DESCRIPTION-only request."""
    instruction_block = ai_extraction.build_extraction_instruction_block("amazon", ["DESCRIPTION"])
    snapshot_block = ai_common.build_snapshot_block(ai_common.prune_html(content))
    key = ai_common.prompt_hash(instruction_block, snapshot_block)
    return {key: json.dumps({"fields": {"DESCRIPTION": {"value": description, "evidence": description}}})}


async def test_fallback_off_by_default_makes_no_call_and_reads_no_key(monkeypatch):
    """
    [Local] scrape details: with no ai_fallback runtime, the LLM path is never entered (off by default).

    Scenario:
        Given an amazon page missing its description and scrape_book called with ai_fallback=None
        And extract_missing_fields replaced by a tripwire that fails if ever called
        When scrape_book runs
        Then it succeeds with description None and ai_extracted_fields == [] and the tripwire never fired

    Boundaries:
        - ai_fallback defaults to None, so page.content() is never read for a prompt and no client/key is touched

    On failure, first check:
        - The ``ai_fallback is None`` guard at the top of _apply_ai_fallback.
    """

    async def _tripwire(*args, **kwargs):
        """Fail immediately if the fallback engine is ever reached with the flag off."""
        raise AssertionError("extract_missing_fields must not be called when the fallback is off")

    monkeypatch.setattr(scrape_details, "extract_missing_fields", _tripwire)
    page = _amazon_page_without_description("<html><body><p>No description element here.</p></body></html>")
    browser = FakeBrowser(page=page)

    result = await scrape_book("https://www.amazon.com/dp/0321714113/", browser, "amazon")

    assert result.status is ScrapeStatus.SUCCESS
    assert result.book["description"] is None
    assert result.book["ai_extracted_fields"] == []


async def test_missing_description_filled_and_marked_in_ai_extracted_fields():
    """
    [Local] scrape details: a drifted description is recovered via replay and marked (EXT-9 provenance).

    Scenario:
        Given an amazon page whose description selector matches nothing but whose HTML text carries
        the description, and an ai_fallback runtime in replay mode returning that text
        When scrape_book runs
        Then the result is SUCCESS, description holds the recovered text, and ai_extracted_fields == ["description"]

    Boundaries:
        - The replay key is the exact prompt hash for a DESCRIPTION-only request; no live client is used

    On failure, first check:
        - The merge in _apply_ai_fallback, or the dataclasses.replace that stamps ai_extracted_fields.
    """
    description = "A thorough, carefully crafted introduction to C++ for working programmers."
    content = f"<html><body><h1>C++ Primer</h1><div id='desc'>{description}</div></body></html>"
    page = _amazon_page_without_description(content)
    browser = FakeBrowser(page=page)
    runtime = scrape_details.AIFallbackRuntime(
        client=None,
        model="claude-sonnet-5",
        usage=UsageAccumulator(),
        cap=scrape_details.AICallCap(10),
        replay=_only_description_replay(content, description),
    )

    result = await scrape_book("https://www.amazon.com/dp/0321714113/", browser, "amazon", ai_fallback=runtime)

    assert result.status is ScrapeStatus.SUCCESS
    assert result.book["description"] == description
    assert result.book["ai_extracted_fields"] == ["description"]
    assert runtime.cap.used == 1
    assert runtime.filled == 1


async def test_title_missing_still_parse_error_with_fallback_on(monkeypatch):
    """
    [Local] scrape details: a missing title is PARSE_ERROR even with the fallback on (EXT-2).

    Scenario:
        Given an amazon page with no title and an ai_fallback runtime enabled
        And extract_missing_fields replaced by a tripwire that fails if ever called
        When scrape_book runs
        Then it returns PARSE_ERROR and the fallback was never invoked (title is never AI-filled)

    Boundaries:
        - The title check returns before the fallback hook, so the dedup hash can never be AI-authored

    On failure, first check:
        - The required-title early return precedes _apply_ai_fallback in scrape_book.
    """

    async def _tripwire(*args, **kwargs):
        """Fail immediately if the fallback runs when the required title is missing."""
        raise AssertionError("the fallback must never run when the required title is missing")

    monkeypatch.setattr(scrape_details, "extract_missing_fields", _tripwire)
    page = FakePage(locators={}, title="C++ Primer - Amazon", content="<html><body>no title element</body></html>")
    browser = FakeBrowser(page=page)
    runtime = scrape_details.AIFallbackRuntime(
        client=_fake_ai_client(_fake_ai_message("{}")),
        model="claude-sonnet-5",
        usage=UsageAccumulator(),
        cap=scrape_details.AICallCap(10),
    )

    result = await scrape_book("https://www.amazon.com/dp/0321714113/", browser, "amazon", ai_fallback=runtime)

    assert result.status is ScrapeStatus.PARSE_ERROR
    assert runtime.cap.used == 0


async def test_cap_stops_requests_after_n_pages_across_concurrent_batch():
    """
    [Local] scrape details: a shared cap bounds fallback requests across the concurrent batch (EXT-4).

    Scenario:
        Given five amazon pages each missing their description, scraped concurrently through ONE
        ai_fallback runtime whose cap is 2, backed by a live mock client
        When the five scrape_book coroutines run under asyncio.gather
        Then the mock client.messages.create is called exactly twice and the cap reports it was hit

    Boundaries:
        - try_reserve is atomic (no await between check and increment), so the batch cannot overshoot 2

    On failure, first check:
        - AICallCap.try_reserve, or that the one shared runtime/cap is threaded into every scrape_book.
    """
    client = _fake_ai_client(_fake_ai_message(json.dumps({"fields": {}})))
    runtime = scrape_details.AIFallbackRuntime(
        client=client,
        model="claude-sonnet-5",
        usage=UsageAccumulator(),
        cap=scrape_details.AICallCap(2),
    )

    def _page_factory(*args, **kwargs) -> FakePage:
        """Hand each scrape attempt its own title-only amazon page (description empty)."""
        return FakePage(
            locators={"#productTitle": FakeLocator("C++ Primer")},
            title="C++ Primer - Amazon",
            content="<html><body><p>only a paragraph, no description selector match</p></body></html>",
        )

    browser = FakeBrowser(page_factory=_page_factory)
    urls = [f"https://www.amazon.com/dp/000000000{i}/" for i in range(5)]
    results = await asyncio.gather(*(scrape_book(url, browser, "amazon", ai_fallback=runtime) for url in urls))

    assert all(r.status is ScrapeStatus.SUCCESS for r in results)
    assert client.messages.create.call_count == 2
    assert runtime.cap.used == 2
    assert runtime.cap.hit is True


def test_run_summary_line_has_counts_and_no_key():
    """
    [Local] scrape details: the AI-fallback run summary carries counts and usage but never the key (EXT-6).

    Scenario:
        Given an AIFallbackRuntime that made 2 requests, filled 1 field of 3 requested, hit its cap,
        and recorded a response usage
        When summary_line() is formatted
        Then it reports the request/filled/dropped counts, the cap-hit note, and the usage line - and
        contains neither the API key nor a price

    Boundaries:
        - summary_line is pure formatting over the shared cap + UsageAccumulator

    On failure, first check:
        - AIFallbackRuntime.summary_line / UsageAccumulator.usage_line.
    """
    usage = UsageAccumulator()
    usage.record(
        SimpleNamespace(input_tokens=1234, cache_read_input_tokens=0, cache_creation_input_tokens=0, output_tokens=7)
    )
    cap = scrape_details.AICallCap(2)
    assert cap.try_reserve() and cap.try_reserve() and not cap.try_reserve()  # used=2, hit
    runtime = scrape_details.AIFallbackRuntime(
        client=None, model="claude-sonnet-5", usage=usage, cap=cap, requested=3, filled=1
    )

    line = runtime.summary_line()

    assert "2 request(s) made" in line
    assert "per-run cap reached" in line
    assert "1 field(s) filled, 2 dropped" in line
    assert "input_tokens=1234" in line
    assert "sk-" not in line and "$" not in line


def test_create_ai_fallback_builds_runtime(monkeypatch):
    """
    [Local] scrape details: create_ai_fallback resolves key/model and builds a ready runtime.

    Scenario:
        Given ANTHROPIC_API_KEY set and anthropic.Anthropic patched to a sentinel client
        When create_ai_fallback(model="claude-x", max_pages=7) runs
        Then the returned runtime carries the resolved model, the sentinel client, and a cap of 7

    Boundaries:
        - No real client is constructed (anthropic.Anthropic is patched); the key is only passed through

    On failure, first check:
        - create_ai_fallback's resolve_api_key/resolve_model/AICallCap wiring.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-build-test")
    sentinel = object()
    monkeypatch.setattr(scrape_details.anthropic, "Anthropic", lambda **kwargs: sentinel)

    runtime = scrape_details.create_ai_fallback(model="claude-x", max_pages=7)

    assert runtime.model == "claude-x"
    assert runtime.client is sentinel
    assert all(runtime.cap.try_reserve() for _ in range(7)) and not runtime.cap.try_reserve()


async def test_ai_fallback_page_content_failure_recovers_nothing():
    """
    [Local] scrape details: a page.content() failure during the fallback degrades to deterministic-only (EXT-7).

    Scenario:
        Given an amazon page missing its description whose content() raises a Playwright error
        And the fallback enabled with a cap slot available
        When scrape_book runs
        Then it succeeds with description None and ai_extracted_fields [], reserving one cap slot but
        requesting nothing

    Boundaries:
        - try_reserve runs before page.content(), so a dead page still consumes the slot but recovers nothing

    On failure, first check:
        - The page.content() try/except in _apply_ai_fallback.
    """
    page = _amazon_page_without_description("<html><body>unused</body></html>")
    page.content = AsyncMock(side_effect=scrape_details.PlaywrightError("page crashed"))
    browser = FakeBrowser(page=page)
    runtime = scrape_details.AIFallbackRuntime(
        client=_fake_ai_client(_fake_ai_message("{}")),
        model="claude-sonnet-5",
        usage=UsageAccumulator(),
        cap=scrape_details.AICallCap(5),
    )

    result = await scrape_book("https://www.amazon.com/dp/0321714113/", browser, "amazon", ai_fallback=runtime)

    assert result.status is ScrapeStatus.SUCCESS
    assert result.book["description"] is None
    assert result.book["ai_extracted_fields"] == []
    assert runtime.cap.used == 1
    assert runtime.requested == 0


def test_create_ai_fallback_client_construction_failure_is_body_free(monkeypatch):
    """
    [Local] scrape details: a client-construction failure raises a body-free AIExtractionError (EXT-6).

    Scenario:
        Given ANTHROPIC_API_KEY set and anthropic.Anthropic patched to raise an HTTP-style error
        whose str() carries a secret body
        When create_ai_fallback runs
        Then it raises AIExtractionError summarizing only the class name (and status), not the body

    Boundaries:
        - Mirrors the rotate-cert / detect-schema "status code, never response body" contract

    On failure, first check:
        - The except branch in create_ai_fallback and ai_common.sdk_error_summary.
    """

    class _FakeStatusError(Exception):
        """A stand-in SDK error carrying an HTTP status and a secret body in its str()."""

        status_code = 503

        def __str__(self) -> str:
            """Return a secret body that must never reach the raised error message."""
            return "SECRET-BODY-should-never-surface"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ctor-fail")

    def _raise(**kwargs):
        """Stand in for anthropic.Anthropic construction failing."""
        raise _FakeStatusError()

    monkeypatch.setattr(scrape_details.anthropic, "Anthropic", _raise)

    with pytest.raises(ai_extraction.AIExtractionError) as excinfo:
        scrape_details.create_ai_fallback(model=None, max_pages=5)

    message = str(excinfo.value)
    assert "SECRET-BODY-should-never-surface" not in message
    assert "_FakeStatusError" in message and "503" in message


async def test_existing_goldens_unchanged_without_flag(monkeypatch):
    """
    [Local] scrape details: without the fallback, scrape_book output is byte-identical to today's goldens.

    Scenario:
        Given a fully populated amazon page and scrape_book called with no ai_fallback
        And extract_missing_fields replaced by a tripwire that fails if ever reached
        When scrape_book runs
        Then the record matches the deterministic happy-path fields exactly and ai_extracted_fields is []

    Boundaries:
        - Proves the off-by-default guarantee at the field level; the chromium detail goldens cover
          the full byte-for-byte record

    On failure, first check:
        - Any fallback code path running when ai_fallback is None, or ai_extracted_fields not defaulting to [].
    """

    async def _tripwire(*args, **kwargs):
        """Fail immediately if the fallback engine runs for an off-by-default run."""
        raise AssertionError("the fallback must not run without --ai-fallback")

    monkeypatch.setattr(scrape_details, "extract_missing_fields", _tripwire)
    url = "https://www.amazon.com/dp/B0ABCDEFGH/ref=sr_1_1"
    browser = FakeBrowser(page=_amazon_page())

    result = await scrape_book(url, browser, "amazon")

    assert result.status is ScrapeStatus.SUCCESS
    book = result.book
    assert book["title"] == "Great Python Book"
    assert book["authors"] == ["Jane Doe", "John Smith"]
    assert book["isbn10"] == "0136816487"
    assert book["isbn13"] == "9780136816485"
    assert book["description"] == "A great book about Python."
    assert book["ai_extracted_fields"] == []


# ---------------------------------------------------------------------------
# Per-task scrape log context (task 07.0/04)
# ---------------------------------------------------------------------------


async def test_get_leanpub_book_details_sets_and_resets_scrape_context():
    """
    [Local] get_leanpub_book_details: the scrape context is leanpub:<slug> during the fetch.

    Scenario:
        Given a mocked httpx client whose get() records the live scrape_context value
        When get_leanpub_book_details runs for the example book endpoint
        Then the in-flight context is ``leanpub:example`` and it is reset to ``-`` afterward.

    Boundaries:
        - httpx mocked; the context is read at the moment the fetch runs (inside the wrapper)
        - Proves the wrapper sets the context and the token is reset on return

    On failure, first check:
        - scrape_log_context(LEANPUB_SITE, url) wrapping _get_leanpub_book_details in scrape_details.py
    """
    captured = []

    def _record_get(*args, **kwargs) -> FakeResponse:
        """Record the live scrape context, then return the canned Leanpub payload."""
        captured.append(book_utils.scrape_context.get())
        return FakeResponse(json_data=_leanpub_payload())

    client = MagicMock()
    client.get = AsyncMock(side_effect=_record_get)
    client.aclose = AsyncMock()

    assert book_utils.scrape_context.get() == "-"
    with patch.object(scrape_details.httpx, "AsyncClient", MagicMock(return_value=client)):
        result = await get_leanpub_book_details(LEANPUB_URL)

    assert result.status is ScrapeStatus.SUCCESS
    assert captured == ["leanpub:example"]
    assert book_utils.scrape_context.get() == "-"


async def test_scrape_book_sets_and_resets_scrape_context():
    """
    [Local] scrape_book: the scrape context is amazon:<asin> during the scrape, reset afterward.

    Scenario:
        Given a storage backend whose book_exists_by_hash records the live scrape_context value
        When scrape_book runs for an amazon /dp/<ASIN> URL
        Then the in-flight context is ``amazon:B0ABCDEFGH`` and it is reset to ``-`` afterward.

    Boundaries:
        - Playwright faked; the context is read during the inline duplicate check
        - Proves the ASIN short-id path and the token reset on return

    On failure, first check:
        - scrape_log_context(site, url) wrapping _scrape_book and scrape_short_id's ASIN branch
    """
    captured = []

    def _record_hash(_hash) -> bool:
        """Record the live scrape context, then report the book as new."""
        captured.append(book_utils.scrape_context.get())
        return False

    storage = MagicMock()
    storage.book_exists_by_hash = MagicMock(side_effect=_record_hash)
    browser = FakeBrowser(page=_amazon_page())

    assert book_utils.scrape_context.get() == "-"
    result = await scrape_book("https://www.amazon.com/dp/B0ABCDEFGH/", browser, "amazon", storage_backend=storage)

    assert result.status is ScrapeStatus.SUCCESS
    assert captured == ["amazon:B0ABCDEFGH"]
    assert book_utils.scrape_context.get() == "-"
