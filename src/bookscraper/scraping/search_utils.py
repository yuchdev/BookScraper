from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import (
    parse_qs,
    quote_plus,
    unquote,
    urljoin,
    urlparse,
)  # Added for URL parsing

import httpx
from playwright.async_api import Browser, Error as PlaywrightError, Page

from ..book_utils import print_log
from .browser import new_scrape_context
from .parameters import site_constants
from .politeness import (
    DomainCircuitBreakers,
    DomainRateLimiters,
    RobotsCache,
    default_breakers,
    default_limiters,
    retry_async,
)
from .scrape_details import (
    MAX_ATTEMPTS,
    RETRY_BASE_DELAY_S,
    RETRY_MAX_DELAY_S,
    ROBOTS_HTTPX_USER_AGENT,
    _detect_bot_wall,
    httpx_retry_after,
    is_transient,
    route_handler,
)

logger = logging.getLogger(__name__)


async def get_leanpub_search_results_via_api(
    query: str,
    limiters: Optional[DomainRateLimiters] = None,
    robots: Optional[RobotsCache] = None,
    max_pages: Optional[int] = None,
) -> list[dict]:
    """
    Fetch Leanpub search-result candidates for ``query`` via Leanpub's paginated JSON API.

    :param query: The search query to run against the Leanpub bookstore API.
    :param limiters: The per-run :class:`DomainRateLimiters` to throttle each page request
        against, or ``None`` to use the shared module-level instance.
    :param robots: The per-run :class:`RobotsCache` to consult before fetching, or ``None`` to
        skip the robots.txt check (e.g. under ``--ignore-robots``).
    :param max_pages: The maximum number of result pages to fetch (the resolved
        ``--max-search-pages``), or ``None`` for no cap. Pagination still stops early when a page
        returns fewer than a full page of results.
    :returns: A list of candidate book dictionaries collected across all result pages.
    """
    print_log(f"Leanpub - Fetching search results from Leanpub.com for {query}.", "info")
    logger.info(f"Leanpub - Fetching search results from Leanpub.com for {query}.")
    active_limiters = limiters if limiters is not None else default_limiters()
    base_url = site_constants["leanpub"]["SEARCH_BASE_API"]

    # robots.txt gate (fetched once per host per run): the Leanpub JSON:API search path goes
    # through the same check as every other URL. If robots.txt disallows it, fetch nothing.
    if robots is not None and not await robots.allowed(base_url, ROBOTS_HTTPX_USER_AGENT):
        print_log(f"Leanpub - Skipping search for '{query}': {base_url} disallowed by robots.txt.", "warning")
        logger.info(f"Leanpub - Skipping search for '{query}': {base_url} disallowed by robots.txt.")
        return []

    all_extracted_books = []
    current_page = 1
    author_lookup = {}

    # Use an AsyncClient for efficient session management
    async with httpx.AsyncClient() as client:

        async def _fetch_page(page_params: dict) -> httpx.Response:
            """Throttle and GET one Leanpub search page, raising on a non-2xx status.

            One :func:`politeness.retry_async` attempt: a transient failure (429/502/503/504 or a
            transport error) raises so the retry wrapper can back off and re-attempt it, rather
            than ending pagination on the first rate-limit response.

            :param page_params: The query parameters for this page request.
            :returns: The successful :class:`httpx.Response` for the page.
            """
            await active_limiters.for_url(base_url).acquire()
            response = await client.get(base_url, params=page_params, timeout=30.0)
            response.raise_for_status()
            return response

        while True:
            # Honor the resolved --max-search-pages cap: stop before fetching a page beyond it.
            if max_pages is not None and current_page > max_pages:
                break
            params = {
                "bookstore": "true",
                "filter_erotica": "true",
                "include": "accepted_authors",
                "language": "eng",
                "page": str(current_page),
                "page_size": "100",
                "search": query,
                "searchable": "true",
                "sellable": "true",
                "sort": "bestsellers_last_week",
                "type": "book",
            }

            try:
                print_log(f"Leanpub - Fetching page {current_page}...", "info")
                logger.info(f"Leanpub - Fetching page {current_page}...")
                # Throttle (inside _fetch_page) and fetch with shared jittered backoff, honoring
                # any Retry-After on a 429/503 so a single rate-limit response no longer fails the
                # whole search. A persistent failure re-raises and ends pagination via the
                # handlers below.
                response = await retry_async(
                    lambda page_params=params: _fetch_page(page_params),
                    attempts=MAX_ATTEMPTS,
                    base_delay=RETRY_BASE_DELAY_S,
                    max_delay=RETRY_MAX_DELAY_S,
                    is_retryable=is_transient,
                    retry_after=httpx_retry_after,
                    sleep=asyncio.sleep,
                )
                data = response.json()

                # Check if the 'data' array is empty, which indicates no more results
                if not data.get("data"):
                    break

                # Update author_lookup with authors from the current page's 'included' section
                for item in data.get("included", []):
                    if item.get("type") == "SimpleAuthor":
                        author_id = item.get("id")
                        author_name = item.get("attributes", {}).get("name")
                        if author_id and author_name:
                            author_lookup[author_id] = author_name

                # Process books from the current page's 'data' section
                books_on_current_page = []
                for book_item in data.get("data", []):
                    book_id = book_item.get("id")

                    attributes = book_item.get("attributes", {})
                    title = attributes.get("title")
                    slug = attributes.get("slug")

                    authors_list_for_book = []
                    relationships = book_item.get("relationships", {})
                    accepted_authors_data = relationships.get("accepted_authors", {}).get("data", [])

                    # For each author associated with this book (by ID)
                    for author_rel in accepted_authors_data:
                        author_id = author_rel.get("id")
                        if author_id and author_id in author_lookup:
                            authors_list_for_book.append(author_lookup[author_id])

                    books_on_current_page.append(
                        {
                            "site": "leanpub",
                            "title": title,
                            "book_id": book_id,
                            "slug": slug,
                            "authors": authors_list_for_book,
                        }
                    )

                # Extend the main list of all extracted books
                all_extracted_books.extend(books_on_current_page)
                print_log(
                    f"Leanpub - Fetched and processed {len(books_on_current_page)} books on page {current_page}.",
                    "info",
                )
                logger.info(
                    f"Leanpub - Fetched and processed {len(books_on_current_page)} books on page {current_page}."
                )

                #
                if len(books_on_current_page) < 100:
                    break

                # Increment page number for the next iteration. Inter-page politeness is now
                # handled by the per-host rate limiter acquired above, so no ad hoc sleep here.
                current_page += 1

            except httpx.RequestError as exc:
                print_log(
                    f"An HTTP error occurred while requesting {exc.request.url!r}: {exc}",
                    "error",
                )
                logger.error(f"An HTTP error occurred while requesting {exc.request.url!r}: {exc}")
                break  # Break the loop on network/request errors
            except json.JSONDecodeError:
                print_log(
                    f"Failed to decode JSON response for page {current_page}. Response content: {response.text[:200]}...",
                    "error",
                )
                logger.error(
                    f"Failed to decode JSON response for page {current_page}. Response content: {response.text[:200]}..."
                )
                break  # Break the loop on JSON parsing errors
            except Exception as e:  # noqa: X002 - per-page isolation boundary: an HTTP-status (raise_for_status) or malformed-structure failure ends pagination and returns the books accumulated so far
                print_log(
                    f"An unexpected error occurred while processing page {current_page}: {e}",
                    "error",
                )
                logger.error(f"An unexpected error occurred while processing page {current_page}: {e}")
                break  # Catch any other unexpected errors

    print_log(
        f"Leanpub - Finished searching for '{query}'. Total books found: {len(all_extracted_books)}",
        "info",
    )
    logger.info(f"Leanpub - Finished searching for '{query}'. Total books found: {len(all_extracted_books)}")
    return all_extracted_books


