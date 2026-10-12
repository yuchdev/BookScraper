from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import urljoin

import httpx
from playwright.async_api import Browser, Error as PlaywrightError, Page, Route, TimeoutError

from ..backends import StorageBackend
from ..book_utils import print_log
from . import normalize
from .browser import new_scrape_context
from .parameters import site_constants
from .politeness import (
    CircuitBreaker,
    DomainCircuitBreakers,
    DomainRateLimiters,
    RobotsCache,
    RobotsFetcher,
    RobotsResponse,
    default_breakers,
    default_limiters,
    parse_retry_after,
    retry_async,
)
from .results import ScrapeResult, ScrapeStatus

# The site identifier carried on every Leanpub ScrapeResult (the httpx/JSON path).
LEANPUB_SITE = "leanpub"

# Wall-clock budget for a single robots.txt fetch (seconds), per the subtask spec.
ROBOTS_FETCH_TIMEOUT_S = 10.0

# Hard cap on the robots.txt body we will buffer (bytes). A real robots.txt is a few KB; 512 KB is
# already pathological, and a hostile or compression-bombed response could otherwise expand without
# bound in memory. Exceeding this is treated as a fetch failure (fail-closed, like any fetch error).
ROBOTS_MAX_BYTES = 512 * 1024

# User-agent presented to the robots.txt check on the httpx/JSON Leanpub paths, which send no
# custom browser user-agent. "*" matches the robots.txt catch-all group - the conservative default.
ROBOTS_HTTPX_USER_AGENT = "*"


def build_robots_fetcher(client: httpx.AsyncClient) -> RobotsFetcher:
    """Return a :data:`RobotsFetcher` bound to the shared ``client`` for a :class:`RobotsCache`.

    The returned coroutine GETs a ``robots.txt`` URL with a 10 s timeout and hands back only its
    status code and body as a transport-agnostic :class:`RobotsResponse`, keeping every httpx
    detail out of :mod:`politeness` (which must carry no transport import).

    :param client: The run's shared :class:`httpx.AsyncClient` to fetch ``robots.txt`` through.
    :returns: A coroutine suitable as the ``fetcher`` argument of :class:`RobotsCache`.
    """

    async def _fetch(robots_url: str) -> RobotsResponse:
        """GET ``robots_url`` and return its status and body as a :class:`RobotsResponse`.

        The body is streamed and buffered only up to :data:`ROBOTS_MAX_BYTES`; a response that
        exceeds that cap raises :class:`ValueError`, which :class:`RobotsCache` treats as a fetch
        failure and fails closed - a hostile or compression-bombed robots.txt can never expand
        without bound in memory.

        :param robots_url: The absolute ``robots.txt`` URL to fetch.
        :returns: The fetched status code and decoded body.
        :raises ValueError: If the (decoded) body exceeds :data:`ROBOTS_MAX_BYTES`.
        """
        async with client.stream("GET", robots_url, timeout=httpx.Timeout(ROBOTS_FETCH_TIMEOUT_S)) as response:
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > ROBOTS_MAX_BYTES:
                    raise ValueError(f"robots.txt at {robots_url} exceeds {ROBOTS_MAX_BYTES} bytes; refusing to buffer")
                chunks.append(chunk)
        body = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
        return RobotsResponse(status_code=response.status_code, text=body)

    return _fetch


# Per-read / per-attempt timeout budgets (milliseconds) and the retry cap. These are module
# constants so a later CLI (subtask 05.0) can override them; required fields get the longer
# FIELD_TIMEOUT_MS, optional ones the shorter OPTIONAL_FIELD_TIMEOUT_MS.
NAVIGATION_TIMEOUT_MS = 60_000
FIELD_TIMEOUT_MS = 10_000
OPTIONAL_FIELD_TIMEOUT_MS = 2_000
MAX_ATTEMPTS = 3

# Shared backoff parameters handed to ``politeness.retry_async`` on every retried request (the
# Playwright detail scrape and both Leanpub httpx/JSON paths). The base and cap match the
# ``retry_async`` defaults; they are named here so a later CLI (subtask 05.0) can override them.
RETRY_BASE_DELAY_S = 2.0
RETRY_MAX_DELAY_S = 60.0

# HTTP status codes that mark a *transient* server-side failure worth retrying, and the subset
# that carries a meaningful ``Retry-After`` header (429 Too Many Requests / 503 Service
# Unavailable). 502/504 are transient but their ``Retry-After`` - if any - is not honored.
_TRANSIENT_HTTP_STATUS: frozenset[int] = frozenset({429, 502, 503, 504})
_RETRY_AFTER_HTTP_STATUS: frozenset[int] = frozenset({429, 503})

# Upper bound on the number of tags kept per book. A page-controlled tag list is untrusted
# input, so the collection is capped (after de-duplication) rather than stored unbounded.
TAGS_LIMIT = 50

