from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright

from ..backends import resolve_dedup_source
from ..book_utils import logger, print_log, save_json_report
from ..scraping.parameters import HEADLESS_BROWSER
from ..scraping.scrape_details import scrape_book
from .staging_output import print_step_one_summary, save_to_staging, warn_about_legacy_store


class InputFileError(Exception):
    """The --input-file is missing, not valid JSON, or not a list of documents with a 'url'."""


def load_input_urls(filepath: str) -> list[str]:
    """
    Reads the URLs to scrape from a JSON input file.

    The file is a JSON array of book documents in the staging-file schema (see
    docs/dev/staging-file-format.md), so a staging file or a `pull` export is itself valid
    input. Only each document's 'url' field is used; any other fields are ignored.

    Raises:
        InputFileError: the file can't be read, isn't valid JSON, isn't an array of
            objects, or any document lacks a non-empty string 'url'. The whole file is
            rejected rather than silently skipping bad entries.
    """
    try:
        with open(filepath, encoding="utf-8-sig") as fh:
            documents = json.load(fh)
    except FileNotFoundError as e:
        raise InputFileError(f"The input file '{filepath}' was not found. Please check the path.") from e
    except json.JSONDecodeError as e:
        raise InputFileError(f"Could not parse the JSON file '{filepath}': {e}. Please check its format.") from e
    except (OSError, UnicodeDecodeError) as e:
        raise InputFileError(f"Could not read the input file '{filepath}': {e}") from e

    if not isinstance(documents, list):
        raise InputFileError(
            f"The JSON file '{filepath}' must contain an array of book documents, got {type(documents).__name__}."
        )

    urls = []
    for index, document in enumerate(documents):
        url = document.get("url") if isinstance(document, dict) else None
        if not isinstance(url, str) or not url.strip():
            raise InputFileError(
                f"Entry #{index} in the JSON file '{filepath}' is not an object with a non-empty string 'url' field."
            )
        urls.append(url.strip())
    return urls


def save_failed_urls_to_json(
    failed_urls_with_status: list[tuple[str, str]], filename: str = "failed_books.json"
) -> None:
    """
    Saves the failed/duplicate URLs with their status as a JSON array of
    {"url": ..., "status": ...} objects.

    Args:
        failed_urls_with_status: A list of tuples, where each tuple is (url, status_string).
        filename: The JSON file to create.
    """
    records = [{"url": url, "status": status} for url, status in failed_urls_with_status]
    save_json_report(records, filename, "failed/duplicate URLs")


def save_other_links_to_json(other_links: list[str], filename: str = "other_links.json") -> None:
    """Saves the 'other' links (skipped URLs) as a JSON array of {"url": ...} objects."""
    save_json_report([{"url": url} for url in other_links], filename, "'other' links")


def identify_website(url: str) -> str:
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

    staging_file = Path(args.staging_file)
    warn_about_legacy_store(staging_file)
    dedup_source = resolve_dedup_source(staging_file, args.check_mongo)

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

    try:
        for url in load_input_urls(args.input_file):
            website_urls[identify_website(url)].append(url)
    except InputFileError as e:
        logger.critical(f"Error reading input file: {e}")
        print_log(f"Critical Error: {e}", "error")
        sys.exit(1)

    all_urls_to_scrape = []
    batch_size = 10

    for website, urls in website_urls.items():
        if website not in ["other"]:
            print_log(f"Found {len(urls)} {website.capitalize()} URLs to scrape.", "info")
            all_urls_to_scrape.extend(urls)
        else:
            print_log(
                f"Found {len(urls)} {website.capitalize()} URLs which will be skipped and added to other_links.json.",
                "info",
            )

    total_urls_to_scrape = len(all_urls_to_scrape)

    if total_urls_to_scrape == 0:
        print_log(
            "No valid book URLs found to scrape. Saving skipped links and exiting.",
            "info",
        )
        save_other_links_to_json(website_urls["other"])
        dedup_source.close()
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
                # Pass the read-only dedup source to scrape_book for an immediate duplicate check
                # scrape_book will return a dict on success, or (None, "DUPLICATE"), or (None, "FAILED")
                tasks.append(scrape_book(url, browser, site, dedup_source))

            results = await asyncio.gather(*tasks)

            for index, result in enumerate(results):
                if isinstance(result, dict):
                    # This is a successfully scraped book (a dictionary)
                    books.append(result)
                elif isinstance(result, tuple) and len(result) == 2 and result[0] is None:
                    # This is a status tuple: (None, "DUPLICATE") or (None, "FAILED")
                    original_url = batch_urls[index]
                    status_type = result[1]  # "DUPLICATE" or "FAILED"

                    # Add to failed_urls_with_status for the diagnostic report
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
    # diagnostics, not book data, so they're always written as JSON best-effort.
    save_failed_urls_to_json(failed_urls_with_status)
    save_other_links_to_json(website_urls["other"])

    # Step 1's only write: append to the staging file for review (ADR 0003). Never MongoDB.
    summary = save_to_staging(staging_file, books, source="scrape-urls")
    dedup_source.close()
    skipped_as_known = sum(1 for _, status in failed_urls_with_status if status == "DUPLICATE")
    print_step_one_summary(staging_file, summary, skipped_as_known=skipped_as_known)