@asynccontextmanager
async def _search_page(browser: Browser) -> AsyncIterator[Page]:
    """Yield a fresh page on its own context for one search-result page, closing both on exit.

    Each search-page iteration gets its own :class:`BrowserContext` (via
    :func:`browser.new_scrape_context`, so the UA/identity matches what the detail scraper and
    schema-detection see) and one page on it. The page and then the context are closed on **every**
    exit path of the ``async with`` block - normal completion, ``break``, ``continue``, a navigation
    error, or any other exception - so an iteration can never leak either. A failure to close (for
    example when the browser has already crashed) is logged at debug level and swallowed so it never
    masks the original exception that is unwinding the block.

    :param browser: The Playwright browser to open the context and page on.
    :returns: An async context manager yielding this search page's :class:`Page`.
    """
    context = await new_scrape_context(browser)
    page = await context.new_page()
    try:
        yield page
    finally:
        try:
            await page.close()
        except Exception as exc:  # noqa: X002 - cleanup isolation boundary: a page-close failure (any type; e.g. "browser already closed") must never mask the original error unwinding the with-block
            logger.debug(f"Ignoring page-close failure: {exc}")
        try:
            await context.close()
        except Exception as exc:  # noqa: X002 - cleanup isolation boundary: a context-close failure (any type; e.g. "browser already closed") must never mask the original error unwinding the with-block
            logger.debug(f"Ignoring context-close failure: {exc}")


