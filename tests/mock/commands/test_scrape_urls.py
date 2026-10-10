"""Unit/mock tests for bookscraper.commands.scrape_urls.

Every external collaborator (the resolved storage backend, Playwright, scrape_book,
asyncio.sleep) is mocked; no network, no browser, and no real ~/.bookscrapper or
books.json is ever touched because resolve_store_backend is always patched.
"""

import csv
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from bookscraper.backends.storage import SaveSummary
from bookscraper.commands.scrape_urls import (
    identify_website,
    run,
    save_failed_urls_to_csv,
    save_other_links_to_csv,
)
from bookscraper.scraping.results import ScrapeResult, ScrapeStatus

MODULE = "bookscraper.commands.scrape_urls"


# --------------------------------------------------------------------------- #
# identify_website
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.amazon.com/dp/1234567890", "amazon"),
        ("https://www.packtpub.com/product/some-book", "packtpub"),
        ("https://leanpub.com/some-book", "leanpub"),
        ("https://www.oreilly.com/library/view/some-book", "oreilly"),
        ("https://example.com/book", "other"),
    ],
)
def test_identify_website_all_branches(url: str, expected: str):
    """
    [Local] scrape urls: verifies that each supported URL pattern maps to the correct site tag.

    Scenario:
        Given one of amazon/packtpub/leanpub/oreilly/unknown URLs
        When identify_website is called
        Then the expected site string ("amazon", "packtpub", "leanpub", "oreilly", "other")
        is returned.

    Boundaries:
        - Parametrized over 5 URL/site pairs; no mocking needed (pure string matching).

    On failure, first check:
        - The URL pattern regexes or string conditions in identify_website.
    """
    assert identify_website(url) == expected


# --------------------------------------------------------------------------- #
# save_failed_urls_to_csv  (scrape_urls variant: csv.writer, (url, status) tuples)
# --------------------------------------------------------------------------- #
def test_save_failed_urls_empty_creates_no_file(tmp_path):
    """
    [Local] scrape urls: verifies that an empty failed-URL list creates no CSV file.

    Scenario:
        Given an empty list passed to save_failed_urls_to_csv
        When the function is called
        Then no file is created at the target path.

    Boundaries:
        - Only the "nothing to write" early-exit is tested; actual CSV content is separate.

    On failure, first check:
        - The empty-list guard in save_failed_urls_to_csv.
    """
    target = tmp_path / "failed_books.csv"
    save_failed_urls_to_csv([], filename=str(target))
    assert not target.exists()


def test_scrape_urls_failed_csv_has_status_and_error_columns(tmp_path):
    """
    [Local] scrape urls: verifies that the failed-URLs CSV has the correct header and rows.

    Scenario:
        Given three (url, status, error) tuples covering DUPLICATE/NOT_FOUND/TIMEOUT
        When save_failed_urls_to_csv writes the file
        Then the CSV has header ["url", "status", "error"] and the body rows match the input.

    Boundaries:
        - Tests the scrape_urls variant (csv.writer with tuples); the search variant
          (DictWriter) is in test_search.py.

    On failure, first check:
        - The header list and row-writing loop in save_failed_urls_to_csv.
    """
    target = tmp_path / "failed_books.csv"
    rows = [
        ("https://leanpub.com/a", "DUPLICATE", ""),
        ("https://www.amazon.com/dp/1", "NOT_FOUND", "404 page detected"),
        ("https://www.oreilly.com/x", "TIMEOUT", "timed out after 3 attempts"),
    ]
    save_failed_urls_to_csv(rows, filename=str(target))

    with open(target, encoding="utf-8", newline="") as fh:
        read_rows = list(csv.reader(fh))

    assert read_rows[0] == ["url", "status", "error"]
    assert read_rows[1:] == [list(r) for r in rows]


def test_save_failed_urls_open_failure_is_swallowed(tmp_path):
    """
    [Local] scrape urls: verifies that an OSError on file open is swallowed, not propagated.

    Scenario:
        Given builtins.open patched to raise OSError("disk full")
        When save_failed_urls_to_csv is called with a non-empty list
        Then no exception propagates to the caller and no file is created.

    Boundaries:
        - Best-effort write contract: the caller must not crash when the diagnostic CSV fails.

    On failure, first check:
        - The try/except around the open() call in save_failed_urls_to_csv.
    """
    target = tmp_path / "failed_books.csv"
    with patch("builtins.open", side_effect=OSError("disk full")):
        # Must not propagate: the caller relies on best-effort diagnostic writes.
        save_failed_urls_to_csv([("u", "FAILED", "boom")], filename=str(target))
    assert not target.exists()


