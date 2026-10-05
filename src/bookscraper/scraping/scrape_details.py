from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from html import unescape
from typing import Optional
from urllib.parse import urljoin

import httpx
from playwright.async_api import Browser, Error as PlaywrightError, Page, Route, TimeoutError

from ..backends import StorageBackend
from ..book_utils import extract_year_from_date, hash_book, print_log
from .parameters import USER_AGENTS, site_constants
from .results import ScrapeResult, ScrapeStatus

# The site identifier carried on every Leanpub ScrapeResult (the httpx/JSON path).
LEANPUB_SITE = "leanpub"

# Per-read / per-attempt timeout budgets (milliseconds) and the retry cap. These are module
# constants so a later CLI (subtask 05.0) can override them; required fields get the longer
# FIELD_TIMEOUT_MS, optional ones the shorter OPTIONAL_FIELD_TIMEOUT_MS.
NAVIGATION_TIMEOUT_MS = 60_000
FIELD_TIMEOUT_MS = 10_000
OPTIONAL_FIELD_TIMEOUT_MS = 2_000
MAX_ATTEMPTS = 3

# Substrings (compared case-insensitively) in a Playwright error message that mark a
# *transient* navigation/network fault worth retrying - as opposed to a deterministic
# selector/parse bug that would fail identically on every attempt.
_TRANSIENT_PLAYWRIGHT_MARKERS: tuple[str, ...] = ("net::err_", "target closed", "navigation failed")

# Get a logger instance specifically for this module
module_logger = logging.getLogger("scrape_details")


def _is_transient_error(exc: BaseException) -> bool:
    """Return whether ``exc`` is a transient failure a retry could plausibly recover from.

    Transient failures are a Playwright navigation timeout
    (:class:`playwright.async_api.TimeoutError`), a :class:`playwright.async_api.Error` whose
    message names a navigation/network fault (``net::ERR_*`` / ``Target closed`` /
    ``Navigation failed``), or an :mod:`httpx` transport error. Deterministic bugs -
    ``KeyError``, ``AttributeError``, ``ValueError``, ``TypeError``, a malformed selector -
    are **not** transient and must fail immediately rather than waste the retry budget.

    :param exc: The exception raised while scraping a single attempt.
    :returns: ``True`` iff retrying the attempt could plausibly succeed.
    """
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, PlaywrightError):
        message = str(exc).lower()
        return any(marker in message for marker in _TRANSIENT_PLAYWRIGHT_MARKERS)
    return False


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
        module_logger.debug(f"No text for selector {selector!r}: {exc}")
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
        module_logger.debug(f"No attribute {name!r} for selector {selector!r}: {exc}")
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
        module_logger.debug(f"No matches for selector {selector!r}: {exc}")
        return texts
    for element in elements:
        try:
            text = await element.text_content(timeout=timeout_ms)
        except PlaywrightError as exc:
            module_logger.debug(f"Skipping unreadable element for selector {selector!r}: {exc}")
            continue
        if text and text.strip():
            texts.append(text.strip())
    return texts


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


@asynccontextmanager
async def _attempt_page(browser: Browser) -> AsyncIterator[Page]:
    """Yield a fresh page for a single scrape attempt, closing it exactly once on exit.

    The page is closed on **every** exit path - normal return, duplicate/404 short-circuit,
    timeout, or any other exception - so an attempt can never leak a page. A failure to
    close (for example when the browser has already crashed) is logged at debug level and
    swallowed so it never masks the original exception that is unwinding the ``with`` block.

    :param browser: The Playwright browser to open the page on.
    :returns: An async context manager yielding the attempt's :class:`Page`.
    """
    page = await browser.new_page()
    try:
        yield page
    finally:
        try:
            await page.close()
        except Exception as exc:  # noqa: X002 - cleanup isolation boundary: a page-close failure (any type; e.g. "browser already closed") must never mask the original error unwinding the with-block
            module_logger.debug(f"Ignoring page-close failure: {exc}")