# Amazon ASIN: the 10-char token embedded in a ``/dp/<asin>`` or ``/gp/product/<asin>`` path.
_AMAZON_ASIN_RE = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})")
# A leading rating number such as the ``4.6`` in "4.6 out of 5 stars" (``.`` or ``,`` decimal).
_RATING_RE = re.compile(r"(\d+(?:[.,]\d+)?)")
# A review-count run of digits/grouping separators such as ``1,234`` in "1,234 ratings".
_RATING_COUNT_RE = re.compile(r"[\d,]+")


def unwrap_amazon_detail_url(raw_href: str, base_url: str) -> Optional[str]:
    """Resolve an Amazon search-card ``href`` to an absolute detail URL, unwrapping sponsored links.

    A sponsored result's link is an ``/sspa/click?...&url=<url-encoded path>`` redirect whose real
    product path lives in the ``url`` query parameter; this decodes that parameter and joins it onto
    ``base_url``. A plain (non-sponsored) ``href`` is simply joined onto ``base_url``. A sponsored
    link carrying no ``url`` parameter has no recoverable destination and yields ``None`` so the
    caller can skip that card.

    :param raw_href: The raw ``href`` read from the card's detail-link element.
    :param base_url: The site base URL to resolve a relative path against (``https://www.amazon.com``).
    :returns: The absolute detail URL, or ``None`` for a sponsored link with no ``url`` parameter.
    """
    if "/sspa/click" in raw_href:
        query_params = parse_qs(urlparse(raw_href).query)
        embedded = query_params.get("url")
        if not embedded:
            return None
        return urljoin(base_url, unquote(embedded[0]))
    return urljoin(base_url, raw_href)


def extract_amazon_asin(url: Optional[str]) -> Optional[str]:
    """Return the 10-character ASIN embedded in an Amazon ``/dp/`` or ``/gp/product/`` ``url``.

    :param url: The absolute detail URL to search, or ``None``.
    :returns: The ASIN, or ``None`` when ``url`` is falsy or carries no ASIN token.
    """
    if not url:
        return None
    match = _AMAZON_ASIN_RE.search(url)
    return match.group(1) if match else None


def parse_rating(text: Optional[str]) -> Optional[float]:
    """Parse the average rating out of a search-card rating string, or ``None``.

    Reads the first number in a string such as ``"4.6 out of 5 stars"`` (tolerating a comma decimal
    separator). A missing, blank, or number-less string yields ``None`` so a card with no rating
    flows downstream as "unrated" rather than a fabricated zero.

    :param text: The raw rating element text, or ``None``.
    :returns: The parsed rating as a float, or ``None`` when none is present/parseable.
    """
    if not text:
        return None
    match = _RATING_RE.search(text)
    if not match:
        return None
    # The regex only matches digits with an optional single decimal group, so float() is safe.
    return float(match.group(1).replace(",", "."))


def parse_rating_count(text: Optional[str]) -> Optional[int]:
    """Parse the review count out of a search-card count string, or ``None``.

    Reads the first digit run (tolerating ``,`` grouping separators) in a string such as ``"1,234"``
    or ``"(1,234)"``. A missing, blank, or digit-less string yields ``None``.

    :param text: The raw rating-count element text, or ``None``.
    :returns: The parsed count as an int, or ``None`` when none is present/parseable.
    """
    if not text:
        return None
    match = _RATING_COUNT_RE.search(text)
    if not match:
        return None
    digits = match.group(0).replace(",", "")
    if not digits.isdigit():
        return None
    # Guarded by isdigit() above, so int() cannot raise.
    return int(digits)