# --------------------------------------------------------------------------- #
# save_other_links_to_csv  (csv.writer, single "url" column)
# --------------------------------------------------------------------------- #
def test_save_other_links_empty_creates_no_file(tmp_path):
    """
    [Local] scrape urls: verifies that an empty other-links list creates no CSV file.

    Scenario:
        Given an empty list passed to save_other_links_to_csv
        When the function is called
        Then no file is created at the target path.

    Boundaries:
        - Only the empty-guard is tested; the write path is the separate "writes header" test.

    On failure, first check:
        - The empty-list guard in save_other_links_to_csv.
    """
    target = tmp_path / "other_links.csv"
    save_other_links_to_csv([], filename=str(target))
    assert not target.exists()


def test_save_other_links_writes_header_and_rows(tmp_path):
    """
    [Local] scrape urls: verifies that save_other_links_to_csv writes a single-column CSV.

    Scenario:
        Given two URL strings passed to save_other_links_to_csv
        When the function writes the file
        Then the CSV has header ["url"] and one row per URL.

    Boundaries:
        - Covers the normal write path; the empty-list and failure paths are separate tests.

    On failure, first check:
        - The header and row-writing loop in save_other_links_to_csv.
    """
    target = tmp_path / "other_links.csv"
    links = ["https://example.com/a", "https://example.org/b"]
    save_other_links_to_csv(links, filename=str(target))

    with open(target, encoding="utf-8", newline="") as fh:
        read_rows = list(csv.reader(fh))

    assert read_rows[0] == ["url"]
    assert read_rows[1:] == [[link] for link in links]


def test_save_other_links_open_failure_is_swallowed(tmp_path):
    """
    [Local] scrape urls: verifies that an OSError on file open is swallowed in save_other_links_to_csv.

    Scenario:
        Given builtins.open patched to raise OSError("read-only fs")
        When save_other_links_to_csv is called with a non-empty list
        Then no exception propagates and no file is created.

    Boundaries:
        - Best-effort write contract; mirrors the equivalent test for save_failed_urls_to_csv.

    On failure, first check:
        - The try/except around the open() call in save_other_links_to_csv.
    """
    target = tmp_path / "other_links.csv"
    with patch("builtins.open", side_effect=OSError("read-only fs")):
        save_other_links_to_csv(["https://example.com/a"], filename=str(target))
    assert not target.exists()


# --------------------------------------------------------------------------- #
# helpers for run()
# --------------------------------------------------------------------------- #
def _write_csv(tmp_path, urls, header: str = "url") -> Path:
    """Write a CSV fixture file under tmp_path for scrape-urls tests."""
    target = tmp_path / "urls.csv"
    lines = [header, *urls]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def _make_playwright_factory() -> tuple[MagicMock, MagicMock]:
    """Builds an async_playwright() stand-in.

    Returns (factory, browser). `async with factory() as p` yields a page-factory
    mock whose chromium.launch is an AsyncMock returning `browser` (whose .close()
    is an AsyncMock).
    """
    browser = MagicMock()
    browser.close = AsyncMock()

    p = MagicMock()
    p.chromium.launch = AsyncMock(return_value=browser)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=p)
    cm.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock(return_value=cm)
    return factory, browser


