from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import os
import time
from collections import Counter
from pathlib import Path
from typing import NoReturn

import httpx

from .. import exitcodes
from ..backends import resolve_store_backend
from ..book_utils import csv_safe_cell, print_log
from ..runtime import runtime_options
from ..scraping.parameters import SEARCH_QUERIES, SITES_TO_SCRAPE
from ..scraping.politeness import RobotsCache, bounded_gather, build_default_limiters
from ..scraping.results import ScrapeResult, ScrapeStatus
from ..scraping.scrape_details import build_robots_fetcher, get_leanpub_book_details
from ..scraping.search_providers import PROVIDERS, SearchContext

module_logger = logging.getLogger(__name__)


def _target_error(message: str) -> NoReturn:
    """
    Report an invalid ``search`` targeting selection and exit with argparse's usage status (2).

    Mirrors ``argparse.ArgumentParser.error``'s exit code so a bad ``--site`` / ``--query`` /
    ``--queries-file`` combination fails as a usage error rather than a mid-run crash.

    :param message: The human-facing reason, surfaced through :func:`print_log`.
    :raises SystemExit: Always, with code ``2``.
    """
    print_log(f"search: {message}", "error")
    module_logger.error(f"search targeting error: {message}")
    raise SystemExit(2)


def _read_queries_file(path: str) -> list[str]:
    """
    Read search queries from ``path``, one per line, skipping blanks and ``#`` comment lines.

    :param path: The ``--queries-file`` path to read.
    :returns: The queries in file order, stripped, with blank and comment lines removed.
    :raises SystemExit: With code ``2`` if the file cannot be read.
    """
    try:
        raw_lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        _target_error(f"cannot read --queries-file {path!r}: {exc.strerror or exc}")
    queries = []
    for raw in raw_lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        queries.append(line)
    return queries


def _resolve_queries(args: argparse.Namespace) -> list[str]:
    """
    Merge ``--query`` and ``--queries-file`` into one de-duplicated, order-preserving query list.

    ``--query`` values come first (in the order given), then ``--queries-file`` values (in file
    order); duplicates after the first occurrence are dropped. When neither flag supplies a query,
    the ``SEARCH_QUERIES`` default from ``parameters.py`` is used.

    :param args: Parsed ``search`` arguments (reads ``queries`` and ``queries_file``).
    :returns: The resolved, de-duplicated query list.
    """
    cli_queries_arg = getattr(args, "queries", None)
    queries_file = getattr(args, "queries_file", None)
    # Fall back to the parameters.py default only when NEITHER flag was supplied. When a flag is
    # given but yields nothing (e.g. a comments-only --queries-file), the resolved list stays empty
    # so the caller raises a loud error rather than silently searching the defaults.
    if cli_queries_arg is None and queries_file is None:
        return list(SEARCH_QUERIES)
    cli_queries = list(cli_queries_arg or [])
    file_queries = _read_queries_file(queries_file) if queries_file else []
    merged = []
    seen = set()
    for query in [*cli_queries, *file_queries]:
        if query not in seen:
            seen.add(query)
            merged.append(query)
    return merged


def resolve_search_targets(args: argparse.Namespace) -> tuple[list[str], list[str], int]:
    """
    Resolve the sites, queries and page cap a ``search`` run should use, in exactly one place.

    Flags beat the ``parameters.py`` defaults: ``--site`` falls back to ``SITES_TO_SCRAPE`` and the
    query flags fall back to ``SEARCH_QUERIES`` only when unset. An empty resolved site or query
    list, or a site with no wired provider in
    :data:`~bookscraper.scraping.search_providers.PROVIDERS` (the single source of truth for which
    sites are searchable), is a loud usage error (exit ``2``) rather than a silent no-op.

    :param args: Parsed ``search`` arguments (reads ``sites``, ``queries``, ``queries_file`` and
        ``max_search_pages``).
    :returns: A ``(sites, queries, max_search_pages)`` tuple ready for the run loop.
    :raises SystemExit: With code ``2`` if the resolved sites or queries are empty or a chosen site
        has no wired search provider.
    """
    sites = list(getattr(args, "sites", None) or SITES_TO_SCRAPE)
    queries = _resolve_queries(args)
    max_search_pages = getattr(args, "max_search_pages", None) or 3

    if not sites:
        _target_error("no sites to search; pass --site or configure SITES_TO_SCRAPE")
    unsupported = [site for site in sites if site not in PROVIDERS]
    if unsupported:
        supported = ", ".join(sorted(PROVIDERS))
        _target_error(f"no search provider wired for: {', '.join(unsupported)}; supported sites: {supported}")
    if not queries:
        _target_error("no search queries; pass --query/--queries-file or configure SEARCH_QUERIES")
    return sites, queries, max_search_pages