def resolve_amazon_max_pages(requested: Optional[int], site_limit: int, query: str) -> int:
    """Clamp a requested search-page count to Amazon's documented site limit, logging any clamp.

    When ``requested`` is ``None`` the site limit is used as-is; otherwise the smaller of the two
    wins, and a request that exceeds the site limit emits a single clamp log line (console + run
    log) so an operator sees that fewer pages than asked for will be fetched.

    :param requested: The resolved ``--max-search-pages`` value, or ``None`` for "use the site limit".
    :param site_limit: ``site_constants["amazon"]["SEARCH_MAXIMUM_PAGES"]`` (the documented cap).
    :param query: The search query, named in the clamp log line.
    :returns: The effective maximum number of result pages to fetch (at least 1).
    """
    if requested is None:
        return max(1, site_limit)
    effective = min(requested, site_limit)
    if requested > site_limit:
        logger.info(
            f"Amazon - requested {requested} search pages for '{query}' exceeds the site limit "
            f"{site_limit}; clamping to {site_limit}."
        )
        print_log(
            f"Amazon - clamping requested {requested} search pages to the site limit of {site_limit}.",
            "info",
        )
    return max(1, effective)


async def _amazon_has_next_page(page: Page, selector: Optional[str]) -> bool:
    """Return whether the Amazon search ``page`` exposes an enabled "next page" control.

    Pagination stops when the next-page control is absent or disabled (Amazon marks the terminal
    page's control with the ``s-pagination-disabled`` class and ``aria-disabled="true"``). A missing
    selector, a no-match, a disabled control, or a read error all read as "no next page", so a listing
    run never over-runs past its last real page. Detection is best-effort and never raises.

    :param page: The freshly-scraped Playwright search page to inspect.
    :param selector: The configured ``SEARCH_NEXT_PAGE_SELECTOR``, or ``None`` when unset.
    :returns: ``True`` iff an enabled next-page control is present.
    """
    if not selector:
        return False
    locator = page.locator(selector)
    try:
        if await locator.count() == 0:
            return False
        classes = await locator.first.get_attribute("class") or ""
        aria_disabled = await locator.first.get_attribute("aria-disabled")
    except PlaywrightError as exc:
        logger.debug(f"Amazon next-page detection failed on {page.url}: {exc}")
        return False
    if "s-pagination-disabled" in classes:
        return False
    return aria_disabled != "true"


