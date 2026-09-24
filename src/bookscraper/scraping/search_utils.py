from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from urllib.parse import (
    parse_qs,
    quote_plus,
    unquote,
    urljoin,
    urlparse,
)  # Added for URL parsing

import httpx
from playwright.async_api import Browser

from ..book_utils import print_log
from .parameters import USER_AGENTS, site_constants
from .scrape_details import route_handler

logger = logging.getLogger(__name__)


async def get_leanpub_search_results_via_api(query: str) -> list[dict]:
    print_log(f"Leanpub - Fetching search results from Leanpub.com for {query}.", "info")
    logger.info(f"Leanpub - Fetching search results from Leanpub.com for {query}.")
    base_url = site_constants["leanpub"]["SEARCH_BASE_API"]

    all_extracted_books = []
    current_page = 1
    author_lookup = {}

    # Use an AsyncClient for efficient session management
    async with httpx.AsyncClient() as client:
        while True:
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
                response = await client.get(base_url, params=params, timeout=30.0)
                response.raise_for_status()
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

                # Increment page number for the next iteration
                current_page += 1

                # Add a small, random delay to be polite and avoid rate limits
                await asyncio.sleep(random.uniform(0.5, 1.5))

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
            except Exception as e:
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


async def get_search_results_via_playwright(
    browser: Browser,
    site_name: str,
    query: str,
    # filters: dict = None
) -> list[dict]:
    """
    Performs a search on a given site for a specific query and scrapes preliminary book data.

    This function navigates to the search results page, extracts initial book information
    (title, authors, publication date, URL, and ASIN for Amazon), and handles pagination
    to scrape multiple search result pages up to a specified maximum.

    It also includes logic to:
    - Set a random user agent for the Playwright page.
    - Route network requests to potentially block unnecessary resources (via `route_handler`).
    - Handle navigation timeouts.
    - Gracefully manage scenarios where no book cards are found on a page.
    - Determine the next page URL based on the current page's URL and the next page button's href.
    - Detect and stop pagination when no active "next page" button is found (either disabled or absent).
    - **Added handling for Amazon sponsored book links to extract the true product URL.**

    Args:
            browser (Browser): The Playwright browser instance to use for navigation and scraping.
            site_name (str): The name of the site to search (e.g., "amazon", "leanpub", "packtpub", "oreilly").
                                             This name is used to look up site-specific selectors and configurations.
            query (str): The search query string (e.g., "Playwright Python", "Data Science with Python").
            max_pages_to_search (int, optional): The maximum number of search result pages to scrape.
                                                                                     Defaults to 1.
            # filters (dict, optional): Dictionary of filters (e.g., min_rating). This parameter
            #                           is currently commented out in the function signature and
            #                           not actively used in the provided code snippet for filtering
            #                           at the search page level.

    Returns:
            list[dict]: A list of dictionaries, where each dictionary represents a preliminary
                                    book record found on the search results pages. Each dictionary typically
                                    contains:
                                    - 'site' (str): The name of the site.
                                    - 'query' (str): The original search query.
                                    - 'search_page_number' (int): The page number where the book was found.
                                    - 'search_result_index' (int): The index of the book on that search page.
                                    - 'title' (str): The book's title.
                                    - 'authors_from_search' (str): Authors as found on the search page (comma-separated).
                                    - 'publication_date' (str): Publication date as found on the search page.
                                    - 'url' (str): The URL to the book's detailed product page.
                                    - 'asin' (str, optional): The ASIN for Amazon books, if available.

    Raises:
            Exception: Catches and logs any unhandled exceptions that occur during the search
                               process, printing a critical error message to the console.
    """

    books_from_search_results = []
    current_page_num = 0
    page = None

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
                parsed_query = quote_plus(query)
                search_urls = [
                    f"{selectors['SEARCH_BASE_URL']}".replace("[k]", parsed_query).replace("[p]", str(p))
                    for p in range(1, selectors.get("SEARCH_MAXIMUM_PAGES"))
                ]

                for search_result_page in search_urls:
                    current_page_num += 1
                    page = await browser.new_page()
                    user_agent = random.choice(USER_AGENTS)
                    await page.set_extra_http_headers({"User-Agent": user_agent})
                    await page.route("**/*", route_handler)

                    try:
                        await page.goto(
                            search_result_page,
                            wait_until="domcontentloaded",
                            timeout=60000,
                        )
                        await page.wait_for_load_state("networkidle", timeout=60000)
                        actual_url_after_goto = page.url
                        logger.info(f"Actual URL after navigation for page {current_page_num}: {actual_url_after_goto}")
                    except Exception as e:
                        logger.error(
                            f"Failed to navigate to page {current_page_num}: {e}",
                            "error",
                        )

                    # Find all book cards on the current page
                    # Case where no books are found, but might have book cards
                    if selectors.get("SEARCH_NO_BOOKS_FOUND") and (
                        await page.locator(selectors["SEARCH_NO_BOOKS_FOUND"]).count() > 0
                    ):
                        print_log(
                            f"No book cards found on page {current_page_num} for {site_name}.",
                            "warning",
                        )
                        logger.warning(f"No book cards found on page {current_page_num} for {site_name}.")
                        break  # No books to scrape, exit the while loop

                    # If book cards are found
                    await page.wait_for_selector(selectors["SEARCH_BOOK_CARD"])
                    book_cards = await page.locator(selectors["SEARCH_BOOK_CARD"]).all()
                    if not book_cards:
                        print_log(
                            f"No book cards found on page {current_page_num} for {site_name}.",
                            "warning",
                        )
                        logger.warning(f"No book cards found on page {current_page_num} for {site_name}.")
                        break  # No books to scrape, exit the while loop

                    #                    print_log(f"Found {len(book_cards)} book cards on page {current_page_num} for {site_name}.", "info")
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

                            book_url = None
                            asin = None  # Initialize ASIN for Amazon

                            if raw_href:
                                if site_name == "amazon":
                                    # Handles Amazon's sponsored items, extracting the actual URL from the 'url' parameter
                                    if "/sspa/click" in raw_href:
                                        parsed_href = urlparse(raw_href)
                                        query_params = parse_qs(parsed_href.query)

                                        if "url" in query_params:
                                            embedded_path = unquote(query_params["url"][0])
                                            book_url = urljoin(
                                                site_constants["amazon"]["BASE_URL"],
                                                embedded_path,
                                            )
                                            logger.debug(f"Extracted and decoded sponsored URL: {book_url}")

                                            # Attempt to extract ASIN from the final book_url (if available)
                                            asin_match = re.search(
                                                r"/(?:dp|gp/product)/([A-Z0-9]{10})",
                                                book_url,
                                            )
                                            if asin_match:
                                                asin = asin_match.group(1)
                                        else:
                                            logger.warning(f"Sponsored link without 'url' parameter: {raw_href}")
                                            continue  # Skip this card if we can't get a valid URL

                                    # Non-sponsored Amazon link
                                    else:
                                        book_url = urljoin(
                                            site_constants["amazon"]["BASE_URL"],
                                            raw_href,
                                        )
                                        if selectors.get("SEARCH_ASIN_ATTRIBUTE"):
                                            asin = await card.get_attribute(selectors["SEARCH_ASIN_ATTRIBUTE"])
                                        else:
                                            logger.warning(
                                                "Check Amazon parameter SEARCH_ASIN_ATTRIBUTE to locate ASIN in book cards."
                                            )

                                else:
                                    # For non-Amazon sites, just join the URL
                                    book_url = urljoin(site_constants[site_name]["BASE_URL"], raw_href)

                            book_data["url"] = book_url if book_url else None
                            # Amazon ASIN
                            if site_name == "amazon":
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
                            if site_name == "leanpub":
                                names_list = authors[0].split(" and ")
                                book_data["authors_from_search"] = names_list if authors else None
                            else:
                                book_data["authors_from_search"] = ", ".join(authors) if authors else None

                            # Extract Publication Date
                            publication_date_selector = selectors.get("SEARCH_PUBLICATION_DATE")
                            if publication_date_selector:
                                try:
                                    date_element = card.locator(publication_date_selector)
                                    book_data["publication_date"] = (
                                        await date_element.text_content() if await date_element.count() > 0 else None
                                    )
                                except Exception as e:
                                    logger.warning(
                                        f"Could not extract publication date for a book on {site_name} using selector '{publication_date_selector}'. Error: {e}"
                                    )
                                    book_data["publication_date"] = None
                            else:
                                # logger.info(f"No SEARCH_PUBLICATION_DATE selector defined for {site_name}.")
                                book_data["publication_date"] = None

                            books_from_search_results.append(book_data)
                            logger.debug(
                                f"Extracted: {book_data.get('title')} by {book_data.get('authors_from_search')} ({site_name}) (URL: {book_data.get('url')}, ASIN: {book_data.get('asin')})"
                            )

                        except Exception as e:
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

            case _:
                # For other sites, simply encode the query and append.
                # Assuming site_constants for other sites correctly define SEARCH_BASE_URL
                # to which the encoded query can be directly appended.
                pass

    except Exception as e:
        logger.critical(
            f"An unhandled error occurred during search on {site_name} for query '{query}': {e}",
            exc_info=True,
        )
        print_log(
            f"Critical error during search on {site_name} for '{query}'. Check logs.",
            "error",
        )
    finally:
        if page:
            await page.close()
            logger.info(f"Page for {site_name} search closed.")

    print_log(
        f"Finished search for '{query}' on {site_name}. Found {len(books_from_search_results)} preliminary books.",
        "info",
    )
    return books_from_search_results