# --------------------------------------------------------------------------- #
# run() - early-exit / error branches
# --------------------------------------------------------------------------- #
async def test_run_no_valid_urls_saves_other_links_and_exits_zero(tmp_path):
    """
    [Local] scrape urls: verifies that a CSV with only "other" URLs exits 0 without scraping.

    Scenario:
        Given a CSV containing only an example.com URL (tagged "other" by identify_website)
        When run() is called
        Then sys.exit(0) is raised, the URL is passed to save_other_links_to_csv,
        and the browser is never opened.

    Boundaries:
        - No book URLs in the input; the scraping loop is never entered.

    On failure, first check:
        - The "no valid URLs" early-exit branch in run() and the other-links routing.
    """
    csv_path = _write_csv(tmp_path, ["https://example.com/book"])
    args = SimpleNamespace(input_file=str(csv_path), store_backend="json", ignore_robots=False)

    backend = MagicMock()
    pw_factory, _browser = _make_playwright_factory()

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend) as resolve,
        patch(f"{MODULE}.async_playwright", pw_factory),
        patch(f"{MODULE}.save_other_links_to_csv") as save_other,
        patch(f"{MODULE}.save_failed_urls_to_csv") as save_failed,
        pytest.raises(SystemExit) as exc_info,
    ):
        await run(args)

    assert exc_info.value.code == 0
    resolve.assert_called_once_with("json")
    # The single "other" URL is handed to the skipped-links writer.
    save_other.assert_called_once_with(["https://example.com/book"])
    # Never reaches the browser section, never saves books, never writes failed CSV.
    pw_factory.assert_not_called()
    save_failed.assert_not_called()
    backend.save_books.assert_not_called()


async def test_run_missing_input_file_exits_one(tmp_path):
    """
    [Local] scrape urls: verifies that a nonexistent input CSV exits 1 without saving.

    Scenario:
        Given an input_file path that does not exist
        When run() is called
        Then sys.exit(1) is raised and save_books is never called.

    Boundaries:
        - Only the file-not-found guard is exercised; bad CSV structure is a separate test.

    On failure, first check:
        - The file-existence check at the start of run().
    """
    missing = tmp_path / "does_not_exist.csv"
    args = SimpleNamespace(input_file=str(missing), store_backend="json", ignore_robots=False)

    backend = MagicMock()
    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        pytest.raises(SystemExit) as exc_info,
    ):
        await run(args)

    assert exc_info.value.code == 1
    backend.save_books.assert_not_called()


async def test_run_missing_url_column_exits_one(tmp_path):
    """
    [Local] scrape urls: verifies that a CSV without a "url" column header exits 1.

    Scenario:
        Given a CSV whose only column is named "link" instead of "url"
        When run() is called
        Then sys.exit(1) is raised and save_books is never called.

    Boundaries:
        - Covers the missing-column KeyError guard; a completely unparseable CSV is separate.

    On failure, first check:
        - The column-name check after pd.read_csv in run().
    """
    csv_path = _write_csv(tmp_path, ["https://leanpub.com/a"], header="link")
    args = SimpleNamespace(input_file=str(csv_path), store_backend="json", ignore_robots=False)

    backend = MagicMock()
    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        pytest.raises(SystemExit) as exc_info,
    ):
        await run(args)

    assert exc_info.value.code == 1
    backend.save_books.assert_not_called()


async def test_run_unparseable_csv_exits_one(tmp_path):
    """
    [Local] scrape urls: verifies that a pd.errors.ParserError on CSV load exits 1.

    Scenario:
        Given pd.read_csv patched to raise ParserError("bad csv")
        When run() is called
        Then sys.exit(1) is raised and save_books is never called.

    Boundaries:
        - Covers the CSV-parse exception path; missing column and missing file are separate.

    On failure, first check:
        - The try/except around pd.read_csv in run().
    """
    csv_path = _write_csv(tmp_path, ["https://leanpub.com/a"])
    args = SimpleNamespace(input_file=str(csv_path), store_backend="json", ignore_robots=False)

    backend = MagicMock()
    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.pd.read_csv", side_effect=pd.errors.ParserError("bad csv")),
        pytest.raises(SystemExit) as exc_info,
    ):
        await run(args)

    assert exc_info.value.code == 1
    backend.save_books.assert_not_called()


