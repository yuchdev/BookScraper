import argparse
import asyncio
import csv
import random
import sys
import time

import pandas as pd
from playwright.async_api import async_playwright

from ..backends import resolve_store_backend
from ..book_utils import logger, print_log
from ..scraping.parameters import HEADLESS_BROWSER
from ..scraping.scrape_details import scrape_book


def save_failed_urls_to_csv(failed_urls_with_status, filename="failed_books.csv"):
    """
    Saves the failed/duplicate URLs with their status to a CSV file.

    Args:
        failed_urls_with_status: A list of tuples, where each tuple is (url, status_string).
        filename: The name of the CSV file to create.
    """
    if not failed_urls_with_status:
        logger.info(f"No failed/duplicate URLs to save to {filename}.")
        return

    try:
        with open(filename, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["url", "status"])  # Add status header
            for url, status in failed_urls_with_status:
                writer.writerow([url, status])
        print_log(
            f"Successfully saved {len(failed_urls_with_status)} failed/duplicate URLs to {filename}",
            "success",
        )
        logger.info(f"Successfully saved {len(failed_urls_with_status)} failed/duplicate URLs to {filename}")

    except Exception as e:
        logger.error(
            f"Error saving failed/duplicate URLs to CSV ({filename}): {e}",
            exc_info=True,
        )
        print_log(
            f"Error: Could not write failed/duplicate URLs to CSV file {filename}. Details: {e}",
            "error",
        )


def save_other_links_to_csv(other_links, filename="other_links.csv"):
    """Saves the 'other' links (skipped URLs) to a CSV file."""
    if not other_links:
        logger.info(f"No 'other' links to save to {filename}.")
        return

    try:
        with open(filename, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["url"])
            for url in other_links:
                writer.writerow([url])
        print_log(
            f"Successfully saved {len(other_links)} 'other' links to {filename}",
            "success",
        )
        logger.info(f"Successfully saved {len(other_links)} 'other' links to {filename}")

    except Exception as e:
        logger.error(f"Error saving 'other' links to CSV ({filename}): {e}", exc_info=True)
        print_log(
            f"Error: Could not write 'other' links to CSV file {filename}. Details: {e}",
            "error",
        )


def identify_website(url):
    """Identifies the website based on the URL."""
    if "amazon.com" in url:
        return "amazon"
    elif "packtpub.com" in url:
        return "packtpub"
    elif "leanpub.com" in url:
        return "leanpub"
    elif "oreilly.com" in url:
        return "oreilly"
    else:
        return "other"