# The ``site_constants`` detail-page selector keys that ``scrape_book`` reads by their
# canonical name. This is the static counterpart of
# ``schema_detection.detail_selector_fields()``; the sanity test
# ``test_every_detail_selector_key_is_consumed`` compares the two so a selector defined in
# ``parameters.py`` can never silently go unread again. The documented exceptions (the
# meta-tag author/date variants, read via their own dedicated branches, and the unused
# alternate ASIN locator) live in that test's allow-list, each with a one-line reason.
CONSUMED_DETAIL_KEYS: frozenset[str] = frozenset(
    {
        "BOOK_TITLE",
        "AUTHORS",
        "AUTHORS_ALT",
        "ISBN10",
        "ISBN13",
        "PUBLICATION_DATE",
        "DESCRIPTION",
        "DESCRIPTION_ALT",
        "TAGS",
        "DETAILS_BUTTON",
        "READ_MORE_LINK",
    }
)

# Substrings (compared case-insensitively) in a Playwright error message that mark a
# *transient* navigation/network fault worth retrying - as opposed to a deterministic
# selector/parse bug that would fail identically on every attempt.
_TRANSIENT_PLAYWRIGHT_MARKERS: tuple[str, ...] = ("net::err_", "target closed", "navigation failed")

# Get a logger instance specifically for this module
module_logger = logging.getLogger("scrape_details")


def is_transient(exc: BaseException) -> bool:
    """Return whether ``exc`` is a transient failure a retry could plausibly recover from.

    This is the single transient classifier shared by the detail scrape and both Leanpub paths
    (and re-used by :mod:`.search_utils`). Transient failures are a Playwright navigation timeout
    (:class:`playwright.async_api.TimeoutError`), an :mod:`httpx` ``HTTPStatusError`` carrying a
    retryable status (429/502/503/504), an :mod:`httpx` transport error, or a
    :class:`playwright.async_api.Error` whose message names a navigation/network fault
    (``net::ERR_*`` / ``Target closed`` / ``Navigation failed``). Deterministic bugs -
    ``KeyError``, ``AttributeError``, ``ValueError``, ``TypeError``, a malformed selector, or a
    non-retryable HTTP status such as 404 - are **not** transient and must fail immediately rather
    than waste the retry budget.

    :param exc: The exception raised while scraping a single attempt.
    :returns: ``True`` iff retrying the attempt could plausibly succeed.
    """
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _TRANSIENT_HTTP_STATUS
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, PlaywrightError):
        message = str(exc).lower()
        return any(marker in message for marker in _TRANSIENT_PLAYWRIGHT_MARKERS)
    return False


def httpx_retry_after(exc: BaseException) -> Optional[float]:
    """Return the ``Retry-After`` delay (seconds) an :mod:`httpx` 429/503 response requests.

    The transport-specific wrapper around :func:`politeness.parse_retry_after`: it reads the raw
    header off an :class:`httpx.HTTPStatusError` with status 429 or 503 and parses either
    spelling (delta-seconds or HTTP-date). Any other exception, a missing header, or an
    unparseable value yields ``None``, so :func:`politeness.retry_async` falls back to jittered
    backoff. The header value is the only thing read; the response body never is.

    :param exc: The exception raised by a failed request.
    :returns: The server-requested delay in seconds, or ``None`` when none applies.
    """
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in _RETRY_AFTER_HTTP_STATUS:
        return parse_retry_after(exc.response.headers.get("Retry-After"))
    return None


async def _text(page: Page, selector: str, *, timeout_ms: int, first: bool = True) -> Optional[str]:
    """Return stripped text for ``selector`` within ``timeout_ms``, or ``None`` if absent.

    This is the single per-field text-extraction primitive. It never raises for a missing or
    slow element - a Playwright timeout or a malformed-selector error both resolve to
    ``None`` - so a missing optional field can never abort a scrape or trigger a retry.

    :param page: The Playwright page to query.
    :param selector: The CSS selector to read.
    :param timeout_ms: The per-read wall-clock budget in milliseconds.
    :param first: Whether to read only the first match (``True``) or the locator as given.
    :returns: The element's stripped text, or ``None`` on timeout/absence/empty text.
    """
    locator = page.locator(selector)
    if first:
        locator = locator.first
    try:
        text = await locator.text_content(timeout=timeout_ms)
    except PlaywrightError as exc:
        module_logger.debug(f"No text for selector {selector!r} on {page.url}: {exc}")
        return None
    return text.strip() if text else None


async def _attr(page: Page, selector: str, name: str, *, timeout_ms: int, first: bool = True) -> Optional[str]:
    """Return a stripped attribute value for ``selector`` within ``timeout_ms``, or ``None``.

    The attribute sibling of :func:`_text` (used for e.g. a ``<meta content="...">`` date). Like
    :func:`_text`, it never raises for a missing/slow element or an absent attribute.

    :param page: The Playwright page to query.
    :param selector: The CSS selector to read.
    :param name: The attribute name to read (e.g. ``"content"``).
    :param timeout_ms: The per-read wall-clock budget in milliseconds.
    :param first: Whether to read only the first match (``True``) or the locator as given.
    :returns: The attribute's stripped value, or ``None`` on timeout/absence/empty value.
    """
    locator = page.locator(selector)
    if first:
        locator = locator.first
    try:
        value = await locator.get_attribute(name, timeout=timeout_ms)
    except PlaywrightError as exc:
        module_logger.debug(f"No attribute {name!r} for selector {selector!r} on {page.url}: {exc}")
        return None
    return value.strip() if value else None


