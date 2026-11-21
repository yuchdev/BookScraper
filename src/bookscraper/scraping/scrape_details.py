from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from datetime import datetime
from html import unescape
from typing import Optional, Union
from urllib.parse import urljoin

import httpx
from playwright.async_api import Browser, Route, TimeoutError

from ..backends import DedupSource
from ..book_utils import download_cover_image, extract_year_from_date, hash_book, print_log
from .parameters import USER_AGENTS, site_constants

# Get a logger instance specifically for this module
module_logger = logging.getLogger("scrape_details")


def _largest_image_from_dynamic(raw: Optional[str]) -> Optional[str]:
    """Parse Amazon's ``data-a-dynamic-image`` attribute (a JSON object mapping each image
    URL to its ``[width, height]``) and return the highest-resolution URL, or None if the
    attribute is absent/unparseable. Pure."""
    if not raw:
        return None
    try:
        mapping = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(mapping, dict) or not mapping:
        return None
    best_url = None
    best_area = -1
    for candidate_url, dimensions in mapping.items():
        area = 0
        if isinstance(dimensions, (list, tuple)) and len(dimensions) == 2:
            try:
                area = int(dimensions[0]) * int(dimensions[1])
            except (TypeError, ValueError):
                area = 0
        if area > best_area:
            best_area = area
            best_url = candidate_url
    return best_url


async def route_handler(route: Route) -> None:
    request = route.request
    # Allow document (HTML page) requests to go through.
    # Block other resource types (images, fonts, stylesheets, etc.) to speed up scraping.
    if request.resource_type == "document":
        await route.continue_()
    else:
        await route.abort()


