"""
Shared start/end-of-run behavior for the step 1 commands (scrape-urls, search) - ADR 0003.

Step 1's only write target is the staging file. A run starts by pointing out a legacy repo-root
books.json (from the old --store-backend json days), and ends by appending its results to the
staging file and telling the user exactly how to review and push them.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ..backends.local.staging import (
    LEGACY_LOCAL_STORE_FILE,
    AppendSummary,
    StagingFileError,
    append_to_staging,
    dump_unsaved,
)
from ..book_utils import logger, print_log


def warn_about_legacy_store(staging_file: Path) -> None:
    """One-time hint: an old repo-root books.json exists but the staging file doesn't. Never moves anything."""
    staging_file = Path(staging_file)
    if LEGACY_LOCAL_STORE_FILE.exists() and not staging_file.exists() and staging_file != LEGACY_LOCAL_STORE_FILE:
        print_log(
            f"Note: found a legacy '{LEGACY_LOCAL_STORE_FILE}' from --store-backend json runs. To keep deduplicating "
            f"against it, move it into place first: mv {LEGACY_LOCAL_STORE_FILE} {staging_file}",
            "warning",
        )


def save_to_staging(staging_file: Path, books: list[dict], *, source: str) -> AppendSummary:
    """
    Appends scraped books to the staging file. If the file became unreadable/invalid while the
    scrape ran, nothing is overwritten: the results are dumped to a sibling *.unsaved-<timestamp>
    file and the process exits 1.
    """
    try:
        return append_to_staging(Path(staging_file), books, source=source)
    except (StagingFileError, OSError) as e:
        logger.critical(f"Could not append to staging file: {e}")
        print_log(f"Critical Error: could not append to the staging file: {e}", "error")
        if books:
            try:
                dumped = dump_unsaved(Path(staging_file), books)
                print_log(f"Scraped results were saved to {dumped} instead - nothing was lost.", "warning")
            except OSError as dump_error:
                print_log(f"Critical Error: could not save scraped results anywhere: {dump_error}", "error")
        sys.exit(1)


def print_step_one_summary(staging_file: Path, summary: AppendSummary, *, skipped_as_known: int) -> None:
    """Counts, what is left to review, and the exact next command."""
    print_log("\n--- Staging Summary ---", "step")
    print_log(f"Appended to {staging_file}: {summary.appended} new book(s).", "success" if summary.appended else "info")
    print_log(f"Already in the staging file (skipped at save): {summary.duplicates}.")
    print_log(f"Skipped before/while scraping as already known: {skipped_as_known}.")
    print_log(
        f"The staging file now holds {summary.total_in_file} document(s), {summary.pending_in_file} pending review."
    )
    print_log(
        f"Next: review {staging_file}, then run: bookscraper push --staging-file {staging_file} --dry-run",
        "step",
    )
