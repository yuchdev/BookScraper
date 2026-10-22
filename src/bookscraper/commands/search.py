from __future__ import annotations

import argparse
import asyncio
import logging
import time
from collections import Counter
from pathlib import Path
from typing import NoReturn

import httpx

from .. import exitcodes
from ..backends import resolve_store_backend
from ..book_utils import print_log
from ..runtime import runtime_options
from ..scraping.dedup import group_candidates
from ..scraping.filters import passes_detail_filters, passes_search_filters
from ..scraping.parameters import SCRAPE_FILTERS, SEARCH_QUERIES, SITES_TO_SCRAPE
from ..scraping.politeness import RobotsCache, bounded_gather, build_default_limiters
from ..scraping.results import FAILURE_STATUSES, ScrapeResult, ScrapeStatus
from ..scraping.scrape_details import build_robots_fetcher
from ..scraping.search_providers import PROVIDERS, SearchContext, scrape_candidate
from ._reporting import elapsed_since, format_status_breakdown, write_failed_csv

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


#: CSV column order for ``failed_urls.csv`` (the ``search`` failed-detail-scrape diagnostic). Covers
#: every key a failure record carries, so the shared ``write_failed_csv`` writer drops no row.
FAILED_URLS_FIELDNAMES = ["url", "site", "title", "error"]


