"""Unit/mock tests for bookscraper.commands.scrape_urls.

Every external collaborator (the resolved storage backend, Playwright, scrape_book,
asyncio.sleep) is mocked; no network, no browser, and no real ~/.bookscrapper or
books.json is ever touched because resolve_store_backend is always patched.
"""

import csv
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
def test_identify_website_all_branches(url: str, expected: str) -> None:
    """
    [Local] scrape urls: verifies identify website all branches.

    Scenario:
        Given test inputs and fixtures for identify website all branches
        When test_identify_website_all_branches executes the target flow
        Then the expected outcome for identify website all branches is confirmed.

    Boundaries:
        - Focus: identify website all branches
        - Fixtures/params: url, expected (parametrized, 5 cases)
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to identify website all branches
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by scrape urls
    """
    assert identify_website(url) == expected


# --------------------------------------------------------------------------- #
# save_failed_urls_to_csv  (scrape_urls variant: csv.writer, (url, status) tuples)
# --------------------------------------------------------------------------- #
def test_save_failed_urls_empty_creates_no_file(tmp_path) -> None:
    """
    [Local] scrape urls: verifies save failed urls empty creates no file.

    Scenario:
        Given test inputs and fixtures for save failed urls empty creates no file
        When test_save_failed_urls_empty_creates_no_file executes the target flow
        Then the expected outcome for save failed urls empty creates no file is confirmed.

    Boundaries:
        - Focus: save failed urls empty creates no file
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to save failed urls empty creates no file
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by scrape urls
    """
    target = tmp_path / "failed_books.csv"
    save_failed_urls_to_csv([], filename=str(target))
    assert not target.exists()