def save_failed_urls_to_csv(failed_urls: list[dict], filename: str = "failed_urls.csv", directory: str = "."):
    """
    Save the failed scrape records to a CSV file.

    Every cell goes through :func:`~bookscraper.book_utils.csv_safe_cell` so page-derived text can't
    be evaluated as a spreadsheet formula.

    :param failed_urls: Failure records with the keys ``url``, ``site``, ``title`` and ``error``.
    :param filename: The fixed name of the CSV file to write (unaffected by ``--output-dir``).
    :param directory: The directory to write ``filename`` into (``--output-dir``; default ``.``).
    """
    if not failed_urls:
        module_logger.info(f"No failed URLs to save to {filename}.")
        return

    target = os.path.join(directory, filename)
    try:
        fieldnames = ["url", "site", "title", "error"]  # Must cover every key a failure record carries
        module_logger.info(f"Saving failed URLs to CSV: {target}")
        print_log(f"Saving {len(failed_urls)} failed URLs to {target}...", "info")

        with open(target, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames, restval="")
            writer.writeheader()
            for item in failed_urls:
                try:
                    writer.writerow({key: csv_safe_cell(value) for key, value in item.items()})
                except ValueError as ve:
                    module_logger.error(f"Error writing failed URL {item.get('url', 'Unknown URL')} to CSV: {ve}")
                except csv.Error as e:
                    module_logger.error(
                        f"Unexpected error writing failed URL {item.get('url', 'Unknown URL')} to CSV: {e}",
                        exc_info=True,
                    )
        print_log(f"Failed URLs successfully saved to {target}.", "success")
        module_logger.info(f"Failed URLs successfully saved to {target}.")
    except IOError as e:
        module_logger.error(f"I/O error saving failed URLs to CSV {target}: {e}")
        print_log(f"Error saving failed URLs to {target}: {e}", "error")
    except csv.Error as e:
        module_logger.critical(
            f"An unexpected error occurred while saving failed URLs to CSV {target}: {e}",
            exc_info=True,
        )
        print_log(f"Critical Error saving failed URLs to {target}. Check log file.", "error")


