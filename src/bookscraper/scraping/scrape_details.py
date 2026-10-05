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

# Get a logger instance specifically for this module
module_logger = logging.getLogger("scrape_details")


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

    retries = 3
    for attempt in range(1, retries + 1):
        print(f"Trying attempt {attempt}...")

        try:
            # Each attempt owns exactly one page; the context manager closes it on every
            # exit path (success, duplicate, 404, timeout, or any other exception).
            async with _attempt_page(browser) as page:
                user_agent = random.choice(USER_AGENTS)
                await page.set_extra_http_headers({"User-Agent": user_agent})
                await page.route("**/*", route_handler)

                # --- Crucial: Construct the full URL using urljoin ---
                base_url = site_constants[site]["BASE_URL"]
                if not base_url:
                    module_logger.error(f"BASE_URL not defined for site: {site}. Cannot scrape {url}")
                    print_log(f"Error: BASE_URL not defined for {site}. Skipping {url}.", "error")
                    return ScrapeResult(
                        ScrapeStatus.FAILED,
                        url=url,
                        site=site,
                        error=f"BASE_URL not defined for site '{site}'",
                        attempts=attempt,
                    )

                full_url = urljoin(base_url, url)
                module_logger.info(f"Navigating to {full_url} for detailed scraping.")
                print_log(f"Navigating to {full_url} for detailed scraping.", "info")

                await page.goto(full_url, timeout=60000)

                # Check for 404 page (if site_constants provides a 404 selector)
                if site_constants[site].get("404_PAGE_TITLE"):
                    page_title = await page.title()
                    if site_constants[site]["404_PAGE_TITLE"].lower() in page_title.lower():
                        module_logger.warning(f"404 page detected for {full_url}. Skipping.")
                        print_log(f"404 page detected for {full_url}. Skipping.", "warning")
                        return ScrapeResult(
                            ScrapeStatus.NOT_FOUND,
                            url=full_url,
                            site=site,
                            error="404 page detected",
                            attempts=attempt,
                        )

                # Scrape data fields using selectors from site_constants
                book_details = {
                    "url": full_url,
                    "site": site,
                }
                selectors = site_constants[site]

                # Title
                title_selector = selectors.get("BOOK_TITLE")
                if title_selector:
                    title_element = await page.locator(title_selector).first.text_content()
                    book_details["title"] = title_element.strip() if title_element else None
                    print(book_details["title"])
                else:
                    module_logger.warning(f"No BOOK_TITLE selector for {site}. Skipping title extraction.")

                # Authors
                authors_list = []
                # Metadata authors (e.g., for Leanpub)
                meta_author_selector_meta = selectors.get("AUTHORS_META")
                author_selector = selectors.get("AUTHORS")

                if meta_author_selector_meta:
                    all_author_elements = await page.locator(meta_author_selector_meta).all()
                    for author_element in all_author_elements:
                        author_name = await author_element.get_attribute("content")
                        if author_name:
                            authors_list.append(author_name)

                # HTML Scraping alternative
                elif author_selector:
                    author_elements = await page.locator(author_selector).all()
                    for author_el in author_elements:
                        author_name = await author_el.text_content()
                        if author_name:
                            authors_list.append(author_name.strip())
                    if not authors_list:
                        alt_author_selector = selectors.get("AUTHORS_ALT")
                        if alt_author_selector:
                            author_elements = await page.locator(alt_author_selector).all()
                            for author_el in author_elements:
                                author_name = await author_el.text_content()
                                if author_name:
                                    authors_list.append(author_name.strip())

                book_details["authors"] = authors_list if authors_list else []

                # Publication Date
                try:
                    # use Metadata (e.g., for Leanpub)
                    meta_publication_date_selector = selectors.get("PUBLICATION_DATE_META")
                    if meta_publication_date_selector:
                        pub_date_text = await page.locator(meta_publication_date_selector).get_attribute(
                            "content", timeout=10000
                        )
                        book_details["publication_date"] = pub_date_text.split("T")[0]
                        book_details["publication_year"] = extract_year_from_date(book_details["publication_date"])
                    else:
                        publication_date_selector = selectors.get("PUBLICATION_DATE")
                        if publication_date_selector:
                            pub_date_text = await page.locator(publication_date_selector).first.text_content(
                                timeout=10000
                            )
                            book_details["publication_date"] = pub_date_text.strip() if pub_date_text else None
                            book_details["publication_year"] = extract_year_from_date(book_details["publication_date"])
                        else:
                            module_logger.warning(f"No PUBLICATION_DATE selector for {site}.")
                except (PlaywrightError, AttributeError) as e:
                    # A missing/slow date element raises a Playwright error; a None
                    # get_attribute/text_content result makes the subsequent .split()/.strip()
                    # raise AttributeError. Either leaves publication_date unset, not fatal.
                    module_logger.warning(f"No PUBLICATION_DATE selector for {site}: {e}.")

                # ISBN10 & ISBN13
                isbn10_selector = selectors.get("ISBN10")
                isbn13_selector = selectors.get("ISBN13")

                if isbn10_selector:
                    try:
                        isbn10_text = await page.locator(isbn10_selector).first.text_content(timeout=2000)
                        book_details["isbn10"] = isbn10_text.strip() if isbn10_text else "N/A"
                    except TimeoutError:
                        # If a TimeoutError occurs, the element was not found within the timeout
                        book_details["isbn10"] = "N/A"
                        module_logger.info(f"ISBN10 element not found for {url}.")
                else:
                    book_details["isbn10"] = "N/A"

                if isbn13_selector:
                    try:
                        isbn13_text = await page.locator(isbn13_selector).first.text_content(timeout=2000)
                        book_details["isbn13"] = isbn13_text.strip() if isbn13_text else "N/A"
                    except TimeoutError:
                        # If a TimeoutError occurs, the element was not found within the timeout
                        book_details["isbn13"] = "N/A"
                        module_logger.info(f"ISBN13 element not found for {url}.")
                else:
                    # If the selector itself is not defined in parameters.py
                    book_details["isbn13"] = "N/A"

                # Amazon ASIN extraction
                if site == "amazon":
                    try:
                        asin_match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", url)
                        if asin_match:
                            book_details["asin"] = asin_match.group(1)
                            module_logger.info(f"Extracted ASIN: {book_details['asin']} for {book_details['title']}.")
                    except (KeyError, TypeError) as e:
                        # KeyError if 'title' was never set (no title selector); TypeError if
                        # the URL is not a string. A missing ASIN is non-fatal.
                        module_logger.warning(f"Could not extract ASIN from Amazon URL {url}: {e}")

                # Description
                description_selector = selectors.get("DESCRIPTION")
                if description_selector:
                    description_element = page.locator(description_selector).first
                    if description_element:
                        # Check for a "Read more" link/button to expand description
                        read_more_selector = selectors.get("READ_MORE_LINK")
                        if read_more_selector:
                            try:
                                read_more_button = page.locator(read_more_selector).first
                                if await read_more_button.is_visible():
                                    await read_more_button.click(timeout=5000)  # Click to expand
                                    await page.wait_for_timeout(500)  # Small wait for content to load
                            except TimeoutError:
                                module_logger.debug(f"No 'Read more' button found or clickable for {url}.")
                            except PlaywrightError as e:
                                module_logger.warning(f"Error clicking 'Read more' on {url}: {e}")

                        description_text = await description_element.text_content()
                        book_details["description"] = description_text.strip() if description_text else None
                    else:
                        book_details["description"] = None
                else:
                    module_logger.warning(f"No DESCRIPTION selector for {site}.")
                    book_details["description"] = None

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

        except TimeoutError as e:
            module_logger.error(f"Timeout while scraping {url} on attempt {attempt}: {e}")
            print_log(f"Timeout scraping {url}. Retrying...", "error")
            if attempt < retries:
                await asyncio.sleep(random.uniform(2, 4))  # Wait before retrying
                continue  # Try next attempt
            module_logger.error(f"Failed to scrape {url} after {retries} attempts due to timeout.")
            print_log(f"Failed to scrape {url} after {retries} attempts due to timeout.", "error")
            return ScrapeResult(
                ScrapeStatus.TIMEOUT,
                url=url,
                site=site,
                error=f"timed out after {retries} attempts",
                attempts=attempt,
            )

        except Exception as e:  # noqa: X002 - per-attempt retry boundary: any non-timeout scrape failure is retried then reported as FAILED; the retry policy is reworked by subtask 03.0/03; so narrowing is deferred to avoid redesigning it here
            module_logger.error(f"Error scraping {url} on attempt {attempt}: {e}", exc_info=True)
            print_log(f"Error scraping {url}. Retrying...", "error")
            if attempt < retries:
                await asyncio.sleep(random.uniform(2, 4))  # Wait before retrying
                continue  # Try next attempt
            module_logger.error(f"Failed to scrape {url} after {retries} attempts.")
            print_log(f"Failed to scrape {url} after {retries} attempts.", "error")
            return ScrapeResult(
                ScrapeStatus.FAILED,
                url=url,
                site=site,
                error=f"{type(e).__name__}: {e}",
                attempts=attempt,
            )

    # The loop only exits here if every attempt fell through without returning; the
    # per-attempt handlers above already return on the final attempt, so this is the
    # explicit post-loop failure return.
    return ScrapeResult(
        ScrapeStatus.FAILED,
        url=url,
        site=site,
        error="scrape exhausted all attempts",
        attempts=retries,
    )