async def _collect_texts(page: Page, selector: str, *, timeout_ms: int) -> list[str]:
    """Return the stripped, non-empty text of every element matching ``selector``.

    Used for multi-valued fields (authors). Every per-element read is bounded by ``timeout_ms``
    and any Playwright failure on an individual element is skipped, so the helper never raises
    for a missing or detached element.

    :param page: The Playwright page to query.
    :param selector: The CSS selector whose matches to read.
    :param timeout_ms: The per-read wall-clock budget in milliseconds.
    :returns: A list of stripped text values (possibly empty).
    """
    texts: list[str] = []
    try:
        elements = await page.locator(selector).all()
    except PlaywrightError as exc:
        module_logger.debug(f"No matches for selector {selector!r} on {page.url}: {exc}")
        return texts
    for element in elements:
        try:
            text = await element.text_content(timeout=timeout_ms)
        except PlaywrightError as exc:
            module_logger.debug(f"Skipping unreadable element for selector {selector!r} on {page.url}: {exc}")
            continue
        if text and text.strip():
            texts.append(text.strip())
    return texts


def _selector(selectors: dict, key: str) -> Optional[str]:
    """Return the configured CSS selector for ``key``, or ``None`` if it is "not configured".

    A selector value of ``""`` (e.g. packtpub's ``READ_MORE_LINK`` sentinel for "no expander")
    and a missing/``None`` value are treated identically: both mean "this site has no selector
    for this field", so callers do not have to distinguish the two spellings everywhere.

    :param selectors: The site's ``site_constants`` entry.
    :param key: The selector key to look up.
    :returns: The non-empty selector string, or ``None`` when unset/empty.
    """
    value = selectors.get(key)
    return value if value else None


async def _texts(page: Page, selector: str, *, timeout_ms: int) -> list[str]:
    """Return every match's stripped text, de-duplicated in order and capped at ``TAGS_LIMIT``.

    Built on :func:`_collect_texts` (so it inherits the same never-raise, per-element timeout
    behaviour) for multi-valued fields where duplicates are meaningless and the page controls
    the element count - tags/topics. Empty strings are already dropped by :func:`_collect_texts`;
    this helper additionally removes later duplicates while preserving first-seen order and caps
    the result so a page cannot inflate the stored list without bound.

    :param page: The Playwright page to query.
    :param selector: The CSS selector whose matches to read.
    :param timeout_ms: The per-read wall-clock budget in milliseconds.
    :returns: An order-preserving, de-duplicated list of at most ``TAGS_LIMIT`` texts.
    """
    seen: set[str] = set()
    unique: list[str] = []
    for text in await _collect_texts(page, selector, timeout_ms=timeout_ms):
        if text in seen:
            continue
        seen.add(text)
        unique.append(text)
        if len(unique) >= TAGS_LIMIT:
            break
    return unique


async def route_handler(route: Route):
    """
    Playwright route handler that lets document requests through and aborts the rest.

    Blocking non-document resources (images, fonts, stylesheets) speeds up page loads.

    :param route: The intercepted Playwright route to continue or abort.
    """
    request = route.request
    # Allow document (HTML page) requests to go through.
    # Block other resource types (images, fonts, stylesheets, etc.) to speed up scraping.
    if request.resource_type == "document":
        await route.continue_()
    else:
        await route.abort()


async def _detect_bot_wall(page: Page, site: str) -> bool:
    """Return whether the loaded ``page`` is ``site``'s bot-wall / anti-bot interstitial.

    Evaluates the site's ``BOT_WALL`` rules (``parameters.site_constants[site]["BOT_WALL"]``, which
    already includes the shared :data:`parameters.GENERIC_BOT_WALL_RULES` Cloudflare rule): a
    ``{"title_contains": str}`` rule matches when that substring is in the page title
    (case-insensitively), a ``{"selector": str}`` rule matches when the selector resolves to at
    least one element. The first matching rule wins. A page-read failure (a dead page, a malformed
    selector) is swallowed as a non-match - detection is best-effort and must never raise into the
    scrape - so an inconclusive check lets the normal title/404 path run and surface its own result.

    This is checked right after navigation and BEFORE the 404 check, so a 200 CAPTCHA page (which
    has none of the real fields) is reported as :attr:`ScrapeStatus.BLOCKED` rather than being
    misread as a parse error. Reused by :mod:`.schema_detection` to refuse a bot-wall sample page.

    :param page: The freshly-navigated Playwright page to inspect.
    :param site: The site identifier whose ``BOT_WALL`` rules to apply.
    :returns: ``True`` iff a bot-wall rule matched; ``False`` otherwise (including on read errors).
    """
    rules = site_constants.get(site, {}).get("BOT_WALL", [])
    if not rules:
        return False
    title_needles = [rule["title_contains"] for rule in rules if "title_contains" in rule]
    if title_needles:
        try:
            page_title = (await page.title() or "").lower()
        except PlaywrightError as exc:
            module_logger.debug(f"Bot-wall title read failed on {page.url}: {exc}")
            page_title = ""
        if any(needle.lower() in page_title for needle in title_needles):
            return True
    for rule in rules:
        selector = rule.get("selector")
        if not selector:
            continue
        try:
            if await page.locator(selector).count() > 0:
                return True
        except PlaywrightError as exc:
            module_logger.debug(f"Bot-wall selector {selector!r} failed on {page.url}: {exc}")
    return False


