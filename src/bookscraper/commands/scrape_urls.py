from __future__ import annotations

import argparse
import asyncio
import csv
import os
import time
from collections import Counter
from pathlib import Path

import httpx
import pandas as pd
from playwright.async_api import async_playwright

from .. import exitcodes
from ..backends import resolve_store_backend
from ..book_utils import csv_safe_cell, logger, print_log
from ..runtime import runtime_options
from ..scraping.politeness import RobotsCache, bounded_gather, build_default_breakers, build_default_limiters
from ..scraping.results import FAILURE_STATUSES, ScrapeResult, ScrapeStatus
from ..scraping.scrape_details import ROBOTS_FETCH_TIMEOUT_S, build_robots_fetcher, scrape_book


def save_failed_urls_to_csv(
    failed_urls_with_status: list[tuple[str, str, str]],
    filename: str = "failed_books.csv",
    directory: str = ".",
):
    """Save the failed/duplicate URLs with their status and error reason to a CSV file.

    :param failed_urls_with_status: A list of ``(url, status, error)`` tuples, where ``status``
        is a :class:`ScrapeStatus` value and ``error`` is a short, secret-free reason (may be
        empty for a duplicate).
    :param filename: The fixed name of the CSV file to create (unaffected by ``--output-dir``).
    :param directory: The directory to write ``filename`` into (``--output-dir``; default ``.``).
    """
    if not failed_urls_with_status:
        logger.info(f"No failed/duplicate URLs to save to {filename}.")
        return

    target = os.path.join(directory, filename)
    try:
        with open(target, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["url", "status", "error"])  # Add status + error header
            for url, status, error in failed_urls_with_status:
                writer.writerow([csv_safe_cell(url), csv_safe_cell(status), csv_safe_cell(error)])
        print_log(
            f"Successfully saved {len(failed_urls_with_status)} failed/duplicate URLs to {target}",
            "success",
        )
        logger.info(f"Successfully saved {len(failed_urls_with_status)} failed/duplicate URLs to {target}")

    except (OSError, csv.Error) as e:
        logger.error(
            f"Error saving failed/duplicate URLs to CSV ({target}): {e}",
            exc_info=True,
        )
        print_log(
            f"Error: Could not write failed/duplicate URLs to CSV file {target}. Details: {e}",
            "error",
        )


def save_other_links_to_csv(other_links: list[str], filename: str = "other_links.csv", directory: str = "."):
    """Saves the 'other' links (skipped URLs) to a CSV file.

    :param other_links: The skipped URLs to record.
    :param filename: The fixed name of the CSV file to create (unaffected by ``--output-dir``).
    :param directory: The directory to write ``filename`` into (``--output-dir``; default ``.``).
    """
    if not other_links:
        logger.info(f"No 'other' links to save to {filename}.")
        return

    target = os.path.join(directory, filename)
    try:
        with open(target, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["url"])
            for url in other_links:
                writer.writerow([csv_safe_cell(url)])
        print_log(
            f"Successfully saved {len(other_links)} 'other' links to {target}",
            "success",
        )
        logger.info(f"Successfully saved {len(other_links)} 'other' links to {target}")

    except (OSError, csv.Error) as e:
        logger.error(f"Error saving 'other' links to CSV ({target}): {e}", exc_info=True)
        print_log(
            f"Error: Could not write 'other' links to CSV file {target}. Details: {e}",
            "error",
        )


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


def _report_dry_run_books(books: list[dict], store_backend: str):
    """Print the title, site and hash of each book a non-dry run would have saved.

    :param books: The successfully scraped, non-duplicate book documents.
    :param store_backend: The resolved ``--store-backend`` name, named in the header line.
    """
    if not books:
        print_log("No new unique books scraped to save.", "info")
        return
    print_log(
        f"Would save {len(books)} book(s) via '{store_backend}' storage backend (title | site | hash):",
        "info",
    )
    for book in books:
        title = book.get("title", "Unknown Title")
        site = book.get("site", "?")
        book_hash = book.get("hash", "?")
        print_log(f"  {title} | {site} | {book_hash}", "info")