# --------------------------------------------------------------------------- #
# run() - scraping loop / result type-switch
# --------------------------------------------------------------------------- #
async def test_run_successful_book_is_saved_and_backend_closed(tmp_path):
    """
    [Local] scrape urls: verifies that a SUCCESS result saves the book and closes browser/backend.

    Scenario:
        Given a CSV with one leanpub URL and scrape_book returning a SUCCESS ScrapeResult
        When run() is called
        Then save_books is called with the book dict, the browser and backend are both
        closed, and the failed-URLs CSV receives an empty list.

    Boundaries:
        - Single URL, single batch; multi-batch behavior is the separate pause test.

    On failure, first check:
        - The save_books call, browser.close, and backend.close in run()'s finally block.
    """
    csv_path = _write_csv(tmp_path, ["https://leanpub.com/good"])
    args = SimpleNamespace(input_file=str(csv_path), store_backend="json", ignore_robots=False)

    backend = MagicMock()
    backend.save_books.return_value = SaveSummary(inserted=1, duplicates=0, errors=0)
    pw_factory, browser = _make_playwright_factory()
    book = {"title": "Good Book", "hash": "abc123"}
    result = ScrapeResult(ScrapeStatus.SUCCESS, url="https://leanpub.com/good", site="leanpub", book=book)
    scrape_book = AsyncMock(side_effect=[result])

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.async_playwright", pw_factory),
        patch(f"{MODULE}.scrape_book", scrape_book),
        patch(f"{MODULE}.save_other_links_to_csv"),
        patch(f"{MODULE}.save_failed_urls_to_csv") as save_failed,
    ):
        await run(args)

    backend.save_books.assert_called_once_with([book])
    browser.close.assert_awaited_once()
    backend.close.assert_called_once_with()
    # Success path records no failed/duplicate entries.
    save_failed.assert_called_once_with([])


@pytest.mark.parametrize(
    ("scrape_result", "expected_row"),
    [
        (
            ScrapeResult(ScrapeStatus.DUPLICATE, url="https://leanpub.com/x", site="leanpub"),
            ("https://leanpub.com/x", "DUPLICATE", ""),
        ),
        (
            ScrapeResult(
                ScrapeStatus.FAILED, url="https://leanpub.com/x", site="leanpub", error="KeyError: 'BASE_URL'"
            ),
            ("https://leanpub.com/x", "FAILED", "KeyError: 'BASE_URL'"),
        ),
        (
            ScrapeResult(
                ScrapeStatus.NOT_FOUND, url="https://leanpub.com/x", site="leanpub", error="404 page detected"
            ),
            ("https://leanpub.com/x", "NOT_FOUND", "404 page detected"),
        ),
        (
            ScrapeResult(
                ScrapeStatus.TIMEOUT, url="https://leanpub.com/x", site="leanpub", error="timed out after 3 attempts"
            ),
            ("https://leanpub.com/x", "TIMEOUT", "timed out after 3 attempts"),
        ),
    ],
)
async def test_run_non_success_results_recorded_not_saved(tmp_path, scrape_result, expected_row):
    """
    [Local] scrape urls: verifies that non-SUCCESS results are recorded in the CSV but not saved.

    Scenario:
        Given a CSV with one URL and scrape_book returning DUPLICATE/FAILED/NOT_FOUND/TIMEOUT
        When run() is called
        Then save_books is never called, the backend and browser are closed, and
        save_failed_urls_to_csv receives exactly one (url, status, error) row whose
        values match the typed result.

    Boundaries:
        - Parametrized over all four non-success statuses; the SUCCESS path is a separate test.
        - The UNKNOWN_ERROR status no longer exists; every non-success carries a typed ScrapeStatus.

    On failure, first check:
        - The match/case result-type dispatch in run() and the (url, status, error) row assembly.
    """
    csv_path = _write_csv(tmp_path, ["https://leanpub.com/x"])
    args = SimpleNamespace(input_file=str(csv_path), store_backend="json", ignore_robots=False)

    backend = MagicMock()
    pw_factory, browser = _make_playwright_factory()
    scrape_book = AsyncMock(side_effect=[scrape_result])

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.async_playwright", pw_factory),
        patch(f"{MODULE}.scrape_book", scrape_book),
        patch(f"{MODULE}.save_other_links_to_csv"),
        patch(f"{MODULE}.save_failed_urls_to_csv") as save_failed,
    ):
        await run(args)

    # No success result => nothing saved, but the backend is still closed.
    backend.save_books.assert_not_called()
    backend.close.assert_called_once_with()
    browser.close.assert_awaited_once()

    # The failed/duplicate diagnostic CSV gets exactly the one (url, status, error) row,
    # keyed off the typed result's own url and error (UNKNOWN_ERROR no longer exists).
    save_failed.assert_called_once()
    (recorded,), _ = save_failed.call_args
    assert recorded == [expected_row]