def _note_circuit_result(breaker: CircuitBreaker, result: ScrapeResult, url: str):
    """Feed one scrape outcome to ``url``'s :class:`CircuitBreaker` and warn if it just opened.

    A :attr:`ScrapeStatus.BLOCKED` result (a detected bot wall) is recorded as a block; if that
    pushes the domain's breaker over its threshold this emits the single ``print_log`` warning the
    subtask requires, naming the domain. A :attr:`ScrapeStatus.SUCCESS` resets the block counter.
    Every other status leaves the breaker untouched. The "circuit open" skip result is produced by
    the caller's pre-request gate and never reaches here, so an open breaker is not re-recorded.

    :param breaker: The domain's circuit breaker.
    :param result: The terminal :class:`ScrapeResult` of this scrape attempt.
    :param url: The scraped URL, used only to name the domain in the warning.
    """
    if result.status is ScrapeStatus.BLOCKED:
        if breaker.record_blocked():
            domain = DomainRateLimiters.registrable_host(url) or url
            module_logger.warning(f"Circuit breaker opened for {domain} after repeated bot-wall blocks.")
            print_log(
                f"Bot-wall circuit opened for '{domain}'; all further URLs for this domain will be "
                "skipped (returned BLOCKED without a request) for the rest of the run.",
                "warning",
            )
    elif result.status is ScrapeStatus.SUCCESS:
        breaker.record_success()


@asynccontextmanager
async def _attempt_page(browser: Browser) -> AsyncIterator[Page]:
    """Yield a fresh page on its own context for a single scrape attempt, closing both on exit.

    Each attempt gets its own :class:`BrowserContext` (via :func:`browser.new_scrape_context`, so
    the UA/identity matches what schema-detection sees) and one page on it. Isolating the context
    per attempt means concurrent scrapes never share a cookie jar. The page and then the context
    are closed on **every** exit path - normal return, duplicate/404 short-circuit, timeout, or any
    other exception - so an attempt can never leak either. A failure to close (for example when the
    browser has already crashed) is logged at debug level and swallowed so it never masks the
    original exception that is unwinding the ``with`` block.

    :param browser: The Playwright browser to open the context and page on.
    :returns: An async context manager yielding the attempt's :class:`Page`.
    """
    context = await new_scrape_context(browser)
    page = await context.new_page()
    try:
        yield page
    finally:
        try:
            await page.close()
        except Exception as exc:  # noqa: X002 - cleanup isolation boundary: a page-close failure (any type; e.g. "browser already closed") must never mask the original error unwinding the with-block
            module_logger.debug(f"Ignoring page-close failure: {exc}")
        try:
            await context.close()
        except Exception as exc:  # noqa: X002 - cleanup isolation boundary: a context-close failure (any type; e.g. "browser already closed") must never mask the original error unwinding the with-block
            module_logger.debug(f"Ignoring context-close failure: {exc}")