async def run(args: argparse.Namespace) -> int:
    """
    The "I already have URLs" workflow: scrape a CSV of book URLs into the chosen store.

    Resolves the storage backend, buckets the input URLs by site, scrapes them through a
    bounded-concurrency sliding window (at most ``concurrency`` Playwright tasks in flight, with
    per-host pacing left to the rate limiter), and writes the run's diagnostic CSVs.

    :param args: Parsed ``scrape-urls`` arguments (input file, ``--store-backend``, etc.).
    :returns: An exit code from :mod:`bookscraper.exitcodes`: :data:`~bookscraper.exitcodes.FATAL`
        on an unreadable input CSV, :data:`~bookscraper.exitcodes.PARTIAL` when at least one URL
        failed to scrape or a save errored, otherwise :data:`~bookscraper.exitcodes.OK` (including
        the "no valid URLs" case - the run did what it should).
    """
    start_time = time.time()

    # Resolve the run-wide runtime knobs once (flag -> env -> parameters.py precedence) and thread
    # the resolved values down instead of importing the headless default / a module concurrency one.
    runtime = runtime_options(args)

    # Create the diagnostics output directory up front (default "." is a no-op) so both the
    # early "no valid URLs" exit and the end-of-run writes land in --output-dir.
    output_dir = runtime.output_dir
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # resolve_store_backend raises BackendUnavailableError on a failed pre-flight; it propagates to
    # main(), which maps it to exitcodes.FATAL. Nothing to close yet if it raises here.
    storage_backend = resolve_store_backend(args.store_backend, runtime.json_store)
    try:
        # One per-run, per-host rate limiter registry, threaded down to every scrape_book call so a
        # whole run (across every batch) is throttled to each host's configured rate.
        limiters = build_default_limiters()
        # One per-run, per-host circuit-breaker registry, threaded down the same way so repeated
        # bot-wall blocks on a host short-circuit the rest of that host's URLs for the run.
        breakers = build_default_breakers()

        # robots.txt compliance is on by default; --ignore-robots disables it with a one-time warning.
        if runtime.ignore_robots:
            print_log(
                "robots.txt checking is DISABLED via --ignore-robots; scraping without robots compliance.",
                "warning",
            )
            logger.warning("robots.txt checking disabled via --ignore-robots for this run.")

        # Store URLs to scrape within a dictionary
        website_urls = {
            "amazon": [],
            "packtpub": [],
            "leanpub": [],
            "oreilly": [],
            "other": [],
        }
        # This will store (url, status, error) tuples for failed or duplicate entries
        failed_urls_with_status = []
        # Per-status tally for the end-of-run summary (counts every ScrapeStatus seen).
        status_counts: Counter[ScrapeStatus] = Counter()

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
            return exitcodes.FATAL
        except pd.errors.ParserError:
            logger.critical(f"Error: Could not parse CSV at {filepath}")
            print_log(
                f"Critical Error: Could not parse the CSV file '{filepath}'. Please check its format.",
                "error",
            )
            return exitcodes.FATAL
        except KeyError:
            logger.critical(f"Error: CSV file '{filepath}' does not contain a 'url' column.")
            print_log(
                f"Critical Error: The CSV file '{filepath}' does not contain a 'url' column. Please ensure it has a 'url' header.",
                "error",
            )
            return exitcodes.FATAL

        all_urls_to_scrape = []

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
            save_other_links_to_csv(website_urls["other"], directory=output_dir)
            # Not a failure: the run correctly found nothing to scrape.
            return exitcodes.OK

        # Playwright Browser Setup and Scraping Loop
        print_log("Starting web scraping operation...", "info")
        # A single shared httpx client backs the per-run robots.txt cache (one robots fetch per host).
        # It stays open for the whole scraping phase and is closed when the block exits.
        async with httpx.AsyncClient(timeout=httpx.Timeout(ROBOTS_FETCH_TIMEOUT_S)) as robots_client:
            robots = None if runtime.ignore_robots else RobotsCache(build_robots_fetcher(robots_client), limiters)
            async with async_playwright() as p:
                print_log("Opening browser...", "info")
                browser = await p.chromium.launch(headless=runtime.headless)

                books = []  # Stores successfully scraped book details (non-duplicates)

                # A bounded-concurrency sliding window replaces the old fixed batches + inter-batch
                # sleep: at most `concurrency` scrapes run at once and a finished slot is reused
                # immediately, while per-host pacing is the rate limiter's job. scrape_book always
                # returns a typed ScrapeResult (see scraping/results.py), passing the storage_backend
                # for an immediate duplicate check.
                concurrency = runtime.concurrency
                # Print a progress line every ~10% of the run (at least every completion for a tiny
                # run, at most every 10 completions), plus a final line when the last task lands.
                progress_interval = max(1, min(10, total_urls_to_scrape // 10))
                progress = Counter()  # keys: "done" / "ok" / "dup" / "fail"

                def report_progress(result):
                    """Tally one completed scrape and emit a progress line at each interval."""
                    progress["done"] += 1
                    if not isinstance(result, ScrapeResult):
                        # bounded_gather surfaced a raw exception (not a typed ScrapeResult); count it
                        # as a failure rather than dereferencing result.status.
                        progress["fail"] += 1
                    else:
                        match result.status:
                            case ScrapeStatus.SUCCESS:
                                progress["ok"] += 1
                            case ScrapeStatus.DUPLICATE:
                                progress["dup"] += 1
                            case _:
                                progress["fail"] += 1
                    if progress["done"] % progress_interval == 0 or progress["done"] == total_urls_to_scrape:
                        print_log(
                            f"{progress['done']}/{total_urls_to_scrape} done "
                            f"(ok={progress['ok']} dup={progress['dup']} fail={progress['fail']})",
                            "info",
                        )

                scrapes = [
                    scrape_book(
                        url,
                        browser,
                        identify_website(url),
                        storage_backend,
                        limiters,
                        robots,
                        breakers,
                        nav_timeout_ms=runtime.nav_timeout_ms,
                    )
                    for url in all_urls_to_scrape
                ]
                results = await bounded_gather(scrapes, limit=concurrency, on_result=report_progress)

                for url, result in zip(all_urls_to_scrape, results, strict=True):
                    if not isinstance(result, ScrapeResult):
                        # bounded_gather surfaced an exception scrape_book did not convert into a typed
                        # ScrapeResult; record it as a raw failure instead of dereferencing .status.
                        status_counts[ScrapeStatus.FAILED] += 1
                        failed_urls_with_status.append((url, ScrapeStatus.FAILED.value, str(result)))
                        logger.error(f"Scraping raised for URL: {url}: {result}")
                        continue
                    status_counts[result.status] += 1
                    match result.status:
                        case ScrapeStatus.SUCCESS:
                            # book is guaranteed non-None on SUCCESS by ScrapeResult.__post_init__.
                            books.append(result.book)
                        case ScrapeStatus.DUPLICATE:
                            failed_urls_with_status.append((result.url, result.status.value, result.error or ""))
                            logger.info(f"Book from URL: {result.url} detected as duplicate and skipped from saving.")
                        case _:
                            # Every other status is a failed scrape; the enum value is the CSV
                            # status and the typed error distinguishes 404 / timeout / parse error.
                            failed_urls_with_status.append((result.url, result.status.value, result.error or ""))
                            logger.error(
                                f"Scraping failed for URL: {result.url} ({result.status.value}): {result.error}"
                            )

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

                if status_counts:
                    status_breakdown = ", ".join(
                        f"{status.value}={status_counts[status]}" for status in ScrapeStatus if status_counts[status]
                    )
                    print_log(f"Scrape status breakdown: {status_breakdown}.", "info")

                await browser.close()

        # Diagnostic reports (failed/duplicate URLs, skipped "other" links) are process
        # diagnostics, not database content, so they're always written as CSV best-effort,
        # independent of --store-backend.
        save_failed_urls_to_csv(failed_urls_with_status, directory=output_dir)
        save_other_links_to_csv(website_urls["other"], directory=output_dir)

        save_errors = 0
        if runtime.dry_run:
            # Dedup reads already ran inline (scrape_book consulted the backend); a dry run just
            # refuses to persist, so no save_books call is made and the store is left untouched.
            print_log("DRY RUN - nothing was saved.", "warning")
            _report_dry_run_books(books, args.store_backend)
        else:
            print_log(f"Saving scraped books via '{args.store_backend}' storage backend...", "info")
            if books:
                summary = await asyncio.to_thread(storage_backend.save_books, books)
                # summary.errors > 0 is the partial-failure signal the exit-code contract
                # (task 05.0, subtask 05) consumes.
                save_errors = summary.errors
                print_log(
                    f"Save summary: {summary.inserted} new, {summary.duplicates} duplicates, {summary.errors} errors.",
                    "warning" if summary.errors > 0 else "success",
                )
            else:
                print_log("No new unique books scraped to save.", "info")

        # PARTIAL when at least one URL ended in a failure status (FAILED/TIMEOUT/PARSE_ERROR/
        # BLOCKED) or a save errored; a DUPLICATE/NOT_FOUND/SKIPPED_ROBOTS outcome is not a failure.
        had_failure = save_errors > 0 or any(status_counts[status] for status in FAILURE_STATUSES)
        return exitcodes.PARTIAL if had_failure else exitcodes.OK
    finally:
        storage_backend.close()
