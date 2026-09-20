import argparse
import asyncio
import csv
import logging
import time

from ..book_utils import print_log
from ..database import close_mongo_connection, save_books_to_mongodb
from ..deduplicate import leanpub_prescrape_deduplicate
from ..output import resolve_output_destinations
from ..parameters import SEARCH_QUERIES, SITES_TO_SCRAPE, site_constants
from ..scrape_details import get_leanpub_book_details
from ..search_utils import get_leanpub_search_results_via_api

module_logger = logging.getLogger(__name__)


def save_books_to_csv(books: list[dict], filename="scraped_books.csv") -> None:
    """
    Save a list of book dictionaries to a CSV file.

    This function automatically determines the CSV column headers by collecting all unique keys
    from the provided list of book dictionaries. It writes the data to the specified CSV file,
    handling any errors during the write process and logging relevant information.

    Args:
                    books (list[dict]): A list of dictionaries, each representing a book's data.
                    filename (str, optional): The filename for the output CSV file. Defaults to "scraped_books.csv".

    Returns:
                    None

    Logs:
                    - Info logs for start and successful completion of saving.
                    - Error logs for issues during writing individual rows or file I/O errors.
                    - Critical logs for unexpected exceptions.
    """
    if not books:
        module_logger.info(f"No book data to save to {filename}.")
        return
    try:
        # Collect all unique fieldnames from all books
        fieldnames = set()
        for book in books:
            if book:
                fieldnames.update(book.keys())
        fieldnames = sorted(fieldnames)  # Sort for consistent column order

        module_logger.info(f"Saving scraped books to CSV: {filename}")
        print_log(f"Saving {len(books)} books to {filename}...", "info")

        with open(filename, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames, restval="")
            writer.writeheader()
            for book in books:
                try:
                    writer.writerow(book)
                except ValueError as ve:
                    module_logger.error(f"Error writing row for book {book.get('title', 'Unknown Title')} to CSV: {ve}")
                except Exception as e:
                    module_logger.error(
                        f"Unexpected error writing book {book.get('title', 'Unknown Title')} to CSV: {e}",
                        exc_info=True,
                    )
        print_log(f"Books successfully saved to {filename}.", "success")
        module_logger.info(f"Books successfully saved to {filename}.")
    except IOError as e:
        module_logger.error(f"I/O error saving books to CSV {filename}: {e}")
        print_log(f"Error saving books to {filename}: {e}", "error")
    except Exception as e:
        module_logger.critical(
            f"An unexpected error occurred while saving books to CSV {filename}: {e}",
            exc_info=True,
        )
        print_log(f"Critical Error saving books to {filename}. Check log file.", "error")


def save_failed_urls_to_csv(failed_urls: list[dict], filename="failed_urls.csv") -> None:
    """
    Saves a list of dictionaries with failed URLs and their errors to a CSV file.
    Each dictionary should contain 'url', 'site', and 'error'.

    Args:
                    failed_urls (list[dict]): A list of dictionaries, each containing the following keys:
                                    - 'url': The URL that failed to scrape.
                                    - 'site': The site from which the URL was scraped.
                                    - 'error': The error message associated with the failure.

    Returns:
                    None
    """
    if not failed_urls:
        module_logger.info(f"No failed URLs to save to {filename}.")
        return

    try:
        fieldnames = ["url", "site", "error"]  # Explicit fieldnames
        module_logger.info(f"Saving failed URLs to CSV: {filename}")
        print_log(f"Saving {len(failed_urls)} failed URLs to {filename}...", "info")

        with open(filename, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames, restval="")
            writer.writeheader()
            for item in failed_urls:
                try:
                    writer.writerow(item)
                except ValueError as ve:
                    module_logger.error(f"Error writing failed URL {item.get('url', 'Unknown URL')} to CSV: {ve}")
                except Exception as e:
                    module_logger.error(
                        f"Unexpected error writing failed URL {item.get('url', 'Unknown URL')} to CSV: {e}",
                        exc_info=True,
                    )
        print_log(f"Failed URLs successfully saved to {filename}.", "success")
        module_logger.info(f"Failed URLs successfully saved to {filename}.")
    except IOError as e:
        module_logger.error(f"I/O error saving failed URLs to CSV {filename}: {e}")
        print_log(f"Error saving failed URLs to {filename}: {e}", "error")
    except Exception as e:
        module_logger.critical(
            f"An unexpected error occurred while saving failed URLs to CSV {filename}: {e}",
            exc_info=True,
        )
        print_log(f"Critical Error saving failed URLs to {filename}. Check log file.", "error")


async def run(args: argparse.Namespace) -> None:
    start_time = time.time()
    module_logger.info("Application started.")

    destinations = resolve_output_destinations(args.output_to_csv, args.output_to_mongo)
    mongo_collection = destinations.mongo_collection

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
                        exists = await asyncio.to_thread(leanpub_prescrape_deduplicate, book_id, book_slug)

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
                                f"{site_name.title()} - Book already in database: {book_title}",
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
    print_log(f"{total_duplicates_skipped} books already in the database and skipped.")

    # --- Output Saving ---
    print_log("\n--- Output Summary ---", "step")
    if destinations.output_to_csv:
        save_books_to_csv(scraped_books_data)
        save_failed_urls_to_csv(failed_scrape_attempts)
    elif destinations.can_output_to_csv:
        print_log("CSV output was not chosen.", "info")
    else:
        print_log("CSV output not requested.", "info")

    if destinations.output_to_mongo:
        if scraped_books_data:
            print_log("Saving newly scraped books to MongoDB database...")
            save_books_to_mongodb(scraped_books_data, mongo_collection)
        else:
            print_log("No new books to save to MongoDB database.")
    else:
        print_log("MongoDB output not requested.", "info")

    if destinations.can_output_to_mongo:
        close_mongo_connection()

    print_log("\nApplication finished.", "step")
    module_logger.info("Application finished.")