async def get_leanpub_book_details(
    url: str,
    client: Optional[httpx.AsyncClient] = None,
    limiters: Optional[DomainRateLimiters] = None,
    robots: Optional[RobotsCache] = None,
) -> ScrapeResult:
    """Fetch detailed information for a single Leanpub book from its JSON:API endpoint.

    Every path returns a :class:`ScrapeResult`: a :attr:`ScrapeStatus.SUCCESS` carrying the
    book document, a :attr:`ScrapeStatus.PARSE_ERROR` when the response is well-formed but
    missing the ``data`` block or the ``title`` the record cannot exist without, or a
    :attr:`ScrapeStatus.FAILED` carrying a short, secret-free ``error``. The raw HTTP response
    body never reaches ``error``, the log, or the console: an HTTP status surfaces only its
    code (``HTTP <code>``), a transport error only its exception class name.

    When ``client`` is supplied it is reused (so :mod:`commands.search` can share one pooled
    client across hundreds of concurrent calls) and left open for the caller to close; when it
    is ``None`` a short-lived client is created and closed for this single call.

    :param url: The API endpoint of the book (e.g.
        ``https://leanpub.com/api/v1/cache/books/quickguidetodatasciencewithpython.json``).
    :param client: An existing :class:`httpx.AsyncClient` to reuse, or ``None`` to create and
        close a dedicated one for this call.
    :param limiters: The per-run :class:`DomainRateLimiters` to throttle against, or ``None`` to
        use the shared module-level instance.
    :param robots: The per-run :class:`RobotsCache` to consult before fetching, or ``None`` to
        skip the robots.txt check (e.g. under ``--ignore-robots``).
    :returns: A :class:`ScrapeResult` for the scrape attempt.
    """
    active_limiters = limiters if limiters is not None else default_limiters()

    # robots.txt gate (fetched once per host per run). The Leanpub JSON:API paths go through the
    # same check as every other URL; if robots.txt disallows them, the check wins.
    if robots is not None and not await robots.allowed(url, ROBOTS_HTTPX_USER_AGENT):
        module_logger.info(f"Skipping Leanpub {url}: disallowed by robots.txt.")
        print_log(f"Skipping {url}: disallowed by robots.txt.", "warning")
        return ScrapeResult(ScrapeStatus.SKIPPED_ROBOTS, url=url, site=LEANPUB_SITE, error="disallowed by robots.txt")

    params = {"include": "accepted_authors"}  # 'included' authors are resolved from this expansion.
    owns_client = client is None
    active_client = (
        client
        if client is not None
        else httpx.AsyncClient(timeout=httpx.Timeout(30.0), limits=httpx.Limits(max_connections=10))
    )

    async def _fetch() -> httpx.Response:
        """Throttle, GET the book endpoint, and raise on a non-2xx status (one retry attempt).

        :returns: The successful :class:`httpx.Response` for the book endpoint.
        """
        await active_limiters.for_url(url).acquire()
        response = await active_client.get(url, params=params)
        response.raise_for_status()
        return response

    try:
        # Retry transient failures (429/502/503/504 and transport errors) with shared jittered
        # backoff, honoring any ``Retry-After`` on a 429/503 - so a single rate-limit response no
        # longer fails the whole fetch. A deterministic HTTP error (e.g. 404) is not retried.
        response = await retry_async(
            _fetch,
            attempts=MAX_ATTEMPTS,
            base_delay=RETRY_BASE_DELAY_S,
            max_delay=RETRY_MAX_DELAY_S,
            is_retryable=is_transient,
            retry_after=httpx_retry_after,
            sleep=asyncio.sleep,
        )
        response_data = response.json()

        book_data = response_data.get("data")
        if not book_data:
            module_logger.warning(f"Leanpub response for {url} contained no 'data' block.")
            print_log(f"No 'data' found in Leanpub response for {url}.", "error")
            return ScrapeResult(ScrapeStatus.PARSE_ERROR, url=url, site=LEANPUB_SITE, error="no 'data' in response")

        attributes = book_data.get("attributes", {})
        title = attributes.get("title")
        if not title:
            module_logger.warning(f"Leanpub response for {url} had no title.")
            print_log(f"Leanpub response for {url} had no title.", "error")
            return ScrapeResult(ScrapeStatus.PARSE_ERROR, url=url, site=LEANPUB_SITE, error="title not found")

        relationships = book_data.get("relationships", {})
        included = response_data.get("included", [])  # Relevant for authors

        # Resolve authors: the accepted_authors relationship references Author resources in
        # the included[] expansion.
        author_lookup = {}
        for item in included:
            if item.get("type") == "Author":
                author_id = item.get("id")
                author_name = item.get("attributes", {}).get("name")
                if author_id and author_name:
                    author_lookup[author_id] = author_name

        authors: list[str] = []
        accepted_authors_data = relationships.get("accepted_authors", {}).get("data", [])
        for author_rel in accepted_authors_data:
            author_id = author_rel.get("id")
            if author_id and author_id in author_lookup:
                authors.append(author_lookup[author_id])

        # Resolve categories from data.attributes.categories.
        categories: list[str] = []
        for category_item in attributes.get("categories", []):
            category_name = category_item.get("name")
            if category_name:
                categories.append(category_name)

        # Build the canonical record: date normalization, the human-facing URL, the HTML
        # about_the_book -> plain-text description, and the (unchanged) hash input all live in
        # the normalizer (see scraping/normalize.py).
        record = normalize.from_leanpub_api(attributes, book_data.get("id"), authors, categories)

        return ScrapeResult(ScrapeStatus.SUCCESS, url=url, site=LEANPUB_SITE, book=record.to_document())

    except httpx.HTTPStatusError as exc:
        # Only the status code is surfaced - never exc.response.text (the raw body).
        status_code = exc.response.status_code
        module_logger.warning(f"Leanpub HTTP error for {url}: HTTP {status_code}")
        print_log(f"Leanpub HTTP error for {url}: HTTP {status_code}.", "error")
        return ScrapeResult(ScrapeStatus.FAILED, url=url, site=LEANPUB_SITE, error=f"HTTP {status_code}")
    except httpx.RequestError as exc:
        # Transport failures surface only the exception class name, never the request detail.
        exc_name = type(exc).__name__
        module_logger.warning(f"Leanpub transport error for {url}: {exc_name}")
        print_log(f"Leanpub transport error for {url}: {exc_name}.", "error")
        return ScrapeResult(ScrapeStatus.FAILED, url=url, site=LEANPUB_SITE, error=exc_name)
    except json.JSONDecodeError:
        # The response body is never logged (it may be an error page); only that it was unparseable.
        module_logger.warning(f"Leanpub response for {url} was not valid JSON.")
        print_log(f"Leanpub response for {url} was not valid JSON.", "error")
        return ScrapeResult(ScrapeStatus.FAILED, url=url, site=LEANPUB_SITE, error="invalid JSON")
    except Exception as exc:  # noqa: X002 - fail-soft boundary: get_leanpub_book_details must always return a ScrapeResult (never raise); any unexpected JSON-shape/processing error becomes a FAILED result carrying only the exception class name
        exc_name = type(exc).__name__
        module_logger.error(f"Unexpected error scraping Leanpub book {url}: {exc_name}")
        print_log(f"Unexpected error scraping Leanpub book {url}.", "error")
        return ScrapeResult(ScrapeStatus.FAILED, url=url, site=LEANPUB_SITE, error=exc_name)
    finally:
        # Only a client we created is ours to close; a caller-supplied one outlives this call.
        if owns_client:
            await active_client.aclose()