#: CSV column order for ``filtered_books.csv`` (the ``search`` SCRAPE_FILTERS-drop diagnostic). A
#: filtered book is never saved to the store; this is a process diagnostic like ``failed_urls.csv``.
FILTERED_BOOKS_FIELDNAMES = ["title", "site", "url", "stage", "reason"]


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
    # Every non-SUCCESS detail result (failures AND duplicate/not-found/robots-skipped outcomes) is
    # recorded here for the failed_urls.csv diagnostic, exactly as scrape-urls records all non-SUCCESS
    # in failed_books.csv. The PARTIAL exit code is driven by status_counts/FAILURE_STATUSES below, not
    # by this list, so a non-failure outcome never flips the exit code.
    failed_urls = []
    total_duplicates_skipped = 0  # To sum up duplicates across all sites
    cross_site_dropped = 0  # Candidates dropped by the within-run cross-site title dedup (subtask 05)
    isbn13_duplicates = 0  # Scraped records dropped post-detail because their ISBN-13 is already stored
    survivors = []  # Non-duplicate SearchCandidates that survived the pre-scrape dedup, across all sites
    filtered_books = []  # Records dropped by SCRAPE_FILTERS (search or detail stage); never saved
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
                    http_client=http_client,
                    limiters=limiters,
                    robots=robots,
                    headless=runtime.headless,
                    backend=storage_backend,
                ) as search_context:
                    # --- Iterate the resolved sites via the provider registry (no per-site branches) ---
                    for site_name in sites:
                        provider = PROVIDERS[site_name]
                        print_log(f"\n--- Processing {provider.site.title()} ---", "step")

                        for search_item in queries:
                            candidates = await provider.search(search_item, max_search_pages, search_context)
                            survivors_before = len(survivors)

                            # Pre-scrape deduplication: check each candidate against the backend (a
                            # blocking read, so it runs off the event loop thread) and keep the new ones.
                            for candidate in candidates:
                                exists = await asyncio.to_thread(provider.already_stored, candidate, storage_backend)
                                if exists:
                                    total_duplicates_skipped += 1
                                    print_log(
                                        f"{provider.site.title()} - Book already in database: {candidate.title}",
                                        "warning",
                                    )
                                    continue
                                # Search-stage SCRAPE_FILTERS: drop a low/un-rated candidate BEFORE the
                                # expensive detail scrape (bypassed entirely by --no-filters).
                                keep, reason = (
                                    (True, None)
                                    if runtime.no_filters
                                    else passes_search_filters(candidate, SCRAPE_FILTERS)
                                )
                                if not keep:
                                    filtered_books.append(
                                        {
                                            "title": candidate.title,
                                            "site": candidate.site,
                                            "url": candidate.url,
                                            "stage": "search",
                                            "reason": reason,
                                        }
                                    )
                                    print_log(
                                        f"{provider.site.title()} - Filtered out ({reason}): {candidate.title}",
                                        "warning",
                                    )
                                    continue
                                survivors.append(candidate)
                                print_log(
                                    f"{provider.site.title()} - New book found: {candidate.title}",
                                    "success",
                                )

                            # Report the post-dedup/post-filter survivor count for this query (what will
                            # actually proceed to the expensive detail scrape), not the raw candidate
                            # count, which overstates it. The within-run cross-site dedup below can still
                            # drop a few more before scraping.
                            new_survivors = len(survivors) - survivors_before
                            survivor_message = (
                                f"{new_survivors} new book(s) to scrape for '{search_item}' "
                                f"(from {len(candidates)} candidate(s) searched)."
                            )
                            print_log(survivor_message, "success")
                            module_logger.info(survivor_message)

                    # --- Within-run cross-site title deduplication (task 03.3 subtask 05) ---
                    # The same book often surfaces on several sites with differently decorated titles
                    # (subtitles, edition markers) that the canonical hash cannot catch. Group the
                    # post-filter survivors by normalized (title | first author) and keep one per key,
                    # preferring a publisher site over a reseller (DEDUP_SITE_PRIORITY), so the same book
                    # is scraped in detail at most once. Each dropped candidate is logged at info level
                    # naming the kept one's URL. No fuzzy matching: a false merge loses a book forever.
                    if survivors:
                        survivors, dropped_pairs = group_candidates(survivors)
                        cross_site_dropped = len(dropped_pairs)
                        for dropped_candidate, kept_candidate in dropped_pairs:
                            module_logger.info(
                                f"Cross-site duplicate dropped: '{dropped_candidate.title}' "
                                f"({dropped_candidate.site}); keeping {kept_candidate.site} "
                                f"candidate at {kept_candidate.url}"
                            )
                            print_log(
                                f"Cross-site duplicate: dropped '{dropped_candidate.title}' "
                                f"({dropped_candidate.site}) in favor of {kept_candidate.url}",
                                "info",
                            )

                    # --- Queue Detailed Scraping Tasks ---
                    # Every surviving candidate - whatever its site - is dispatched through the one
                    # generic scrape_candidate() router (task 03.3 subtask 06): Leanpub via the run's
                    # shared httpx JSON:API client, every other site via the context's lazily launched
                    # browser. This loop carries no per-site branch - the routing lives in the router.
                    if survivors:
                        print_log(f"--- Scraping {len(survivors)} book(s) ---", "step")
                        # Detail scraping runs through a bounded-concurrency sliding window instead of an
                        # unbounded gather: at most `concurrency` fetches are in flight at once and a
                        # finished slot is reused immediately, with per-host pacing left to the rate
                        # limiter. Exceptions are still captured per item like gather(return_exceptions).
                        concurrency = runtime.concurrency
                        total_detail = len(survivors)
                        # Progress line every ~10% (at least each completion for a tiny run, at most every
                        # 10), plus a final line. A detail scrape can still report a DUPLICATE (inline
                        # backend check), so dup has its own bucket like scrape-urls rather than folding
                        # into fail.
                        progress_interval = max(1, min(10, total_detail // 10))
                        progress = Counter()  # keys: "done" / "ok" / "dup" / "fail"

                        def report_progress(result):
                            """Tally one completed detail scrape and emit a progress line at each interval."""
                            progress["done"] += 1
                            if not isinstance(result, ScrapeResult):
                                # bounded_gather surfaced a raw exception (not a typed ScrapeResult); count
                                # it as a failure rather than dereferencing result.status.
                                progress["fail"] += 1
                            else:
                                match result.status:
                                    case ScrapeStatus.SUCCESS:
                                        progress["ok"] += 1
                                    case ScrapeStatus.DUPLICATE:
                                        progress["dup"] += 1
                                    case _:
                                        progress["fail"] += 1
                            if progress["done"] % progress_interval == 0 or progress["done"] == total_detail:
                                print_log(
                                    f"{progress['done']}/{total_detail} done "
                                    f"(ok={progress['ok']} dup={progress['dup']} fail={progress['fail']})",
                                    "info",
                                )

                        detail_tasks = [scrape_candidate(candidate, search_context) for candidate in survivors]
                        current_site_scraped_results = await bounded_gather(
                            detail_tasks, limit=concurrency, on_result=report_progress
                        )

                        # Process results from detailed scraping
                        for candidate, result in zip(survivors, current_site_scraped_results, strict=True):
                            if isinstance(result, ScrapeResult):
                                status_counts[result.status] += 1
                                if result.status is ScrapeStatus.SUCCESS:
                                    # Detail-stage SCRAPE_FILTERS: a successfully scraped book can still be
                                    # dropped on its ISBN count before it reaches save_books (bypassed by
                                    # --no-filters). A filtered book is a diagnostic, never a failure.
                                    keep, reason = (
                                        (True, None)
                                        if runtime.no_filters
                                        else passes_detail_filters(result.book, SCRAPE_FILTERS)
                                    )
                                    if not keep:
                                        filtered_books.append(
                                            {
                                                "title": result.book.get("title") or candidate.title,
                                                "site": candidate.site,
                                                "url": result.url,
                                                "stage": "detail",
                                                "reason": reason,
                                            }
                                        )
                                        print_log(
                                            f"Filtered out ({reason}): "
                                            f"{result.book.get('title', 'Unknown Title')} from {candidate.site}",
                                            "warning",
                                        )
                                        continue
                                    # Post-detail cross-site dedup (task 03.3 subtask 05): the ISBN-13 is
                                    # the only reliable cross-site identity, so a scraped record whose
                                    # canonical isbn13 is already in the store is a DUPLICATE and is not
                                    # saved. A duplicate is not a failure (the exit-code contract excludes
                                    # it), so it only bumps the duplicate tallies. The blocking store read
                                    # runs off the event loop thread like the pre-scrape checks.
                                    isbn13 = result.book.get("isbn13")
                                    if isbn13 and await asyncio.to_thread(
                                        storage_backend.book_exists_by_isbn13, isbn13
                                    ):
                                        isbn13_duplicates += 1
                                        total_duplicates_skipped += 1
                                        print_log(
                                            f"{candidate.site.title()} - Book already in database by ISBN-13: "
                                            f"{result.book.get('title', 'Unknown Title')}",
                                            "warning",
                                        )
                                        continue
                                    scraped_books_data.append(result.book)
                                    print_log(
                                        f"Successfully scraped details for: "
                                        f"{result.book.get('title', 'Unknown Title')} from {candidate.site}",
                                        "info",
                                    )
                                else:
                                    # Every non-SUCCESS result is recorded to failed_urls.csv (a
                                    # diagnostic), but only a FAILURE_STATUSES outcome drives the PARTIAL
                                    # exit code via status_counts below - a DUPLICATE / NOT_FOUND /
                                    # SKIPPED_ROBOTS outcome means the run did what it should, mirroring
                                    # scrape-urls. result.url is the real detail URL (fixes the old
                                    # "url": "N/A") and result.error is the typed secret-free reason.
                                    failed_urls.append(
                                        {
                                            "url": result.url,
                                            "site": candidate.site,
                                            "title": candidate.title,
                                            "error": result.error or result.status.value,
                                        }
                                    )
                                    print_log(
                                        f"Could not scrape details for {result.url} from {candidate.site}: "
                                        f"{result.status.value} - {result.error}",
                                        "error" if result.status in FAILURE_STATUSES else "warning",
                                    )
                            else:
                                # gather(return_exceptions=True) surfaced an exception the detail scraper
                                # did not convert into a ScrapeResult; record it as a raw failure and count
                                # it as FAILED so it drives the PARTIAL exit code like scrape-urls.
                                status_counts[ScrapeStatus.FAILED] += 1
                                failed_urls.append(
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

        total_time = elapsed_since(start_time)
        print_log("\n--- Scraping Process Summary ---", "step")
        print_log(f"Total scraping process completed in {total_time:.2f} seconds.")
        print_log(f"{len(scraped_books_data)} new book{'s' if len(scraped_books_data) != 1 else ''} scraped.")
        print_log(
            f"{len(failed_urls)} URLs failed during detailed scrape.",
            "warning" if len(failed_urls) > 0 else "info",
        )
        print_log(f"{total_duplicates_skipped} books already in the database and skipped.")
        if cross_site_dropped:
            print_log(
                f"{cross_site_dropped} candidate(s) dropped as within-run cross-site duplicates.",
                "info",
            )
        if isbn13_duplicates:
            print_log(
                f"{isbn13_duplicates} scraped book(s) skipped as ISBN-13 already in the store.",
                "info",
            )
        if status_counts:
            # This breakdown tallies every detail-scrape OUTCOME, so its SUCCESS count can exceed
            # len(scraped_books_data): a SUCCESS-scraped book is still dropped afterward by the
            # detail-stage SCRAPE_FILTERS or the post-detail ISBN-13 dedup. A book counted SUCCESS
            # here but missing from the saved batch was filtered/deduped, not lost.
            print_log(f"Scrape status breakdown: {format_status_breakdown(status_counts)}.")
        if filtered_books:
            # Count every filtered-out book per reason for the run summary; the books themselves are
            # written to filtered_books.csv below and never saved to the store.
            reason_counts = Counter(item["reason"] for item in filtered_books)
            filter_breakdown = ", ".join(f"{reason}={count}" for reason, count in reason_counts.most_common())
            print_log(
                f"{len(filtered_books)} book(s) filtered out by SCRAPE_FILTERS: {filter_breakdown}.",
                "warning",
            )

        # --- Output Saving ---
        print_log("\n--- Output Summary ---", "step")
        # failed_urls.csv is a process diagnostic, not database content, so it's always
        # written as CSV best-effort, independent of --store-backend.
        write_failed_csv(
            failed_urls,
            fieldnames=FAILED_URLS_FIELDNAMES,
            filename="failed_urls.csv",
            directory=output_dir,
        )
        # filtered_books.csv is likewise a process diagnostic (never database content), so it's
        # always written best-effort regardless of --store-backend or --dry-run, through the same
        # shared writer as failed_urls.csv (just a different fieldname set and filename).
        write_failed_csv(
            filtered_books,
            fieldnames=FILTERED_BOOKS_FIELDNAMES,
            filename="filtered_books.csv",
            directory=output_dir,
        )

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

        # FATAL if the orchestration aborted; else PARTIAL when at least one detail scrape ended in a
        # failure status (FAILED/TIMEOUT/PARSE_ERROR/BLOCKED) or a save errored; else OK. A
        # DUPLICATE/NOT_FOUND/SKIPPED_ROBOTS outcome is not a failure, matching scrape-urls.
        if had_unhandled_error:
            return exitcodes.FATAL
        had_failure = save_errors > 0 or any(status_counts[status] for status in FAILURE_STATUSES)
        return exitcodes.PARTIAL if had_failure else exitcodes.OK
    finally:
        storage_backend.close()