async def get_leanpub_book_details(url: str) -> ScrapeResult:
    """Fetch detailed information for a single Leanpub book from its JSON:API endpoint.

    Every path returns a :class:`ScrapeResult`: a :attr:`ScrapeStatus.SUCCESS` carrying the
    book document, or a :attr:`ScrapeStatus.FAILED` carrying a short, secret-free ``error``
    (the raw HTTP response body is never put in ``error``; only the status code is). Refining
    the "no data" / unparseable-field cases into richer statuses is deferred to subtask 03.0/05.

    :param url: The API endpoint of the book (e.g.
        ``https://leanpub.com/api/v1/cache/books/quickguidetodatasciencewithpython.json``).
    :returns: A :class:`ScrapeResult` for the scrape attempt.
    """
    # In a real application, you would make an HTTP request here:
    params = {"include": "accepted_authors"}  # Keep this if 'included' authors are still needed from API

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=30.0)
            response.raise_for_status()
            response_data = response.json()

            book_data = response_data.get("data")
            if not book_data:
                print(f"No 'data' found in response for book slug: {url}")
                return ScrapeResult(ScrapeStatus.FAILED, url=url, site=LEANPUB_SITE, error="no 'data' in response")

            attributes = book_data.get("attributes", {})
            relationships = book_data.get("relationships", {})
            included = response_data.get("included", [])  # Relevant for authors
            about_the_book = unescape(attributes.get("about_the_book"))
            about_the_book = re.sub(r"</p>", "\n\n", about_the_book, flags=re.IGNORECASE)
            about_the_book = re.sub(r"</li>", "\n", about_the_book, flags=re.IGNORECASE)
            about_the_book = re.sub(r"<[^>]*>", "", about_the_book)
            about_the_book = re.sub(r"\n\n+", "\n\n", about_the_book).strip()
            about_the_book = re.sub(r" +", " ", about_the_book)

            extracted_details = {
                "site": "leanpub.com",
                "title": attributes.get("title"),
                "book_id": book_data.get("id"),
                "slug": attributes.get("slug"),
                "authors": [],
                "about_the_book": about_the_book,
                "categories": [],
                "last_published_at": None,
                "hash": "",
            }

            # Extract Last published at
            last_published_at_str = attributes.get("last_published_at")
            if last_published_at_str:
                try:
                    # 'Z' indicates UTC. datetime.fromisoformat handles '+00:00'
                    dt_object = datetime.fromisoformat(last_published_at_str.replace("Z", "+00:00"))
                    extracted_details["last_published_at"] = dt_object.strftime("%Y-%m-%d")
                except ValueError:
                    print_log(f"Warning: Could not parse date string: '{last_published_at_str}'", "warning")
                    extracted_details["last_published_at"] = None

            # Extract authors
            author_lookup = {}
            for item in included:
                # Ensure the 'type' matches 'Author' now, not 'SimpleAuthor'
                if item.get("type") == "Author":
                    author_id = item.get("id")
                    author_name = item.get("attributes", {}).get("name")
                    if author_id and author_name:
                        author_lookup[author_id] = author_name

            accepted_authors_data = relationships.get("accepted_authors", {}).get("data", [])
            for author_rel in accepted_authors_data:
                author_id = author_rel.get("id")
                if author_id and author_id in author_lookup:
                    extracted_details["authors"].append(author_lookup[author_id])

            # Extract Categories from data.attributes.categories
            categories_data = attributes.get("categories", [])
            for category_item in categories_data:
                category_name = category_item.get("name")
                if category_name:
                    extracted_details["categories"].append(category_name)

            extracted_details["hash"] = hash_book(
                extracted_details["title"],
                extracted_details["authors"],
                extract_year_from_date(extracted_details["last_published_at"]),
            )

            return ScrapeResult(ScrapeStatus.SUCCESS, url=url, site=LEANPUB_SITE, book=extracted_details)

    except httpx.HTTPStatusError as e:
        print(f"HTTP error occurred: {e.response.status_code} - {e.response.text}")
        return ScrapeResult(ScrapeStatus.FAILED, url=url, site=LEANPUB_SITE, error=f"HTTP {e.response.status_code}")
    except httpx.RequestError as exc:
        print(f"An HTTP error occurred while requesting {exc.request.url!r}: {exc}")
        return ScrapeResult(ScrapeStatus.FAILED, url=url, site=LEANPUB_SITE, error="request error")
    except json.JSONDecodeError:
        print(f"Failed to decode JSON response for url '{url}'. Response content: {response.text[:200]}...")
        return ScrapeResult(ScrapeStatus.FAILED, url=url, site=LEANPUB_SITE, error="invalid JSON response")
    except Exception as e:  # noqa: X002 - fail-soft boundary: get_leanpub_book_details must always return a ScrapeResult (never raise); so any unexpected JSON-shape/processing error is converted to a FAILED result
        print(f"An unexpected error occurred for url '{url}': {e}")
        return ScrapeResult(ScrapeStatus.FAILED, url=url, site=LEANPUB_SITE, error=f"{type(e).__name__}: {e}")