async def get_search_results_via_playwright(
    browser: Browser,
    site_name: str,
    query: str,
    limiters: Optional[DomainRateLimiters] = None,
    robots: Optional[RobotsCache] = None,
    breakers: Optional[DomainCircuitBreakers] = None,
    max_pages: Optional[int] = None,
    # filters: dict = None
) -> list[dict]:
    """
    Perform a search on a given site for a specific query and scrape preliminary book data.

    This function navigates to the search results page, extracts initial book information
    (title, authors, publication date, URL, and ASIN for Amazon), and handles pagination
    to scrape multiple search result pages up to a specified maximum. Each search page gets its
    own isolated :class:`BrowserContext`/page, closed on every exit path of the iteration (normal
    completion, break, a navigation error, or any exception) before moving to the next page URL.

    It also includes logic to:
    - Set a random user agent for the Playwright page.
    - Route network requests to potentially block unnecessary resources (via ``route_handler``).
    - Handle navigation timeouts.
    - Gracefully manage scenarios where no book cards are found on a page.
    - Determine the next page URL based on the current page's URL and the next page button's href.
    - Detect and stop pagination when no active "next page" button is found (either disabled or absent).
    - Handle Amazon sponsored book links to extract the true product URL.

    :param browser: The Playwright browser instance to use for navigation and scraping.
    :param site_name: The name of the site to search (e.g. ``"amazon"``, ``"leanpub"``,
        ``"packtpub"``, ``"oreilly"``); used to look up site-specific selectors and configurations.
    :param query: The search query string (e.g. ``"Playwright Python"``).
    :param limiters: The per-run :class:`DomainRateLimiters` to throttle each page request against,
        or ``None`` to use the shared module-level instance.
    :param robots: The per-run :class:`RobotsCache` to consult before fetching, or ``None`` to skip
        the robots.txt check (e.g. under ``--ignore-robots``).
    :param breakers: The per-run :class:`DomainCircuitBreakers` tracking repeated bot-wall blocks,
        or ``None`` to use the shared module-level instance. When the site's breaker is open,
        pagination stops without a request (matching ``scrape_details.scrape_book``).
    :param max_pages: The maximum number of result pages to fetch (the resolved
        ``--max-search-pages``), or ``None`` to use the site's ``SEARCH_MAXIMUM_PAGES``. The value is
        clamped to that site limit (logged when it clamps).
    :returns: A list of dictionaries, each a preliminary book record found on the search results
        pages, typically carrying ``site``, ``query``, ``search_page_number``,
        ``search_result_index``, ``title``, ``authors_from_search``, ``publication_date``, ``url``
        and (for Amazon) ``asin``, ``rating`` and ``rating_count``.
    :raises Exception: Catches and logs any unhandled exceptions that occur during the search
        process, printing a critical error message to the console.
    """

    books_from_search_results = []
    current_page_num = 0
    active_limiters = limiters if limiters is not None else default_limiters()
    active_breakers = breakers if breakers is not None else default_breakers()

    try:
        site_config = site_constants.get(site_name)
        if not site_config:
            logger.error(f"Configuration for site '{site_name}' not found in site_constants.")
            print_log(f"Error: Site '{site_name}' configuration missing.", "error")
            return []

        # Use site_config as selectors dictionary
        selectors = site_config

        if "SEARCH_BASE_URL" not in selectors:
            logger.error(f"SEARCH_BASE_URL not defined for site '{site_name}'. Skipping search.")
            print_log(f"Error: Search base URL missing for {site_name}.", "error")
            return []

        match site_name.lower():
            case "amazon":
                base_url = site_constants["amazon"]["BASE_URL"]
                parsed_query = quote_plus(query)
                # Clamp the requested page count to Amazon's documented site limit (logged on clamp).
                effective_max = resolve_amazon_max_pages(max_pages, selectors.get("SEARCH_MAXIMUM_PAGES") or 1, query)
                # One per-domain breaker for the Amazon host: an open breaker (from earlier repeated
                # bot-wall blocks this run) skips the search entirely, mirroring scrape_book.
                breaker = active_breakers.for_url(selectors["SEARCH_BASE_URL"])

                # Paginate 1..effective_max INCLUSIVE (fixes the old range(1, MAX) off-by-one). The
                # loop also stops early on a bot wall, a no-results marker, an empty page, or a
                # missing/disabled next-page control (handled per page below).
                for page_num in range(1, effective_max + 1):
                    current_page_num = page_num
                    search_result_page = (
                        selectors["SEARCH_BASE_URL"].replace("[k]", parsed_query).replace("[p]", str(page_num))
                    )

                    if breaker.is_open():
                        logger.info(f"Amazon - circuit open; stopping search for '{query}' without a request.")
                        print_log(f"Amazon - bot-wall circuit open; stopping search for '{query}'.", "warning")
                        break

                    # Each search page gets its own isolated context (own cookie jar, UA/identity
                    # matching what the detail scraper and schema-detection see) via the shared
                    # factory, closed on every exit path of this iteration by _search_page.
                    async with _search_page(browser) as page:
                        await page.route("**/*", route_handler)

                        # robots.txt gate (fetched once per host per run), BEFORE navigating: a
                        # disallowed search page is skipped and never fetched. The user-agent matched
                        # against robots.txt is the one the context actually presents (read from the
                        # live page), never a stale hard-coded value. All search pages share one host,
                        # so a disallowed first page ends pagination.
                        if robots is not None and not await robots.allowed(
                            search_result_page, await page.evaluate("() => navigator.userAgent")
                        ):
                            logger.info(f"Skipping search page {search_result_page}: disallowed by robots.txt.")
                            print_log(
                                f"Skipping search page {search_result_page}: disallowed by robots.txt.", "warning"
                            )
                            break

                        try:
                            # Throttle per host immediately before navigating to each search page.
                            await active_limiters.for_url(search_result_page).acquire()
                            await page.goto(
                                search_result_page,
                                wait_until="domcontentloaded",
                                timeout=60000,
                            )
                            await page.wait_for_load_state("networkidle", timeout=60000)
                            actual_url_after_goto = page.url
                            logger.info(
                                f"Actual URL after navigation for page {current_page_num}: {actual_url_after_goto}"
                            )
                        except PlaywrightError as e:
                            logger.error(f"Failed to navigate to page {current_page_num}: {e}")
                            continue

                        # Bot-wall detection right after navigation (identify and back off, never
                        # bypass): a CAPTCHA/interstitial trips the per-domain circuit breaker and
                        # stops pagination, exactly like scrape_book treats a BLOCKED detail page.
                        if await _detect_bot_wall(page, "amazon"):
                            opened = breaker.record_blocked()
                            logger.warning(f"Amazon - bot wall detected on search page {current_page_num}; stopping.")
                            if opened:
                                print_log(
                                    "Amazon - bot-wall circuit opened after repeated blocks; skipping Amazon "
                                    "for the rest of the run.",
                                    "warning",
                                )
                            else:
                                print_log(
                                    f"Amazon - bot wall detected on search page {current_page_num}; stopping.",
                                    "warning",
                                )
                            break

                        # No-results markers: a zero-results search page carries one of these and no
                        # cards, so stop before waiting on the card selector.
                        no_results = False
                        for marker_key in ("SEARCH_NO_RESULTS_TEXT", "SEARCH_NO_BOOKS_FOUND"):
                            marker_selector = selectors.get(marker_key)
                            if marker_selector and await page.locator(marker_selector).count() > 0:
                                no_results = True
                                break
                        if no_results:
                            print_log(f"No results on page {current_page_num} for {site_name}.", "warning")
                            logger.warning(f"No results on page {current_page_num} for {site_name}.")
                            break

                        # Wait for the card selector; a page with no cards (an exhausted last page)
                        # times out here, which ends pagination with the pages gathered so far.
                        try:
                            await page.wait_for_selector(selectors["SEARCH_BOOK_CARD"])
                        except PlaywrightError as e:
                            logger.info(f"No book cards appeared on page {current_page_num} for {site_name}: {e}")
                            print_log(f"No book cards found on page {current_page_num} for {site_name}.", "warning")
                            break
                        book_cards = await page.locator(selectors["SEARCH_BOOK_CARD"]).all()
                        if not book_cards:
                            print_log(
                                f"No book cards found on page {current_page_num} for {site_name}.",
                                "warning",
                            )
                            logger.warning(f"No book cards found on page {current_page_num} for {site_name}.")
                            break  # No books to scrape, exit the loop

                        # A page that produced cards is a healthy response; reset the breaker's
                        # consecutive-block counter like a successful scrape does.
                        breaker.record_success()

                        print_log(
                            f"Found {len(book_cards)} books on page {search_result_page}.",
                            "info",
                        )

                        # Scrape Book card data
                        for i, card in enumerate(book_cards):
                            book_data = {
                                "site": site_name,
                                "query": query,
                                "search_page_number": current_page_num,
                                "search_result_index": i + 1,
                            }
                            try:
                                link_element = card.locator(selectors["SEARCH_BOOK_DETAIL_LINK"])
                                raw_href = (
                                    await link_element.get_attribute("href") if await link_element.count() > 0 else None
                                )

                                # Unwrap the detail URL (sponsored /sspa/click links decode to the
                                # real product path). A sponsored link with no 'url' param has no
                                # recoverable destination, so skip that card.
                                is_sponsored = bool(raw_href) and "/sspa/click" in raw_href
                                book_url = unwrap_amazon_detail_url(raw_href, base_url) if raw_href else None
                                if is_sponsored and book_url is None:
                                    logger.warning(f"Sponsored link without 'url' parameter: {raw_href}")
                                    continue

                                # ASIN (source_id) comes from the card's data-asin attribute, read
                                # independently of the detail link so it survives link-selector drift.
                                # A sponsored card that carries none falls back to the unwrapped URL.
                                asin = None
                                asin_attribute = selectors.get("SEARCH_ASIN_ATTRIBUTE")
                                if asin_attribute:
                                    asin = await card.get_attribute(asin_attribute)
                                if not asin and is_sponsored:
                                    asin = extract_amazon_asin(book_url)

                                book_data["url"] = book_url if book_url else None
                                book_data["asin"] = asin if asin else None

                                # Extract Title
                                title_element = card.locator(selectors["SEARCH_TITLE"])
                                book_data["title"] = (
                                    await title_element.text_content() if await title_element.count() > 0 else None
                                )

                                # Extract Authors
                                authors_elements = card.locator(selectors["SEARCH_AUTHORS"])
                                authors = []
                                if await authors_elements.count() > 0:
                                    authors = await authors_elements.all_text_contents()
                                book_data["authors_from_search"] = ", ".join(authors) if authors else None

                                # Extract Publication Date
                                publication_date_selector = selectors.get("SEARCH_PUBLICATION_DATE")
                                if publication_date_selector:
                                    try:
                                        date_element = card.locator(publication_date_selector)
                                        book_data["publication_date"] = (
                                            await date_element.text_content()
                                            if await date_element.count() > 0
                                            else None
                                        )
                                    except PlaywrightError as e:
                                        logger.warning(
                                            f"Could not extract publication date for a book on {site_name} using selector '{publication_date_selector}'. Error: {e}"
                                        )
                                        book_data["publication_date"] = None
                                else:
                                    # logger.info(f"No SEARCH_PUBLICATION_DATE selector defined for {site_name}.")
                                    book_data["publication_date"] = None

                                # Rating / rating count (optional - absent for a book with no reviews).
                                book_data["rating"] = None
                                rating_selector = selectors.get("SEARCH_RATING")
                                if rating_selector:
                                    try:
                                        rating_locator = card.locator(rating_selector)
                                        if await rating_locator.count() > 0:
                                            book_data["rating"] = parse_rating(
                                                await rating_locator.first.text_content()
                                            )
                                    except PlaywrightError as e:
                                        logger.debug(f"Could not read rating on {site_name} card: {e}")
                                book_data["rating_count"] = None
                                rating_count_selector = selectors.get("SEARCH_RATING_COUNT")
                                if rating_count_selector:
                                    try:
                                        count_locator = card.locator(rating_count_selector)
                                        if await count_locator.count() > 0:
                                            book_data["rating_count"] = parse_rating_count(
                                                await count_locator.first.text_content()
                                            )
                                    except PlaywrightError as e:
                                        logger.debug(f"Could not read rating count on {site_name} card: {e}")

                                if not book_data["asin"]:
                                    logger.debug(
                                        f"Amazon card kept without an ASIN (page {current_page_num}, "
                                        f"card {i + 1}): {book_data.get('title')}"
                                    )

                                books_from_search_results.append(book_data)
                                logger.debug(
                                    f"Extracted: {book_data.get('title')} by {book_data.get('authors_from_search')} ({site_name}) (URL: {book_data.get('url')}, ASIN: {book_data.get('asin')})"
                                )

                            except Exception as e:  # noqa: X002 - per-card isolation boundary: one malformed card (Playwright; URL-parse; indexing; or missing-attribute error) must skip only that card; not abort the page
                                logger.error(
                                    f"Error scraping book card on {site_name} (page {current_page_num}, card {i + 1}): {e}",
                                    exc_info=True,
                                )
                                print_log(
                                    f"Error scraping book card on {site_name} (page {current_page_num}, card {i + 1}).",
                                    "error",
                                )
                                # Continue to the next card if an error occurs during extraction of this card
                                continue

                        # Stop when there is no enabled next-page control (a missing or disabled
                        # "next" button marks the last page); otherwise continue to the next page.
                        if not await _amazon_has_next_page(page, selectors.get("SEARCH_NEXT_PAGE_SELECTOR")):
                            logger.info(f"Amazon - no enabled next page after page {current_page_num}; stopping.")
                            break

            case _:
                # For other sites, simply encode the query and append.
                # Assuming site_constants for other sites correctly define SEARCH_BASE_URL
                # to which the encoded query can be directly appended.
                pass

    except Exception as e:  # noqa: X002 - top-level isolation boundary: any unhandled failure logs critical and returns the preliminary books gathered so far rather than crashing the search
        logger.critical(
            f"An unhandled error occurred during search on {site_name} for query '{query}': {e}",
            exc_info=True,
        )
        print_log(
            f"Critical error during search on {site_name} for '{query}'. Check logs.",
            "error",
        )

    print_log(
        f"Finished search for '{query}' on {site_name}. Found {len(books_from_search_results)} preliminary books.",
        "info",
    )
    return books_from_search_results