async def test_run_summary_counts_each_status(tmp_path):
    """
    [Local] scrape urls: verifies run summary counts each status.

    Scenario:
        a run yields one SUCCESS, one NOT_FOUND, and one TIMEOUT result.

    Boundaries:
        - the end-of-run summary must emit a per-ScrapeStatus breakdown tallying each status exactly once, in enum order, via ``print_log``.

    On failure, first check:
        - the summary drops or miscounts a status, so an operator can't see at a glance how a batch broke down (how many dead URLs vs. timeouts vs. real books).
    """
    urls = [f"https://leanpub.com/book-{i}" for i in range(3)]
    csv_path = _write_csv(tmp_path, urls)
    args = SimpleNamespace(input_file=str(csv_path), store_backend="json", ignore_robots=False)

    backend = MagicMock()
    backend.save_books.return_value = SaveSummary(inserted=1, duplicates=0, errors=0)
    pw_factory, _browser = _make_playwright_factory()
    results = [
        ScrapeResult(ScrapeStatus.SUCCESS, url=urls[0], site="leanpub", book={"title": "A", "hash": "h"}),
        ScrapeResult(ScrapeStatus.NOT_FOUND, url=urls[1], site="leanpub", error="404 page detected"),
        ScrapeResult(ScrapeStatus.TIMEOUT, url=urls[2], site="leanpub", error="timed out after 3 attempts"),
    ]
    scrape_book = AsyncMock(side_effect=results)
    print_log = MagicMock()

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.async_playwright", pw_factory),
        patch(f"{MODULE}.scrape_book", scrape_book),
        patch(f"{MODULE}.asyncio.sleep", AsyncMock()),
        patch(f"{MODULE}.save_other_links_to_csv"),
        patch(f"{MODULE}.save_failed_urls_to_csv"),
        patch(f"{MODULE}.print_log", print_log),
    ):
        await run(args)

    breakdown_lines = [
        call.args[0] for call in print_log.call_args_list if call.args and "Scrape status breakdown:" in call.args[0]
    ]
    assert len(breakdown_lines) == 1
    line = breakdown_lines[0]
    assert "SUCCESS=1" in line
    assert "NOT_FOUND=1" in line
    assert "TIMEOUT=1" in line
    # Only the three statuses that actually occurred appear (no DUPLICATE=0 noise).
    assert "DUPLICATE" not in line
    assert "FAILED" not in line


async def test_run_more_than_ten_urls_triggers_inter_batch_pause(tmp_path):
    # 11 recognized URLs => two batches (batch_size hardcoded at 10) => one pause.
    """
    [Local] scrape urls: verifies that 11 URLs produce two batches and exactly one inter-batch pause.

    Scenario:
        Given 11 leanpub URLs (batch_size is hardcoded at 10) each returning SUCCESS
        When run() is called
        Then scrape_book is awaited 11 times, asyncio.sleep is awaited exactly once
        (between batch 1 and batch 2), and all books are saved together.

    Boundaries:
        - Tests the batch-boundary sleep; exactly 10 URLs (one batch) would not sleep.

    On failure, first check:
        - The inter-batch sleep condition in run()'s batch loop.
    """
    urls = [f"https://leanpub.com/book-{i}" for i in range(11)]
    csv_path = _write_csv(tmp_path, urls)
    args = SimpleNamespace(input_file=str(csv_path), store_backend="json", ignore_robots=False)

    backend = MagicMock()
    backend.save_books.return_value = SaveSummary(inserted=11, duplicates=0, errors=0)
    pw_factory, browser = _make_playwright_factory()
    books = [{"title": f"Book {i}", "hash": f"h{i}"} for i in range(11)]
    results = [
        ScrapeResult(ScrapeStatus.SUCCESS, url=f"https://leanpub.com/book-{i}", site="leanpub", book=book)
        for i, book in enumerate(books)
    ]
    scrape_book = AsyncMock(side_effect=results)
    sleep = AsyncMock()

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.async_playwright", pw_factory),
        patch(f"{MODULE}.scrape_book", scrape_book),
        patch(f"{MODULE}.asyncio.sleep", sleep),
        patch(f"{MODULE}.save_other_links_to_csv"),
        patch(f"{MODULE}.save_failed_urls_to_csv"),
    ):
        await run(args)

    # All 11 scraped once each, across two batches.
    assert scrape_book.await_count == 11
    # Exactly one inter-batch pause (between batch 1 and batch 2).
    sleep.assert_awaited_once()
    backend.save_books.assert_called_once_with(books)
    backend.close.assert_called_once_with()
    browser.close.assert_awaited_once()