def _report_dry_run_books(books: list[dict], store_backend: str):
    """Print the title, site and hash of each book a non-dry run would have saved.

    :param books: The successfully scraped book documents.
    :param store_backend: The resolved ``--store-backend`` name, named in the header line.
    """
    if not books:
        print_log("No new books to save.")
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
    The "discover new books" workflow: search each configured site, then scrape survivors.

    For every site in ``SITES_TO_SCRAPE`` and query in ``SEARCH_QUERIES`` it collects
    candidates, deduplicates them against the resolved storage backend before the
    expensive detail scrape, scrapes details for the survivors, and saves them.

    :param args: Parsed ``search`` arguments (``--store-backend``, ``--max-search-pages``, etc.).
    :returns: An exit code from :mod:`bookscraper.exitcodes`: :data:`~bookscraper.exitcodes.FATAL`
        if the orchestration hit an unhandled error, :data:`~bookscraper.exitcodes.PARTIAL` when at
        least one detail scrape failed or a save errored, otherwise
        :data:`~bookscraper.exitcodes.OK`. A duplicate skipped before scraping is not a failure.
    """
    start_time = time.time()
    module_logger.info("Application started.")

    # Resolve the run-wide runtime knobs once (concurrency / ignore-robots apply to the search
    # path; headless / nav-timeout are no-ops for today's httpx-only Leanpub provider).
    runtime = runtime_options(args)

    # Resolve sites/queries/page cap before anything is opened, so a bad targeting selection
    # (exit 2) fails fast without spinning up a storage backend or HTTP client.
    sites, queries, max_search_pages = resolve_search_targets(args)

    # Create the diagnostics output directory up front (default "." is a no-op).
    output_dir = runtime.output_dir
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # resolve_store_backend raises BackendUnavailableError on a failed pre-flight; it propagates to
    # main(), which maps it to exitcodes.FATAL. Nothing to close yet if it raises here.
    storage_backend = resolve_store_backend(args.store_backend, runtime.json_store)
    # One per-run, per-host rate limiter registry shared by the search and detail fetches so the
    # whole run is throttled to each host's configured rate.
    limiters = build_default_limiters()

    # robots.txt compliance is on by default; --ignore-robots disables it with a one-time warning.
    if runtime.ignore_robots:
        print_log(
            "robots.txt checking is DISABLED via --ignore-robots; scraping without robots compliance.",
            "warning",
        )
        module_logger.warning("robots.txt checking disabled via --ignore-robots for this run.")

    # --- Main Scraping Logic Starts Here ---
    scraped_books_data = []  # List to store successfully scraped, detailed book dictionaries to save
    failed_scrape_attempts = []  # List to store book data that failed detailed scraping
    total_duplicates_skipped = 0  # To sum up duplicates across all sites
    survivors = []  # Non-duplicate SearchCandidates that survived the pre-scrape dedup, across all sites
    # Per-status tally for the end-of-run summary (counts every ScrapeStatus seen).
    status_counts: Counter[ScrapeStatus] = Counter()
    # Set by the broad isolation boundary below; an aborted orchestration returns FATAL.
    had_unhandled_error = False
    save_errors = 0

    try:
        try:
            # One AsyncClient is created per run and reused for BOTH the robots.txt checks and every
            # Leanpub detail fetch (robots overrides to a 10 s per-request timeout), so hundreds of
            # concurrent calls share one bounded connection pool instead of each opening its own.
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(30.0), limits=httpx.Limits(max_connections=10)
            ) as http_client:
                robots = None if runtime.ignore_robots else RobotsCache(build_robots_fetcher(http_client), limiters)

                # The per-run SearchContext bundles the shared httpx client, limiters, robots cache,
                # and a lazily launched browser (started only if a selected provider needs one - the
                # API-only Leanpub provider never triggers a launch). Closed on block exit.
                async with SearchContext(
                    http_client=http_client, limiters=limiters, robots=robots, headless=runtime.headless
                ) as search_context:
                    # --- Iterate the resolved sites via the provider registry (no per-site branches) ---
                    for site_name in sites:
                        provider = PROVIDERS[site_name]
                        print_log(f"\n--- Processing {provider.site.title()} ---", "step")

                        for search_item in queries:
                            candidates = await provider.search(search_item, max_search_pages, search_context)

                            # Pre-scrape deduplication: check each candidate against the backend (a
                            # blocking read, so it runs off the event loop thread) and keep the new ones.
                            for candidate in candidates:
                                exists = await asyncio.to_thread(provider.already_stored, candidate, storage_backend)
                                if not exists:
                                    survivors.append(candidate)
                                    print_log(
                                        f"{provider.site.title()} - New book found: {candidate.title}",
                                        "success",
                                    )
                                else:
                                    total_duplicates_skipped += 1
                                    print_log(
                                        f"{provider.site.title()} - Book already in database: {candidate.title}",
                                        "warning",
                                    )

                            print_log(
                                f"Found {len(candidates)} potential new books for '{search_item}'.",
                                "success",
                            )
                            module_logger.info(f"Found {len(candidates)} potential new books for '{search_item}'.")

                # --- Deduplicate Cross-Site using ISBN / Fuzzy search / Hash ---
                # (placeholder for task 03.3 subtask 05; today survivors pass through unchanged.)

                # --- Queue Detailed Scraping Tasks ---
                # Detail dispatch stays Leanpub-only until subtask 06 wires generic per-site
                # dispatch; today every registered provider is API-based, so every survivor is
                # fetched through the Leanpub JSON:API detail helper.
                if survivors:
                    print_log(f"--- Scraping {len(survivors)} book(s) ---", "step")
                    # Detail scraping runs through a bounded-concurrency sliding window instead of an
                    # unbounded gather: at most `concurrency` fetches are in flight at once and a
                    # finished slot is reused immediately, with per-host pacing left to the rate
                    # limiter. Exceptions are still captured per item like gather(return_exceptions).
                    concurrency = runtime.concurrency
                    total_detail = len(survivors)
                    # Progress line every ~10% (at least each completion for a tiny run, at most every
                    # 10), plus a final line. Detail scrapes have no duplicate outcome, so dup is 0.
                    progress_interval = max(1, min(10, total_detail // 10))
                    progress = Counter()  # keys: "done" / "ok" / "fail"

                    def report_progress(result):
                        """Tally one completed detail scrape and emit a progress line at each interval."""
                        progress["done"] += 1
                        if isinstance(result, ScrapeResult) and result.status is ScrapeStatus.SUCCESS:
                            progress["ok"] += 1
                        else:
                            progress["fail"] += 1
                        if progress["done"] % progress_interval == 0 or progress["done"] == total_detail:
                            print_log(
                                f"{progress['done']}/{total_detail} done "
                                f"(ok={progress['ok']} dup=0 fail={progress['fail']})",
                                "info",
                            )

                    site_scrape_tasks = [
                        get_leanpub_book_details(
                            url=candidate.detail_ref, client=http_client, limiters=limiters, robots=robots
                        )
                        for candidate in survivors
                    ]
                    current_site_scraped_results = await bounded_gather(
                        site_scrape_tasks, limit=concurrency, on_result=report_progress
                    )

                    # Process results from detailed scraping
                    for candidate, result in zip(survivors, current_site_scraped_results, strict=True):
                        if isinstance(result, ScrapeResult):
                            status_counts[result.status] += 1
                            if result.status is ScrapeStatus.SUCCESS:
                                scraped_books_data.append(result.book)
                                print_log(
                                    f"Successfully scraped details for: "
                                    f"{result.book.get('title', 'Unknown Title')} from {candidate.site}",
                                    "info",
                                )
                            else:
                                # result.url is the real detail URL (fixes the old "url": "N/A"), and
                                # result.error is the typed, secret-free failure reason.
                                failed_scrape_attempts.append(
                                    {
                                        "url": result.url,
                                        "site": candidate.site,
                                        "title": candidate.title,
                                        "error": result.error or result.status.value,
                                    }
                                )
                                print_log(
                                    f"Failed to scrape details for {result.url} from {candidate.site}: "
                                    f"{result.status.value} - {result.error}",
                                    "error",
                                )
                        else:
                            # gather(return_exceptions=True) surfaced an exception the detail scraper
                            # did not convert into a ScrapeResult; record it as a raw failure.
                            failed_scrape_attempts.append(
                                {
                                    "url": candidate.detail_ref,
                                    "site": candidate.site,
                                    "title": candidate.title,
                                    "error": str(result),
                                }
                            )
                            print_log(
                                f"Failed to scrape details for {candidate.detail_ref} from {candidate.site}: "
                                f"{str(result)}",
                                "error",
                            )
                else:
                    print_log("No new books identified for detailed scraping.", "info")

        # KeyboardInterrupt is intentionally NOT caught here: it propagates past this isolation
        # boundary (and the finally that closes the backend) up to main_sync, which prints one line
        # and exits 130. The broad Exception catch below is for an orchestration failure only.
        except Exception as e:  # noqa: X002 - top-level isolation boundary: an unhandled orchestration failure is logged; the run still writes diagnostics and closes the backend; returns FATAL
            module_logger.critical(f"An unhandled error occurred in main execution: {e}", exc_info=True)
            print_log(f"Critical error in main execution. Check logs: {e}", "error")
            had_unhandled_error = True

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
        if status_counts:
            status_breakdown = ", ".join(
                f"{status.value}={status_counts[status]}" for status in ScrapeStatus if status_counts[status]
            )
            print_log(f"Scrape status breakdown: {status_breakdown}.")

        # --- Output Saving ---
        print_log("\n--- Output Summary ---", "step")
        # failed_urls.csv is a process diagnostic, not database content, so it's always
        # written as CSV best-effort, independent of --store-backend.
        save_failed_urls_to_csv(failed_scrape_attempts, directory=output_dir)

        if runtime.dry_run:
            # Pre-scrape dedup reads already ran (leanpub_book_exists above); a dry run just refuses to
            # persist, so no save_books call is made and the store is left untouched.
            print_log("DRY RUN - nothing was saved.", "warning")
            _report_dry_run_books(scraped_books_data, args.store_backend)
        elif scraped_books_data:
            print_log(f"Saving newly scraped books via '{args.store_backend}' storage backend...")
            # save_books blocks (a MongoDB insert_many or the JSON store's fsync + replace), so it runs
            # in a worker thread like the other storage calls instead of stalling the event loop.
            summary = await asyncio.to_thread(storage_backend.save_books, scraped_books_data)
            # summary.errors > 0 is the partial-failure signal the exit-code contract
            # (task 05.0, subtask 05) consumes.
            save_errors = summary.errors
            print_log(
                f"Save summary: {summary.inserted} new, {summary.duplicates} duplicates, {summary.errors} errors.",
                "warning" if summary.errors > 0 else "success",
            )
        else:
            print_log("No new books to save.")

        print_log("\nApplication finished.", "step")
        module_logger.info("Application finished.")

        # FATAL if the orchestration aborted; else PARTIAL if any detail scrape failed or a save
        # errored; else OK. A pre-scrape duplicate is not a failure.
        if had_unhandled_error:
            return exitcodes.FATAL
        if failed_scrape_attempts or save_errors > 0:
            return exitcodes.PARTIAL
        return exitcodes.OK
    finally:
        storage_backend.close()