def test_scrape_urls_failed_csv_has_status_and_error_columns(tmp_path) -> None:
    """
    [Local] scrape urls: verifies scrape urls failed csv has status and error columns.

    Scenario:
        Given test inputs and fixtures for scrape urls failed csv has status and error columns
        When test_scrape_urls_failed_csv_has_status_and_error_columns executes the target flow
        Then the expected outcome for scrape urls failed csv has status and error columns is confirmed.

    Boundaries:
        - Focus: scrape urls failed csv has status and error columns
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to scrape urls failed csv has status and error columns
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by scrape urls
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


def test_save_failed_urls_open_failure_is_swallowed(tmp_path) -> None:
    """
    [Local] scrape urls: verifies save failed urls open failure is swallowed.

    Scenario:
        Given test inputs and fixtures for save failed urls open failure is swallowed
        When test_save_failed_urls_open_failure_is_swallowed executes the target flow
        Then the expected outcome for save failed urls open failure is swallowed is confirmed.

    Boundaries:
        - Focus: save failed urls open failure is swallowed
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to save failed urls open failure is swallowed
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by scrape urls
    """
    target = tmp_path / "failed_books.csv"
    with patch("builtins.open", side_effect=OSError("disk full")):
        # Must not propagate: the caller relies on best-effort diagnostic writes.
        save_failed_urls_to_csv([("u", "FAILED", "boom")], filename=str(target))
    assert not target.exists()


# --------------------------------------------------------------------------- #
# save_other_links_to_csv  (csv.writer, single "url" column)
# --------------------------------------------------------------------------- #
def test_save_other_links_empty_creates_no_file(tmp_path) -> None:
    """
    [Local] scrape urls: verifies save other links empty creates no file.

    Scenario:
        Given test inputs and fixtures for save other links empty creates no file
        When test_save_other_links_empty_creates_no_file executes the target flow
        Then the expected outcome for save other links empty creates no file is confirmed.

    Boundaries:
        - Focus: save other links empty creates no file
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to save other links empty creates no file
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by scrape urls
    """
    target = tmp_path / "other_links.csv"
    save_other_links_to_csv([], filename=str(target))
    assert not target.exists()


def test_save_other_links_writes_header_and_rows(tmp_path) -> None:
    """
    [Local] scrape urls: verifies save other links writes header and rows.

    Scenario:
        Given test inputs and fixtures for save other links writes header and rows
        When test_save_other_links_writes_header_and_rows executes the target flow
        Then the expected outcome for save other links writes header and rows is confirmed.

    Boundaries:
        - Focus: save other links writes header and rows
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to save other links writes header and rows
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by scrape urls
    """
    target = tmp_path / "other_links.csv"
    links = ["https://example.com/a", "https://example.org/b"]
    save_other_links_to_csv(links, filename=str(target))

    with open(target, encoding="utf-8", newline="") as fh:
        read_rows = list(csv.reader(fh))

    assert read_rows[0] == ["url"]
    assert read_rows[1:] == [[link] for link in links]


def test_save_other_links_open_failure_is_swallowed(tmp_path) -> None:
    """
    [Local] scrape urls: verifies save other links open failure is swallowed.

    Scenario:
        Given test inputs and fixtures for save other links open failure is swallowed
        When test_save_other_links_open_failure_is_swallowed executes the target flow
        Then the expected outcome for save other links open failure is swallowed is confirmed.

    Boundaries:
        - Focus: save other links open failure is swallowed
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to save other links open failure is swallowed
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by scrape urls
    """
    target = tmp_path / "other_links.csv"
    with patch("builtins.open", side_effect=OSError("read-only fs")):
        save_other_links_to_csv(["https://example.com/a"], filename=str(target))
    assert not target.exists()


# --------------------------------------------------------------------------- #
# helpers for run()
# --------------------------------------------------------------------------- #
def _write_csv(tmp_path, urls, header: str = "url"):
    target = tmp_path / "urls.csv"
    lines = [header, *urls]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def _make_playwright_factory():
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
async def test_run_no_valid_urls_saves_other_links_and_exits_zero(tmp_path) -> None:
    """
    [Local] scrape urls: verifies run no valid urls saves other links and exits zero.

    Scenario:
        Given test inputs and fixtures for run no valid urls saves other links and exits zero
        When test_run_no_valid_urls_saves_other_links_and_exits_zero executes the target flow
        Then the expected outcome for run no valid urls saves other links and exits zero is confirmed.

    Boundaries:
        - Focus: run no valid urls saves other links and exits zero
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to run no valid urls saves other links and exits zero
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by scrape urls
    """
    csv_path = _write_csv(tmp_path, ["https://example.com/book"])
    args = SimpleNamespace(input_file=str(csv_path), store_backend="json")

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


async def test_run_missing_input_file_exits_one(tmp_path) -> None:
    """
    [Local] scrape urls: verifies run missing input file exits one.

    Scenario:
        Given test inputs and fixtures for run missing input file exits one
        When test_run_missing_input_file_exits_one executes the target flow
        Then the expected outcome for run missing input file exits one is confirmed.

    Boundaries:
        - Focus: run missing input file exits one
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to run missing input file exits one
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by scrape urls
    """
    missing = tmp_path / "does_not_exist.csv"
    args = SimpleNamespace(input_file=str(missing), store_backend="json")

    backend = MagicMock()
    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        pytest.raises(SystemExit) as exc_info,
    ):
        await run(args)

    assert exc_info.value.code == 1
    backend.save_books.assert_not_called()


async def test_run_missing_url_column_exits_one(tmp_path) -> None:
    """
    [Local] scrape urls: verifies run missing url column exits one.

    Scenario:
        Given test inputs and fixtures for run missing url column exits one
        When test_run_missing_url_column_exits_one executes the target flow
        Then the expected outcome for run missing url column exits one is confirmed.

    Boundaries:
        - Focus: run missing url column exits one
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to run missing url column exits one
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by scrape urls
    """
    csv_path = _write_csv(tmp_path, ["https://leanpub.com/a"], header="link")
    args = SimpleNamespace(input_file=str(csv_path), store_backend="json")

    backend = MagicMock()
    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        pytest.raises(SystemExit) as exc_info,
    ):
        await run(args)

    assert exc_info.value.code == 1
    backend.save_books.assert_not_called()


async def test_run_unparseable_csv_exits_one(tmp_path) -> None:
    """
    [Local] scrape urls: verifies run unparseable csv exits one.

    Scenario:
        Given test inputs and fixtures for run unparseable csv exits one
        When test_run_unparseable_csv_exits_one executes the target flow
        Then the expected outcome for run unparseable csv exits one is confirmed.

    Boundaries:
        - Focus: run unparseable csv exits one
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to run unparseable csv exits one
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by scrape urls
    """
    csv_path = _write_csv(tmp_path, ["https://leanpub.com/a"])
    args = SimpleNamespace(input_file=str(csv_path), store_backend="json")

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
async def test_run_successful_book_is_saved_and_backend_closed(tmp_path) -> None:
    """
    [Local] scrape urls: verifies run successful book is saved and backend closed.

    Scenario:
        Given test inputs and fixtures for run successful book is saved and backend closed
        When test_run_successful_book_is_saved_and_backend_closed executes the target flow
        Then the expected outcome for run successful book is saved and backend closed is confirmed.

    Boundaries:
        - Focus: run successful book is saved and backend closed
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to run successful book is saved and backend closed
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by scrape urls
    """
    csv_path = _write_csv(tmp_path, ["https://leanpub.com/good"])
    args = SimpleNamespace(input_file=str(csv_path), store_backend="json")

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
async def test_run_non_success_results_recorded_not_saved(tmp_path, scrape_result, expected_row) -> None:
    """
    [Local] scrape urls: verifies run non success results recorded not saved.

    Scenario:
        Given test inputs and fixtures for run non success results recorded not saved
        When test_run_non_success_results_recorded_not_saved executes the target flow
        Then the expected outcome for run non success results recorded not saved is confirmed.

    Boundaries:
        - Focus: run non success results recorded not saved
        - Fixtures/params: tmp_path, scrape_result, expected_row (parametrized, 4 cases)
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to run non success results recorded not saved
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by scrape urls
    """
    csv_path = _write_csv(tmp_path, ["https://leanpub.com/x"])
    args = SimpleNamespace(input_file=str(csv_path), store_backend="json")

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


async def test_run_summary_counts_each_status(tmp_path) -> None:
    """Scenario: a run yields one SUCCESS, one NOT_FOUND, and one TIMEOUT result.

    Boundaries: the end-of-run summary must emit a per-ScrapeStatus breakdown tallying each
    status exactly once, in enum order, via ``print_log``.
    On failure: the summary drops or miscounts a status, so an operator can't see at a glance
    how a batch broke down (how many dead URLs vs. timeouts vs. real books).
    """
    urls = [f"https://leanpub.com/book-{i}" for i in range(3)]
    csv_path = _write_csv(tmp_path, urls)
    args = SimpleNamespace(input_file=str(csv_path), store_backend="json")

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


async def test_run_more_than_ten_urls_triggers_inter_batch_pause(tmp_path) -> None:
    # 11 recognized URLs => two batches (batch_size hardcoded at 10) => one pause.
    """
    [Local] scrape urls: verifies run more than ten urls triggers inter batch pause.

    Scenario:
        Given test inputs and fixtures for run more than ten urls triggers inter batch pause
        When test_run_more_than_ten_urls_triggers_inter_batch_pause executes the target flow
        Then the expected outcome for run more than ten urls triggers inter batch pause is confirmed.

    Boundaries:
        - Focus: run more than ten urls triggers inter batch pause
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to run more than ten urls triggers inter batch pause
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by scrape urls
    """
    urls = [f"https://leanpub.com/book-{i}" for i in range(11)]
    csv_path = _write_csv(tmp_path, urls)
    args = SimpleNamespace(input_file=str(csv_path), store_backend="json")

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