async def scrape_book(
    url: str,
    browser: Browser,
    site: str,
    storage_backend: Optional[StorageBackend] = None,
    limiters: Optional[DomainRateLimiters] = None,
    robots: Optional[RobotsCache] = None,
    breakers: Optional[DomainCircuitBreakers] = None,
    nav_timeout_ms: Optional[int] = None,
) -> ScrapeResult:
    """Scrape book details from a URL, handle retries, and check for duplicates.

    Every path returns a :class:`ScrapeResult` - a :attr:`ScrapeStatus.SUCCESS` carrying the
    scraped book document, or one of the non-success statuses (:attr:`ScrapeStatus.DUPLICATE`,
    :attr:`ScrapeStatus.NOT_FOUND`, :attr:`ScrapeStatus.TIMEOUT`, or :attr:`ScrapeStatus.FAILED`)
    carrying a short, secret-free ``error``.

    :param url: The URL of the book page to scrape (can be relative).
    :param browser: The Playwright browser instance.
    :param site: The identifier for the website (e.g. ``"amazon"``, ``"leanpub"``).
    :param storage_backend: The :class:`StorageBackend` (mongo or json) to check duplicates
        against, or ``None`` to skip the inline duplicate check.
    :param limiters: The per-run :class:`DomainRateLimiters` to throttle against, or ``None`` to
        use the shared module-level instance.
    :param robots: The per-run :class:`RobotsCache` to consult before fetching, or ``None`` to
        skip the robots.txt check (e.g. under ``--ignore-robots``).
    :param breakers: The per-run :class:`DomainCircuitBreakers` tracking repeated bot-wall blocks,
        or ``None`` to use the shared module-level instance. When a domain's breaker is open, this
        returns :attr:`ScrapeStatus.BLOCKED` (``error="circuit open"``) with no request.
    :param nav_timeout_ms: Per-page navigation timeout in milliseconds (the resolved
        ``--nav-timeout``), or ``None`` to use the :data:`NAVIGATION_TIMEOUT_MS` default.
    :returns: A :class:`ScrapeResult` for the scrape attempt.
    """
    active_limiters = limiters if limiters is not None else default_limiters()
    active_breakers = breakers if breakers is not None else default_breakers()
    active_nav_timeout_ms = nav_timeout_ms if nav_timeout_ms is not None else NAVIGATION_TIMEOUT_MS

    # Circuit-breaker gate: once a domain's breaker has opened (threshold consecutive bot-wall
    # blocks), every further URL for it is skipped WITHOUT a request for the rest of the run.
    breaker = active_breakers.for_url(url)
    if breaker.is_open():
        module_logger.info(f"Circuit open for {url}; skipping without a request.")
        return ScrapeResult(ScrapeStatus.BLOCKED, url=url, site=site, error="circuit open")

    # Leanpub is a pure httpx/JSON path - dispatch it before any Playwright page is created,
    # so it never opens (and therefore never leaks) a browser page.
    if site == "leanpub":
        module_logger.debug(f"Leanpub book; using the JSON API for {url}.")
        return await get_leanpub_book_details(url, limiters=active_limiters, robots=robots)

    attempts_made = 0

    async def _attempt() -> ScrapeResult:
        """Run one scrape attempt: navigate, extract, and return a terminal ScrapeResult.

        Raises on a transient or deterministic fault so :func:`politeness.retry_async` can
        decide whether to retry; returns directly on every non-exception terminal outcome
        (success, duplicate, 404, parse error, or a robots.txt skip), none of which is retried.

        :returns: The :class:`ScrapeResult` for this single attempt.
        """
        nonlocal attempts_made
        attempts_made += 1
        attempt = attempts_made
        module_logger.debug(f"Scrape attempt {attempt} of {MAX_ATTEMPTS} for {url}.")

        # Each attempt owns exactly one page; the context manager closes it on every exit path
        # (success, duplicate, 404, parse error, timeout, or any other exception).
        async with _attempt_page(browser) as page:
            await page.route("**/*", route_handler)

            selectors = site_constants[site]
            # A missing BASE_URL is no longer fatal: ``urljoin`` leaves an already-absolute
            # URL untouched, so a site whose constants omit BASE_URL still scrapes an
            # absolute detail URL (previously this raised KeyError and wasted all retries).
            base_url = selectors.get("BASE_URL") or ""
            full_url = urljoin(base_url, url)
            module_logger.info(f"Navigating to {full_url} for detailed scraping.")
            print_log(f"Navigating to {full_url} for detailed scraping.", "info")

            # robots.txt gate (fetched once per host per run), BEFORE any request to the
            # target path: a disallowed URL is never fetched and yields SKIPPED_ROBOTS with
            # no retry (a policy decision, not a transient fault). The user-agent matched against
            # robots.txt is the one the context actually presents (read from the live page), so a
            # native-mode run matches on its real Chromium identity and a rotate-mode run on the
            # string it set - never a stale hard-coded value.
            if robots is not None and not await robots.allowed(
                full_url, await page.evaluate("() => navigator.userAgent")
            ):
                module_logger.info(f"Skipping {full_url}: disallowed by robots.txt.")
                print_log(f"Skipping {full_url}: disallowed by robots.txt.", "warning")
                return ScrapeResult(
                    ScrapeStatus.SKIPPED_ROBOTS,
                    url=full_url,
                    site=site,
                    error="disallowed by robots.txt",
                    attempts=attempt,
                )

            # Throttle per host immediately before navigating.
            await active_limiters.for_url(full_url).acquire()
            await page.goto(full_url, timeout=active_nav_timeout_ms)

            # Bot-wall detection BEFORE the 404 check: a 200 CAPTCHA interstitial has none of the
            # real fields, so without this it would be misread as a PARSE_ERROR. A positive match
            # is a policy decision (identify and back off), never a transient fault - return
            # BLOCKED with no retry; the per-domain circuit breaker (in scrape_book) counts it.
            if await _detect_bot_wall(page, site):
                module_logger.warning(f"Bot wall detected for {full_url}; returning BLOCKED (no retry).")
                print_log(f"Bot wall detected for {full_url}; skipping (no retry).", "warning")
                return ScrapeResult(
                    ScrapeStatus.BLOCKED,
                    url=full_url,
                    site=site,
                    error="bot wall detected",
                    attempts=attempt,
                )

            # Check for 404 page (if site_constants provides a 404 selector)
            not_found_title = selectors.get("404_PAGE_TITLE")
            if not_found_title:
                page_title = await page.title()
                if not_found_title.lower() in page_title.lower():
                    module_logger.warning(f"404 page detected for {full_url}. Skipping.")
                    print_log(f"404 page detected for {full_url}. Skipping.", "warning")
                    return ScrapeResult(
                        ScrapeStatus.NOT_FOUND,
                        url=full_url,
                        site=site,
                        error="404 page detected",
                        attempts=attempt,
                    )

            # Every output key is initialized up front with a neutral value, so the
            # normalizer always sees a well-formed raw dict to canonicalize.
            book_details: dict = {
                "url": full_url,
                "site": site,
                "title": None,
                "authors": [],
                "publication_date": None,
                "isbn10": "N/A",
                "isbn13": "N/A",
                "description": None,
                "tags": [],
            }

            # Title (required): a missing or empty title after the page loads is a parse
            # error, not a transient fault - return immediately with no retry.
            title_selector = _selector(selectors, "BOOK_TITLE")
            title = await _text(page, title_selector, timeout_ms=FIELD_TIMEOUT_MS) if title_selector else None
            if not title:
                module_logger.warning(f"Required title not found for {full_url}.")
                print_log(f"Required title not found for {full_url}.", "warning")
                return ScrapeResult(
                    ScrapeStatus.PARSE_ERROR,
                    url=full_url,
                    site=site,
                    error="title not found",
                    attempts=attempt,
                )
            book_details["title"] = title

            # Authors (metadata tags, else linked text, else the alternative selector).
            meta_author_selector = _selector(selectors, "AUTHORS_META")
            author_selector = _selector(selectors, "AUTHORS")
            if meta_author_selector:
                authors_list: list[str] = []
                for element in await page.locator(meta_author_selector).all():
                    name = await element.get_attribute("content", timeout=OPTIONAL_FIELD_TIMEOUT_MS)
                    if name and name.strip():
                        authors_list.append(name.strip())
                book_details["authors"] = authors_list
            elif author_selector:
                authors_list = await _collect_texts(page, author_selector, timeout_ms=OPTIONAL_FIELD_TIMEOUT_MS)
                if not authors_list:
                    alt_author_selector = _selector(selectors, "AUTHORS_ALT")
                    if alt_author_selector:
                        authors_list = await _collect_texts(
                            page, alt_author_selector, timeout_ms=OPTIONAL_FIELD_TIMEOUT_MS
                        )
                book_details["authors"] = authors_list

            # Details expander (amazon): a visible "Product details" button reveals the
            # ISBN/publication-date blocks, so click it (best-effort) BEFORE those reads.
            # A missing/invisible button or a failed click is non-fatal - logged and skipped.
            details_button_selector = _selector(selectors, "DETAILS_BUTTON")
            if details_button_selector:
                try:
                    details_button = page.locator(details_button_selector).first
                    if await details_button.is_visible():
                        await details_button.click(timeout=OPTIONAL_FIELD_TIMEOUT_MS)
                        await page.wait_for_timeout(500)
                except PlaywrightError as exc:
                    module_logger.debug(f"No usable details expander for {full_url}: {exc}")

            # Publication date (metadata ``content`` attribute, else page text).
            meta_date_selector = _selector(selectors, "PUBLICATION_DATE_META")
            if meta_date_selector:
                pub_date_text = await _attr(page, meta_date_selector, "content", timeout_ms=FIELD_TIMEOUT_MS)
                if pub_date_text:
                    book_details["publication_date"] = pub_date_text.split("T")[0]
            else:
                date_selector = _selector(selectors, "PUBLICATION_DATE")
                if date_selector:
                    pub_date_text = await _text(page, date_selector, timeout_ms=FIELD_TIMEOUT_MS)
                    if pub_date_text:
                        book_details["publication_date"] = pub_date_text

            # ISBN10 & ISBN13 (optional): the raw "N/A" sentinel is left in place here and
            # normalized to None (or a canonical ISBN) by normalize.from_playwright.
            isbn10_selector = _selector(selectors, "ISBN10")
            if isbn10_selector:
                isbn10 = await _text(page, isbn10_selector, timeout_ms=OPTIONAL_FIELD_TIMEOUT_MS)
                if isbn10:
                    book_details["isbn10"] = isbn10
            isbn13_selector = _selector(selectors, "ISBN13")
            if isbn13_selector:
                isbn13 = await _text(page, isbn13_selector, timeout_ms=OPTIONAL_FIELD_TIMEOUT_MS)
                if isbn13:
                    book_details["isbn13"] = isbn13

            # Amazon ASIN extraction (the URL is always a string and the title is set above,
            # so neither TypeError nor KeyError can arise here any more).
            if site == "amazon":
                asin_match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", url)
                if asin_match:
                    book_details["asin"] = asin_match.group(1)
                    module_logger.info(f"Extracted ASIN: {book_details['asin']} for {book_details['title']} ({url}).")

            # Description (optionally behind a "Read more" expander, else a fallback selector).
            description_selector = _selector(selectors, "DESCRIPTION")
            description: Optional[str] = None
            if description_selector:
                read_more_selector = _selector(selectors, "READ_MORE_LINK")
                if read_more_selector:
                    try:
                        read_more_button = page.locator(read_more_selector).first
                        if await read_more_button.is_visible():
                            await read_more_button.click(timeout=OPTIONAL_FIELD_TIMEOUT_MS)
                            await page.wait_for_timeout(500)
                    except PlaywrightError as exc:
                        module_logger.debug(f"No usable 'Read more' expander for {full_url}: {exc}")
                description = await _text(page, description_selector, timeout_ms=FIELD_TIMEOUT_MS)
            # Fall back to DESCRIPTION_ALT when the primary selector matched nothing.
            if not description:
                description_alt_selector = _selector(selectors, "DESCRIPTION_ALT")
                if description_alt_selector:
                    description = await _text(page, description_alt_selector, timeout_ms=FIELD_TIMEOUT_MS)
            book_details["description"] = description

            # Tags / topics: every match, stripped, de-duplicated in order, capped at
            # TAGS_LIMIT. A missing selector or no matches leaves the neutral empty list.
            tags_selector = _selector(selectors, "TAGS")
            if tags_selector:
                book_details["tags"] = await _texts(page, tags_selector, timeout_ms=OPTIONAL_FIELD_TIMEOUT_MS)

            # Build the canonical record from the raw field dict: author cleaning, ISBN
            # normalization/validation, date normalization, and the (unchanged) hash input
            # all live in the normalizer (see scraping/normalize.py).
            record = normalize.from_playwright(book_details, site, full_url)

            # Check for duplicates BEFORE returning, if a storage backend is provided
            if storage_backend is not None:
                is_duplicate = storage_backend.book_exists_by_hash(record.hash)
                if is_duplicate:
                    module_logger.info(
                        f"Book with hash '{record.hash}' (ISBN10: {record.isbn10}, ISBN13: {record.isbn13}) already exists in DB. Skipping URL: {url}"
                    )
                    print_log(f"Skipping duplicate book for URL: {url}", "warning")
                    return ScrapeResult(ScrapeStatus.DUPLICATE, url=full_url, site=site, attempts=attempt)

            # If all successful and not a duplicate:
            module_logger.info(f"Successfully scraped details for {record.title} ({url}).")
            return ScrapeResult(
                ScrapeStatus.SUCCESS, url=full_url, site=site, book=record.to_document(), attempts=attempt
            )

    try:
        # Retry only transient faults (navigation/network errors, timeouts, 429/502/503/504)
        # with shared jittered backoff, honoring any Retry-After on a 429/503. A deterministic
        # bug or a non-retryable status re-raises on the first attempt and wastes no retries.
        result = await retry_async(
            _attempt,
            attempts=MAX_ATTEMPTS,
            base_delay=RETRY_BASE_DELAY_S,
            max_delay=RETRY_MAX_DELAY_S,
            is_retryable=is_transient,
            retry_after=httpx_retry_after,
            sleep=asyncio.sleep,
        )
    except Exception as exc:  # noqa: X002 - terminal fail boundary: retry_async has exhausted retries on a transient fault or re-raised a non-transient one so one URL can never raise out of scrape_book and abort the asyncio.gather batch
        if is_transient(exc):
            # Exhausted the retry budget on a transient failure.
            status = ScrapeStatus.TIMEOUT if isinstance(exc, TimeoutError) else ScrapeStatus.FAILED
            error_text = (
                f"timed out after {attempts_made} attempts"
                if status is ScrapeStatus.TIMEOUT
                else f"transient failure after {attempts_made} attempts"
            )
            module_logger.error(f"Failed to scrape {url} after {attempts_made} attempts: {exc}")
            print_log(f"Failed to scrape {url} after {attempts_made} attempts.", "error")
            result = ScrapeResult(status, url=url, site=site, error=error_text, attempts=attempts_made)
        else:
            # Deterministic bug (KeyError / AttributeError / ValueError / TypeError) or any
            # unexpected error: never retried - it would fail identically on every attempt.
            module_logger.error(f"Non-transient error scraping {url} on attempt {attempts_made}: {exc}", exc_info=True)
            print_log(f"Error scraping {url}.", "error")
            result = ScrapeResult(
                ScrapeStatus.FAILED,
                url=url,
                site=site,
                error=f"{type(exc).__name__}: {exc}",
                attempts=attempts_made,
            )
    # Feed the outcome to the domain's circuit breaker (a detected bot wall counts as a block, a
    # success resets the counter) and warn once if this result just opened the breaker.
    _note_circuit_result(breaker, result, url)
    return result
