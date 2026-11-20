from __future__ import annotations

import argparse
import asyncio
import logging
import time
from pathlib import Path

from ..backends import resolve_dedup_source
from ..book_utils import print_log, save_json_report
from ..scraping.parameters import SEARCH_QUERIES, SITES_TO_SCRAPE, site_constants
from ..scraping.scrape_details import get_leanpub_book_details
from ..scraping.search_utils import get_leanpub_search_results_via_api
from .staging_output import print_step_one_summary, save_to_staging, warn_about_legacy_store

module_logger = logging.getLogger(__name__)


FAILED_URL_FIELDS = ("url", "site", "error")


def save_failed_urls_to_json(failed_urls: list[dict], filename: str = "failed_urls.json") -> None:
    """
    Saves failed detail-scrape attempts as a JSON array of {"url", "site", "error"} objects.

    Every record is normalized to exactly those three keys, in that order: a missing key
    becomes "" and any extra key is dropped. This keeps one fixed record shape no matter
    what the producing code put in the dict, as the old CSV's fixed header columns did.

    Args:
        failed_urls: Dicts each carrying 'url' (the URL that failed to scrape), 'site'
            (the site it belongs to), and 'error' (the associated error message).
        filename: The JSON file to create.
    """
    records = [{field: item.get(field, "") for field in FAILED_URL_FIELDS} for item in failed_urls]
    save_json_report(records, filename, "failed URLs")


async def run(args: argparse.Namespace) -> None:
    start_time = time.time()
    module_logger.info("Application started.")

    staging_file = Path(args.staging_file)
    warn_about_legacy_store(staging_file)
    dedup_source = resolve_dedup_source(staging_file, args.check_mongo)

    # --- Main Scraping Logic Starts Here ---
    scraped_books_data = []  # List to store successfully scraped, detailed book dictionaries to save
    failed_scrape_attempts = []  # List to store book data that failed detailed scraping
    total_duplicates_skipped = 0  # To sum up duplicates across all sites
    books_from_search = []

    try:
        # --- Iterate through SITES_TO_SCRAPE and perform search + scrape ---
        for site_name in SITES_TO_SCRAPE:
            print_log(f"\n--- Processing {site_name.title()} ---", "step")

            for search_item in SEARCH_QUERIES:
                current_search_results = []
                # Leanpub-Specific
                if site_name.lower() == "leanpub":
                    print_log(f"Leanpub - Searching for '{search_item}' via API...", "info")

                    # Get search results - Format: [{'site', 'title', 'book_id', 'slug', 'authors'}]
                    current_search_results = await get_leanpub_search_results_via_api(search_item)

                    # Pre-scrape deduplication
                    print_log("Leanpub - Deduplicating search results...")
                    for book in current_search_results:
                        book_title = book.get("title")
                        book_id = book.get("book_id")
                        book_slug = book.get("slug")
                        book_url = site_constants["leanpub"]["SINGLE_BOOK_API"].replace("[slug]", book_slug)
                        book["book_url"] = book_url
                        exists = await asyncio.to_thread(dedup_source.leanpub_book_exists, book_id, book_slug)

                        if not exists:
                            # Format: {'site', 'title', 'book_id', 'slug', 'authors', 'book_url'}
                            books_from_search.append(book)
                            print_log(
                                f"{site_name.title()} - New book found: {book_title}",
                                "success",
                            )
                        else:
                            total_duplicates_skipped += 1
                            print_log(
                                f"{site_name.title()} - Book already staged or in the database: {book_title}",
                                "warning",
                            )

                print_log(
                    f"Found {len(current_search_results)} potential new books for '{search_item}'.",
                    "success",
                )
                module_logger.info(f"Found {len(current_search_results)} potential new books for '{search_item}'.")

        # --- Deduplicate Cross-Site using ISBN / Fuzzy search / Hash ---
        new_books_for_detailed_scrape = books_from_search

        # --- Queue Detailed Scraping Tasks for current site ---
        site_scrape_tasks = []
        for book_data_to_scrape in new_books_for_detailed_scrape:
            if book_data_to_scrape["site"].lower() == "leanpub":
                print_log(f"--- Scraping {len(book_data_to_scrape)} Book Data ---", "step")
                # Scraping Leanpub details via httpx API
                site_scrape_tasks.append(get_leanpub_book_details(url=book_data_to_scrape.get("book_url")))

        # Run detailed scraping tasks for the current site concurrently
        if site_scrape_tasks:
            current_site_scraped_results = await asyncio.gather(*site_scrape_tasks, return_exceptions=True)

            # Process results from detailed scraping for the current site
            for original_book_data, result in zip(
                new_books_for_detailed_scrape, current_site_scraped_results, strict=True
            ):
                if isinstance(result, dict):
                    scraped_books_data.append(result)
                    print_log(
                        f"Successfully scraped details for: {result.get('title', 'Unknown Title')} from {site_name}",
                        "info",
                    )
                elif isinstance(result, Exception):
                    failed_scrape_attempts.append(
                        {
                            "url": original_book_data.get("url", "N/A"),
                            "site": original_book_data.get("site", "N/A"),
                            "title": original_book_data.get("title", "N/A"),
                            "error": str(result),
                        }
                    )
                    print_log(
                        f"Failed to scrape details for {original_book_data.get('url', 'N/A')} from {site_name}: {str(result)}",
                        "error",
                    )
                else:
                    failed_scrape_attempts.append(
                        {
                            "url": original_book_data.get("url", "N/A"),
                            "site": original_book_data.get("site", "N/A"),
                            "title": original_book_data.get("title", "N/A"),
                            "error": f"Detailed scrape returned unexpected type: {type(result)} - {str(result)}",
                        }
                    )
                    print_log(
                        f"Failed to scrape details for {original_book_data.get('url', 'N/A')} from {site_name}: Unexpected result type.",
                        "error",
                    )
        else:
            print_log(f"No new books identified for detailed scraping on {site_name}.", "info")

    except KeyboardInterrupt:
        print_log("Scraping interrupted by user.", "warning")
        module_logger.warning("Application interrupted by user.")
    except Exception as e:
        module_logger.critical(f"An unhandled error occurred in main execution: {e}", exc_info=True)
        print_log(f"Critical error in main execution. Check logs: {e}", "error")

    # --- End of Site Iteration ---

    total_time = time.time() - start_time
    print_log("\n--- Scraping Process Summary ---", "step")
    print_log(f"Total scraping process completed in {total_time:.2f} seconds.")
    print_log(f"{len(scraped_books_data)} new book{'s' if len(scraped_books_data) != 1 else ''} scraped.")
    print_log(
        f"{len(failed_scrape_attempts)} URLs failed during detailed scrape.",
        "warning" if len(failed_scrape_attempts) > 0 else "info",
    )
    print_log(f"{total_duplicates_skipped} books already staged (or in MongoDB) and skipped.")

    # --- Output Saving ---
    print_log("\n--- Output Summary ---", "step")
    # failed_urls.json is a process diagnostic, not book data, so it's always written best-effort.
    save_failed_urls_to_json(failed_scrape_attempts)

    # Step 1's only write: append to the staging file for review (ADR 0003). Never MongoDB.
    summary = save_to_staging(staging_file, scraped_books_data, source="search")
    dedup_source.close()
    print_step_one_summary(staging_file, summary, skipped_as_known=total_duplicates_skipped)

    print_log("\nApplication finished.", "step")
    module_logger.info("Application finished.")