async def scrape_book(
    url: str, browser: Browser, site: str, storage_backend: Optional[StorageBackend] = None
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
    :returns: A :class:`ScrapeResult` for the scrape attempt.
    """
    # Leanpub is a pure httpx/JSON path - dispatch it before any Playwright page is created,
    # so it never opens (and therefore never leaks) a browser page.
    if site == "leanpub":
        print("Leanpub book")
        return await get_leanpub_book_details(url)

    for attempt in range(1, MAX_ATTEMPTS + 1):
        module_logger.debug(f"Scrape attempt {attempt} of {MAX_ATTEMPTS} for {url}.")

        try:
            # Each attempt owns exactly one page; the context manager closes it on every
            # exit path (success, duplicate, 404, parse error, timeout, or any other exception).
            async with _attempt_page(browser) as page:
                user_agent = random.choice(USER_AGENTS)
                await page.set_extra_http_headers({"User-Agent": user_agent})
                await page.route("**/*", route_handler)

                selectors = site_constants[site]
                # A missing BASE_URL is no longer fatal: ``urljoin`` leaves an already-absolute
                # URL untouched, so a site whose constants omit BASE_URL still scrapes an
                # absolute detail URL (previously this raised KeyError and wasted all retries).
                base_url = selectors.get("BASE_URL") or ""
                full_url = urljoin(base_url, url)
                module_logger.info(f"Navigating to {full_url} for detailed scraping.")
                print_log(f"Navigating to {full_url} for detailed scraping.", "info")

                await page.goto(full_url, timeout=NAVIGATION_TIMEOUT_MS)

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

                # Every output key is initialized up front with a neutral value, so a missing
                # optional field can never raise a KeyError downstream (e.g. at the hash line).
                book_details: dict = {
                    "url": full_url,
                    "site": site,
                    "title": None,
                    "authors": [],
                    "publication_date": None,
                    "publication_year": None,
                    "isbn10": "N/A",
                    "isbn13": "N/A",
                    "description": None,
                }

                # Title (required): a missing or empty title after the page loads is a parse
                # error, not a transient fault - return immediately with no retry.
                title_selector = selectors.get("BOOK_TITLE")
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
                meta_author_selector = selectors.get("AUTHORS_META")
                author_selector = selectors.get("AUTHORS")
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
                        alt_author_selector = selectors.get("AUTHORS_ALT")
                        if alt_author_selector:
                            authors_list = await _collect_texts(
                                page, alt_author_selector, timeout_ms=OPTIONAL_FIELD_TIMEOUT_MS
                            )
                    book_details["authors"] = authors_list

                # Publication date (metadata ``content`` attribute, else page text).
                meta_date_selector = selectors.get("PUBLICATION_DATE_META")
                if meta_date_selector:
                    pub_date_text = await _attr(page, meta_date_selector, "content", timeout_ms=FIELD_TIMEOUT_MS)
                    if pub_date_text:
                        book_details["publication_date"] = pub_date_text.split("T")[0]
                        book_details["publication_year"] = extract_year_from_date(book_details["publication_date"])
                else:
                    date_selector = selectors.get("PUBLICATION_DATE")
                    if date_selector:
                        pub_date_text = await _text(page, date_selector, timeout_ms=FIELD_TIMEOUT_MS)
                        if pub_date_text:
                            book_details["publication_date"] = pub_date_text
                            book_details["publication_year"] = extract_year_from_date(pub_date_text)

                # ISBN10 & ISBN13 (optional - keep the "N/A" sentinel when absent; 03.1 replaces it).
                isbn10_selector = selectors.get("ISBN10")
                if isbn10_selector:
                    isbn10 = await _text(page, isbn10_selector, timeout_ms=OPTIONAL_FIELD_TIMEOUT_MS)
                    if isbn10:
                        book_details["isbn10"] = isbn10
                isbn13_selector = selectors.get("ISBN13")
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
                        module_logger.info(f"Extracted ASIN: {book_details['asin']} for {book_details['title']}.")

                # Description (optionally behind a "Read more" expander).
                description_selector = selectors.get("DESCRIPTION")
                if description_selector:
                    read_more_selector = selectors.get("READ_MORE_LINK")
                    if read_more_selector:
                        try:
                            read_more_button = page.locator(read_more_selector).first
                            if await read_more_button.is_visible():
                                await read_more_button.click(timeout=5000)
                                await page.wait_for_timeout(500)
                        except PlaywrightError as exc:
                            module_logger.debug(f"No usable 'Read more' expander for {full_url}: {exc}")
                    book_details["description"] = await _text(page, description_selector, timeout_ms=FIELD_TIMEOUT_MS)

                # Generate hash
                book_details["hash"] = hash_book(
                    book_details["title"], book_details["authors"], book_details["publication_year"]
                )

                # Check for duplicates BEFORE returning, if a storage backend is provided
                if storage_backend is not None:
                    is_duplicate = storage_backend.book_exists_by_hash(book_details["hash"])
                    if is_duplicate:
                        module_logger.info(
                            f"Book with hash '{book_details.get('hash')}' (ISBN10: {book_details.get('isbn10')}, ISBN13: {book_details.get('isbn13')}) already exists in DB. Skipping URL: {url}"
                        )
                        print_log(f"Skipping duplicate book for URL: {url}", "warning")
                        return ScrapeResult(ScrapeStatus.DUPLICATE, url=full_url, site=site, attempts=attempt)

                # If all successful and not a duplicate:
                module_logger.info(f"Successfully scraped details for {book_details['title']}")
                return ScrapeResult(ScrapeStatus.SUCCESS, url=full_url, site=site, book=book_details, attempts=attempt)

        except Exception as exc:  # noqa: X002 - single retry/fail boundary - every per-attempt exception is classified by _is_transient_error and then either retried or converted to a FAILED/TIMEOUT ScrapeResult so one URL can never raise out of scrape_book and abort the asyncio.gather batch
            transient = _is_transient_error(exc)
            if transient and attempt < MAX_ATTEMPTS:
                module_logger.warning(f"Transient error scraping {url} on attempt {attempt}: {exc}. Retrying.")
                print_log(f"Transient error scraping {url}. Retrying...", "warning")
                await asyncio.sleep(random.uniform(2, 4))  # Wait before retrying
                continue  # Try next attempt
            if transient:
                # Exhausted the retry budget on a transient failure.
                status = ScrapeStatus.TIMEOUT if isinstance(exc, TimeoutError) else ScrapeStatus.FAILED
                error_text = (
                    f"timed out after {MAX_ATTEMPTS} attempts"
                    if status is ScrapeStatus.TIMEOUT
                    else f"transient failure after {MAX_ATTEMPTS} attempts"
                )
                module_logger.error(f"Failed to scrape {url} after {MAX_ATTEMPTS} attempts: {exc}")
                print_log(f"Failed to scrape {url} after {MAX_ATTEMPTS} attempts.", "error")
                return ScrapeResult(status, url=url, site=site, error=error_text, attempts=attempt)
            # Deterministic bug (KeyError / AttributeError / ValueError / TypeError) or any
            # unexpected error: never retried - it would fail identically on every attempt.
            module_logger.error(f"Non-transient error scraping {url} on attempt {attempt}: {exc}", exc_info=True)
            print_log(f"Error scraping {url}.", "error")
            return ScrapeResult(
                ScrapeStatus.FAILED,
                url=url,
                site=site,
                error=f"{type(exc).__name__}: {exc}",
                attempts=attempt,
            )

    # The loop only exits here if every attempt fell through without returning; the
    # per-attempt handler above already returns on the final attempt, so this is the
    # explicit (defensive) post-loop failure return.
    return ScrapeResult(
        ScrapeStatus.FAILED,
        url=url,
        site=site,
        error="scrape exhausted all attempts",
        attempts=MAX_ATTEMPTS,
    )