async def get_leanpub_book_details(url: str) -> Optional[dict]:
    """
    Fetches detailed information for a single Leanpub book from the provided JSON structure.

    Args:
        url (str): The API endpoint of the book (e.g., "https://leanpub.com/api/v1/cache/books/quickguidetodatasciencewithpython.json").

    Returns:
        dict: A dictionary containing extracted book details, or None if an error occurs.
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
                return None

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
            # Public book page, so a staged document carries a 'url' and a staging file is valid
            # `scrape-urls -f` input (the API URL we fetched is not user-facing). Without a slug the
            # key is omitted, never set to None: the staging validator rejects a present-but-empty
            # 'url', so one None would refuse the whole push.
            if slug := attributes.get("slug"):
                extracted_details["url"] = f"{site_constants['leanpub']['BASE_URL']}/{slug}"

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

            # Cover image: pulled straight from the API JSON (Leanpub never uses CSS
            # selectors). The exact attribute key could not be verified against the live
            # cache API from this environment, so a small ordered list of the known
            # cover-field names is tried and a miss is logged loudly rather than silently
            # defaulting - cover_path is None on a miss/failed download, never a bad value.
            cover_url = None
            for cover_key in ("title_page_url", "image_url", "cover_image_url", "page_medium_url"):
                cover_value = attributes.get(cover_key)
                if isinstance(cover_value, str) and cover_value.strip():
                    cover_url = cover_value.strip()
                    break
            if cover_url:
                extracted_details["cover_path"] = await download_cover_image(cover_url, extracted_details.get("title"))
            else:
                print_log(
                    f"No cover-image attribute found on Leanpub book '{extracted_details.get('slug')}'.", "warning"
                )
                extracted_details["cover_path"] = None

            extracted_details["hash"] = hash_book(
                extracted_details["title"],
                extracted_details["authors"],
                extract_year_from_date(extracted_details["last_published_at"]),
            )

            return extracted_details

    except httpx.HTTPStatusError as e:
        print(f"HTTP error occurred: {e.response.status_code} - {e.response.text}")
        return None
    except httpx.RequestError as exc:
        print(f"An HTTP error occurred while requesting {exc.request.url!r}: {exc}")
        return None
    except json.JSONDecodeError:
        print(f"Failed to decode JSON response for url '{url}'. Response content: {response.text[:200]}...")
        return None
    except Exception as e:
        print(f"An unexpected error occurred for url '{url}': {e}")
        return None


async def scrape_book(
    url: str, browser: Browser, site: str, dedup_source: Optional[DedupSource] = None
) -> Optional[Union[dict, tuple[None, str]]]:
    """
    Scrapes book details from a given URL, handles retries, and checks for duplicates.

    Args:
        url: The URL of the book page to scrape (can be relative).
        browser: The Playwright browser instance.
        site: The identifier for the website (e.g., "amazon", "leanpub").
        dedup_source: Read-only duplicate lookups (the staging file, plus MongoDB with --check-mongo).

    Returns:
        A dictionary of book details on successful scrape, (None, "DUPLICATE") if already exists,
        or (None, "FAILED") on failure.
    """
    retries = 3
    for attempt in range(1, retries + 1):
        print(f"Trying attempt {attempt}...")
        page = await browser.new_page()

        try:
            user_agent = random.choice(USER_AGENTS)
            await page.set_extra_http_headers({"User-Agent": user_agent})
            await page.route("**/*", route_handler)

            if site == "leanpub":
                print("Leanpub book")
                return await get_leanpub_book_details(url)

            # --- Crucial: Construct the full URL using urljoin ---
            base_url = site_constants[site]["BASE_URL"]
            if not base_url:
                module_logger.error(f"BASE_URL not defined for site: {site}. Cannot scrape {url}")
                print_log(f"Error: BASE_URL not defined for {site}. Skipping {url}.", "error")
                await page.close()
                return (None, "FAILED")

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
                    await page.close()
                    return (None, "FAILED")

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
                        pub_date_text = await page.locator(publication_date_selector).first.text_content(timeout=10000)
                        book_details["publication_date"] = pub_date_text.strip() if pub_date_text else None
                        book_details["publication_year"] = extract_year_from_date(book_details["publication_date"])
                    else:
                        module_logger.warning(f"No PUBLICATION_DATE selector for {site}.")
            except Exception as e:
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

            # Edition, Publisher, Series - same selector-driven pattern as ISBN10/13 above:
            # a defined selector resolves its text (or "N/A" on a miss/timeout), a selector
            # left None in parameters.py always resolves to "N/A".
            for field_key, detail_key in (("EDITION", "edition"), ("PUBLISHER", "publisher"), ("SERIES", "series")):
                field_selector = selectors.get(field_key)
                if field_selector:
                    try:
                        field_text = await page.locator(field_selector).first.text_content(timeout=2000)
                        book_details[detail_key] = field_text.strip() if field_text else "N/A"
                    except TimeoutError:
                        book_details[detail_key] = "N/A"
                        module_logger.info(f"{field_key} element not found for {url}.")
                else:
                    book_details[detail_key] = "N/A"

            # Amazon ASIN extraction
            if site == "amazon":
                try:
                    asin_match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", url)
                    if asin_match:
                        book_details["asin"] = asin_match.group(1)
                        module_logger.info(f"Extracted ASIN: {book_details['asin']} for {book_details['title']}.")
                except Exception as e:
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
                        except Exception as e:
                            module_logger.warning(f"Error clicking 'Read more' on {url}: {e}")

                    description_text = await description_element.text_content()
                    book_details["description"] = description_text.strip() if description_text else None
                else:
                    book_details["description"] = None
            else:
                module_logger.warning(f"No DESCRIPTION selector for {site}.")
                book_details["description"] = None

            # Cover image: COVER_IMAGE points at an <img> whose URL lives in an attribute,
            # not in text. Amazon exposes a hi-res URL in data-old-hires (or a resolution map
            # in data-a-dynamic-image); other sites use a plain src. A None selector, a
            # missing element, or a failed download all resolve to cover_path=None and never
            # abort the scrape (same resilience as the ISBN "N/A" fallback above).
            cover_url = None
            cover_selector = selectors.get("COVER_IMAGE")
            if cover_selector:
                try:
                    cover_element = page.locator(cover_selector).first
                    for attribute_name in ("data-old-hires", "src"):
                        attribute_value = await cover_element.get_attribute(attribute_name)
                        if attribute_value and attribute_value.strip():
                            cover_url = attribute_value.strip()
                            break
                    if not cover_url:
                        cover_url = _largest_image_from_dynamic(
                            await cover_element.get_attribute("data-a-dynamic-image")
                        )
                except Exception as e:
                    module_logger.info(f"COVER_IMAGE element not found for {url}: {e}")
            book_details["cover_path"] = (
                await download_cover_image(cover_url, book_details.get("title")) if cover_url else None
            )

            # Generate hash
            book_details["hash"] = hash_book(
                book_details["title"], book_details["authors"], book_details["publication_year"]
            )

            # Check for duplicates BEFORE returning, if a dedup source is provided
            if dedup_source is not None:
                is_duplicate = dedup_source.book_exists_by_hash(book_details["hash"])
                if is_duplicate:
                    module_logger.info(
                        f"Book with hash '{book_details.get('hash')}' (ISBN10: {book_details.get('isbn10')}, ISBN13: {book_details.get('isbn13')}) is already staged or in the database. Skipping URL: {url}"
                    )
                    print_log(f"Skipping duplicate book for URL: {url}", "warning")
                    await page.close()
                    return (None, "DUPLICATE")  # Indicate duplicate status

            # If all successful and not a duplicate:
            module_logger.info(f"Successfully scraped details for {book_details['title']}")
            await page.close()
            return book_details

        except TimeoutError as e:
            module_logger.error(f"Timeout while scraping {url} on attempt {attempt}: {e}")
            print_log(f"Timeout scraping {url}. Retrying...", "error")
            if page:
                await page.close()
            if attempt < retries:
                await asyncio.sleep(random.uniform(2, 4))  # Wait before retrying
                continue  # Try next attempt
            else:
                module_logger.error(f"Failed to scrape {url} after {retries} attempts due to timeout.")
                print_log(f"Failed to scrape {url} after {retries} attempts due to timeout.", "error")
                if page:
                    await page.close()
                return (None, "FAILED")

        except Exception as e:
            module_logger.error(f"Error scraping {url} on attempt {attempt}: {e}", exc_info=True)
            print_log(f"Error scraping {url}. Retrying...", "error")
            if page:
                await page.close()
            if attempt < retries:
                await asyncio.sleep(random.uniform(2, 4))  # Wait before retrying
            else:
                module_logger.error(f"Failed to scrape {url} after {retries} attempts.")
                print_log(f"Failed to scrape {url} after {retries} attempts.", "error")
                return (None, "FAILED")

    # If the loop finishes without a successful scrape (e.g., all retries failed)
    # This part should theoretically be unreachable if all errors are caught, but added for robustness.
    if page:
        await page.close()
    return (None, "FAILED")