async def run(args: argparse.Namespace) -> None:
    start_time = time.time()

    storage_backend = resolve_store_backend(args.store_backend)

    # Store URLs to scrape within a dictionary
    website_urls = {
        "amazon": [],
        "packtpub": [],
        "leanpub": [],
        "oreilly": [],
        "other": [],
    }
    # This will store (url, status) tuples for failed or duplicate entries
    failed_urls_with_status = []

    filepath = args.input_file
    try:
        df = pd.read_csv(filepath, encoding="utf-8")
        for url in df["url"]:
            site_identifier = identify_website(url)
            website_urls[site_identifier].append(url)

    except FileNotFoundError:
        logger.critical(f"Error: Input file not found at {filepath}")
        print_log(
            f"Critical Error: The input file '{filepath}' was not found. Please check the path.",
            "error",
        )
        sys.exit(1)
    except pd.errors.ParserError:
        logger.critical(f"Error: Could not parse CSV at {filepath}")
        print_log(
            f"Critical Error: Could not parse the CSV file '{filepath}'. Please check its format.",
            "error",
        )
        sys.exit(1)
    except KeyError:
        logger.critical(f"Error: CSV file '{filepath}' does not contain a 'url' column.")
        print_log(
            f"Critical Error: The CSV file '{filepath}' does not contain a 'url' column. Please ensure it has a 'url' header.",
            "error",
        )
        sys.exit(1)

    all_urls_to_scrape = []
    batch_size = 10

    for website, urls in website_urls.items():
        if website not in ["other"]:
            print_log(f"Found {len(urls)} {website.capitalize()} URLs to scrape.", "info")
            all_urls_to_scrape.extend(urls)
        else:
            print_log(
                f"Found {len(urls)} {website.capitalize()} URLs which will be skipped and added to other_links.csv.",
                "info",
            )

    total_urls_to_scrape = len(all_urls_to_scrape)

    if total_urls_to_scrape == 0:
        print_log(
            "No valid book URLs found to scrape. Saving skipped links and exiting.",
            "info",
        )
        save_other_links_to_csv(website_urls["other"])
        sys.exit(0)

    # Playwright Browser Setup and Scraping Loop
    print_log("Starting web scraping operation...", "info")
    async with async_playwright() as p:
        print_log("Opening browser...", "info")
        browser = await p.chromium.launch(headless=HEADLESS_BROWSER)

        books = []  # Stores successfully scraped book details (non-duplicates)

        for i in range(0, total_urls_to_scrape, batch_size):
            batch_urls = all_urls_to_scrape[i : i + batch_size]
            current_batch_num = i // batch_size + 1
            total_batches = (total_urls_to_scrape + batch_size - 1) // batch_size  # Ceiling division
            print_log(f"Starting batch {current_batch_num} of {total_batches}", "info")

            tasks = []
            for url in batch_urls:
                site = identify_website(url)
                # Pass the storage_backend to scrape_book for immediate duplicate check
                # scrape_book will return a dict on success, or (None, "DUPLICATE"), or (None, "FAILED")
                tasks.append(scrape_book(url, browser, site, storage_backend))

            results = await asyncio.gather(*tasks)

            for index, result in enumerate(results):
                if isinstance(result, dict):
                    # This is a successfully scraped book (a dictionary)
                    books.append(result)
                elif isinstance(result, tuple) and len(result) == 2 and result[0] is None:
                    # This is a status tuple: (None, "DUPLICATE") or (None, "FAILED")
                    original_url = batch_urls[index]
                    status_type = result[1]  # "DUPLICATE" or "FAILED"

                    # Add to failed_urls_with_status for diagnostic CSV
                    failed_urls_with_status.append((original_url, status_type))

                    if status_type == "DUPLICATE":
                        logger.info(f"Book from URL: {original_url} detected as duplicate and skipped from saving.")
                    elif status_type == "FAILED":
                        logger.error(f"Scraping failed for URL: {original_url}")
                else:
                    # Catch-all for unexpected return types from scrape_book
                    original_url = batch_urls[index]
                    logger.error(f"Unexpected return type from scrape_book for URL: {original_url}. Result: {result}")
                    failed_urls_with_status.append(
                        (original_url, "UNKNOWN_ERROR")
                    )  # Add to failed_urls for diagnostics

            if current_batch_num < total_batches:  # Only pause if there are more batches
                print_log("Pausing between batches...", "info")
                await asyncio.sleep(random.uniform(3, 5))

        total_scraped_books = len(books)  # Only counts successfully scraped books
        total_time_taken = time.time() - start_time
        print_log("Shutting down browser...", "info")
        print_log(
            f"{total_scraped_books} unique books scraped from {total_urls_to_scrape} URLs processed in {total_time_taken:.2f} seconds.",
            "info",
        )
        if total_scraped_books > 0:
            print_log(
                f"Average time per unique book: {total_time_taken / total_scraped_books:.2f}s",
                "info",
            )
        else:
            print_log("No unique books were scraped successfully.", "warning")

        await browser.close()

    # Diagnostic reports (failed/duplicate URLs, skipped "other" links) are process
    # diagnostics, not database content, so they're always written as CSV best-effort,
    # independent of --store-backend.
    save_failed_urls_to_csv(failed_urls_with_status)
    save_other_links_to_csv(website_urls["other"])

    print_log(f"Saving scraped books via '{args.store_backend}' storage backend...", "info")
    if books:
        await asyncio.to_thread(storage_backend.save_books, books)
    else:
        print_log("No new unique books scraped to save.", "info")

    storage_backend.close()