def test_failed_and_other_link_csvs_neutralize_formula_cells(tmp_path):
    """
    [Local] scrape urls: verifies both diagnostic CSV writers quote formula-leading cells.

    Scenario:
        Given a failed row whose error and an other-link whose URL start with spreadsheet formula triggers
        When save_failed_urls_to_csv and save_other_links_to_csv write their files
        Then those cells are written with a leading single quote and ordinary cells are unchanged.

    Boundaries:
        - Covers failed_books.csv and other_links.csv; the search variant has its own test

    On failure, first check:
        - csv_safe_cell is applied to every cell in both writers in commands/scrape_urls.py
    """
    failed = tmp_path / "failed_books.csv"
    save_failed_urls_to_csv([("https://x/1", "FAILED", "=cmd|' /C calc'!A0")], filename=str(failed))
    other = tmp_path / "other_links.csv"
    save_other_links_to_csv(["@SUM(1)", "https://example.com/ok"], filename=str(other))

    with open(failed, encoding="utf-8", newline="") as fh:
        assert list(csv.reader(fh))[1] == ["https://x/1", "FAILED", "'=cmd|' /C calc'!A0"]
    with open(other, encoding="utf-8", newline="") as fh:
        assert [row[0] for row in csv.reader(fh)][1:] == ["'@SUM(1)", "https://example.com/ok"]



async def test_ignore_robots_flag_bypasses_check_and_warns_once(tmp_path):
    """
    [Local] scrape urls: verifies --ignore-robots passes robots=None and warns exactly once.

    Scenario:
        Given a CSV with two leanpub URLs and args.ignore_robots=True
        When run() is called
        Then every scrape_book call receives robots=None (the robots gate is bypassed), and a
        single one-time "--ignore-robots" warning is printed.

    Boundaries:
        - Opt-out wiring only; the enforced-path behavior is covered by the scrape_details test
          test_disallowed_url_yields_skipped_robots_result.

    On failure, first check:
        - The ``None if args.ignore_robots else RobotsCache(...)`` wiring and the one-time
          warning in scrape_urls.run().
    """
    csv_path = _write_csv(tmp_path, ["https://leanpub.com/a", "https://leanpub.com/b"])
    args = SimpleNamespace(input_file=str(csv_path), store_backend="json", ignore_robots=True)

    backend = MagicMock()
    backend.save_books.return_value = SaveSummary(inserted=2, duplicates=0, errors=0)
    pw_factory, _browser = _make_playwright_factory()
    results = [
        ScrapeResult(ScrapeStatus.SUCCESS, url="https://leanpub.com/a", site="leanpub", book={"title": "A", "hash": "a"}),
        ScrapeResult(ScrapeStatus.SUCCESS, url="https://leanpub.com/b", site="leanpub", book={"title": "B", "hash": "b"}),
    ]
    scrape_book = AsyncMock(side_effect=results)
    printed: list[tuple[str, str]] = []

    def _record_print(message, severity="info", *args, **kwargs):
        """Capture (message, severity) for every print_log call."""
        printed.append((message, severity))

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.async_playwright", pw_factory),
        patch(f"{MODULE}.scrape_book", scrape_book),
        patch(f"{MODULE}.save_other_links_to_csv"),
        patch(f"{MODULE}.save_failed_urls_to_csv"),
        patch(f"{MODULE}.print_log", _record_print),
    ):
        await run(args)

    # Every scrape_book call is handed robots=None (the 6th positional arg).
    assert scrape_book.await_count == 2
    for call in scrape_book.await_args_list:
        assert call.args[5] is None

    warnings = [msg for msg, sev in printed if sev == "warning" and "--ignore-robots" in msg]
    assert len(warnings) == 1
