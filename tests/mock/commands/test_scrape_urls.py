"""Unit/mock tests for bookscraper.commands.scrape_urls.

Every external collaborator (the resolved storage backend, Playwright, scrape_book,
asyncio.sleep) is mocked; no network, no browser, and no real ~/.bookscrapper or
books.json is ever touched because resolve_store_backend is always patched.
"""

import csv
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bookscraper import exitcodes
from bookscraper.backends.storage import JsonBackend, SaveSummary
from bookscraper.commands._reporting import RunReport
from bookscraper.commands.scrape_urls import (
    identify_website,
    run,
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
async def test_no_valid_urls_returns_ok_without_sys_exit_in_command(tmp_path):
    """
    [Local] scrape urls: verifies that a CSV with only "other" URLs returns OK without scraping.

    Scenario:
        Given a CSV containing only an example.com URL (tagged "other" by identify_website)
        When run() is called
        Then run() returns exitcodes.OK (never sys.exit mid-function), the URL is passed to
        save_other_links_to_csv, the browser is never opened, and the backend is closed.

    Boundaries:
        - No book URLs in the input; the scraping loop is never entered.

    On failure, first check:
        - The "no valid URLs" branch in run() returning OK and the other-links routing.
    """
    csv_path = _write_csv(tmp_path, ["https://example.com/book"])
    args = SimpleNamespace(input_file=str(csv_path), store_backend="json", ignore_robots=False)

    backend = MagicMock()
    pw_factory, _browser = _make_playwright_factory()

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend) as resolve,
        patch(f"{MODULE}.async_playwright", pw_factory),
        patch(f"{MODULE}.save_other_links_to_csv") as save_other,
        patch(f"{MODULE}.write_failed_csv") as save_failed,
    ):
        code = await run(args)

    assert code == exitcodes.OK
    resolve.assert_called_once_with("json", None)
    # The single "other" URL is handed to the skipped-links writer, in the default "." directory.
    save_other.assert_called_once_with(["https://example.com/book"], directory=".")
    # Never reaches the browser section, never saves books, never writes failed CSV.
    pw_factory.assert_not_called()
    save_failed.assert_not_called()
    backend.save_books.assert_not_called()
    backend.close.assert_called_once_with()


async def test_run_missing_input_file_returns_fatal(tmp_path):
    """
    [Local] scrape urls: verifies that a nonexistent input CSV returns FATAL without saving.

    Scenario:
        Given an input_file path that does not exist
        When run() is called
        Then run() returns exitcodes.FATAL, save_books is never called, and the backend is closed.

    Boundaries:
        - Only the file-not-found guard is exercised; bad CSV structure is a separate test.

    On failure, first check:
        - The file-existence check at the start of run() and its FATAL return.
    """
    missing = tmp_path / "does_not_exist.csv"
    args = SimpleNamespace(input_file=str(missing), store_backend="json", ignore_robots=False)

    backend = MagicMock()
    with patch(f"{MODULE}.resolve_store_backend", return_value=backend):
        code = await run(args)

    assert code == exitcodes.FATAL
    backend.save_books.assert_not_called()
    backend.close.assert_called_once_with()


async def test_run_missing_url_column_returns_fatal(tmp_path):
    """
    [Local] scrape urls: verifies that a CSV without a "url" column header returns FATAL.

    Scenario:
        Given a CSV whose only column is named "link" instead of "url"
        When run() is called
        Then run() returns exitcodes.FATAL and save_books is never called.

    Boundaries:
        - Covers the missing-column KeyError guard; a completely unparseable CSV is separate.

    On failure, first check:
        - The column-name check after pd.read_csv in run() and its FATAL return.
    """
    csv_path = _write_csv(tmp_path, ["https://leanpub.com/a"], header="link")
    args = SimpleNamespace(input_file=str(csv_path), store_backend="json", ignore_robots=False)

    backend = MagicMock()
    with patch(f"{MODULE}.resolve_store_backend", return_value=backend):
        code = await run(args)

    assert code == exitcodes.FATAL
    backend.save_books.assert_not_called()


async def test_run_unparseable_csv_returns_fatal(tmp_path):
    """
    [Local] scrape urls: verifies that a csv.Error on CSV load returns FATAL.

    Scenario:
        Given csv.DictReader patched to raise csv.Error("bad csv")
        When run() is called
        Then run() returns exitcodes.FATAL and save_books is never called.

    Boundaries:
        - Covers the CSV-parse exception path; missing column and missing file are separate.

    On failure, first check:
        - The ``except (csv.Error, UnicodeDecodeError)`` clause around the DictReader in run().
    """
    csv_path = _write_csv(tmp_path, ["https://leanpub.com/a"])
    args = SimpleNamespace(input_file=str(csv_path), store_backend="json", ignore_robots=False)

    backend = MagicMock()
    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.csv.DictReader", side_effect=csv.Error("bad csv")),
    ):
        code = await run(args)

    assert code == exitcodes.FATAL
    backend.save_books.assert_not_called()


async def test_run_empty_csv_returns_fatal(tmp_path):
    """
    [Local] scrape urls: verifies that an empty CSV file (no header/rows) returns FATAL.

    Scenario:
        Given an input file that exists but is empty, so DictReader.fieldnames is None
        When run() is called
        Then run() returns exitcodes.FATAL cleanly (no traceback) and save_books is never called.

    Boundaries:
        - Covers the empty-file path; a missing file, missing column and unparseable CSV are
          separate branches.

    On failure, first check:
        - The ``reader.fieldnames is None`` guard around the DictReader in run().
    """
    csv_path = tmp_path / "urls.csv"
    csv_path.write_text("", encoding="utf-8")
    args = SimpleNamespace(input_file=str(csv_path), store_backend="json", ignore_robots=False)

    backend = MagicMock()
    with patch(f"{MODULE}.resolve_store_backend", return_value=backend):
        code = await run(args)

    assert code == exitcodes.FATAL
    backend.save_books.assert_not_called()


async def test_csv_with_bom_and_extra_columns_is_read(tmp_path):
    """
    [Local] scrape urls: verifies a UTF-8-BOM CSV with extra columns is read and its URL scraped.

    Scenario:
        Given a CSV written with a leading UTF-8 BOM, a header of "url,title,rating" and one
            leanpub data row, resolving to a SUCCESS ScrapeResult
        When run() is called
        Then the BOM does not corrupt the "url" header (it is still recognised), only the url
            column is consumed, and the book is scraped and saved.

    Boundaries:
        - Covers the utf-8-sig BOM strip and DictReader's tolerance of extra columns in the
          stdlib csv path that replaced pandas.

    On failure, first check:
        - The encoding="utf-8-sig" open() and the DictReader fieldnames handling in run().
    """
    csv_path = tmp_path / "urls.csv"
    csv_path.write_text(
        "url,title,rating\nhttps://leanpub.com/good,Some Book,4.5\n",
        encoding="utf-8-sig",
    )
    args = SimpleNamespace(input_file=str(csv_path), store_backend="json", ignore_robots=False)

    backend = MagicMock()
    backend.save_books.return_value = SaveSummary(inserted=1, duplicates=0, errors=0)
    pw_factory, browser = _make_playwright_factory()
    book = {"title": "Some Book", "hash": "abc123"}
    result = ScrapeResult(ScrapeStatus.SUCCESS, url="https://leanpub.com/good", site="leanpub", book=book)
    scrape_book = AsyncMock(side_effect=[result])

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.async_playwright", pw_factory),
        patch(f"{MODULE}.scrape_book", scrape_book),
        patch(f"{MODULE}.save_other_links_to_csv"),
        patch(f"{MODULE}.write_failed_csv"),
    ):
        code = await run(args)

    assert code == exitcodes.OK
    # The URL (not the BOM-prefixed header, not the extra columns) reached scrape_book and was saved.
    assert scrape_book.await_count == 1
    assert scrape_book.await_args.args[0] == "https://leanpub.com/good"
    backend.save_books.assert_called_once_with([book])
    browser.close.assert_awaited_once()
    backend.close.assert_called_once_with()


async def test_csv_missing_url_column_exits_with_same_message(tmp_path):
    """
    [Local] scrape urls: verifies the missing-"url"-column message matches the pre-pandas wording.

    Scenario:
        Given a CSV whose only header is "link" (no "url" column)
        When run() is called
        Then run() returns exitcodes.FATAL and the exact "does not contain a 'url' column" error
            string is printed through print_log, unchanged from the pandas implementation.

    Boundaries:
        - Pins the user-facing message so the stdlib csv rewrite cannot silently reword it; the
          FATAL return itself is also covered by test_run_missing_url_column_returns_fatal.

    On failure, first check:
        - The ``"url" not in fieldnames`` branch's print_log message in run().
    """
    csv_path = _write_csv(tmp_path, ["https://leanpub.com/a"], header="link")
    args = SimpleNamespace(input_file=str(csv_path), store_backend="json", ignore_robots=False)

    backend = MagicMock()
    printed: list[tuple[str, str]] = []

    def _record_print(message, severity="info", *args, **kwargs):
        """Capture (message, severity) for every print_log call."""
        printed.append((message, severity))

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.print_log", _record_print),
    ):
        code = await run(args)

    assert code == exitcodes.FATAL
    expected = (
        f"Critical Error: The CSV file '{csv_path}' does not contain a 'url' column. "
        "Please ensure it has a 'url' header."
    )
    assert (expected, "error") in printed
    backend.save_books.assert_not_called()
    backend.close.assert_called_once_with()


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
        patch(f"{MODULE}.write_failed_csv") as save_failed,
    ):
        await run(args)

    backend.save_books.assert_called_once_with([book])
    browser.close.assert_awaited_once()
    backend.close.assert_called_once_with()
    # Success path records no failed/duplicate entries (written to the default "." directory).
    save_failed.assert_called_once()
    assert save_failed.call_args.args[0] == []
    assert save_failed.call_args.kwargs["directory"] == "."


async def test_run_records_per_site_count_and_save_into_report(tmp_path):
    """
    [Local] scrape urls: verifies run() records the per-site count, save summary and flags.

    Scenario:
        Given a CSV with one amazon URL, a SUCCESS ScrapeResult, and a RunReport passed to run()
        When run() completes
        Then report.per_site["amazon"] is {SUCCESS: 1}, report.save reflects the insert,
        store_backend is "json", and the ai_fallback block is None (no --ai-fallback).

    Boundaries:
        - Only the report-recording hooks in run(); the save/close contract is a separate test.

    On failure, first check:
        - The report.record_status / report.set_save / report.set_context calls in scrape_urls.run.
    """
    csv_path = _write_csv(tmp_path, ["https://www.amazon.com/dp/B01"])
    args = SimpleNamespace(input_file=str(csv_path), store_backend="json", ignore_robots=False)
    report = RunReport("scrape-urls", ["scrape-urls", "--store-backend", "json"])

    backend = MagicMock()
    backend.save_books.return_value = SaveSummary(inserted=1, duplicates=0, errors=0)
    pw_factory, _browser = _make_playwright_factory()
    book = {"title": "Good Book", "site": "amazon", "hash": "abc123"}
    result = ScrapeResult(ScrapeStatus.SUCCESS, url="https://www.amazon.com/dp/B01", site="amazon", book=book)

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.async_playwright", pw_factory),
        patch(f"{MODULE}.scrape_book", AsyncMock(side_effect=[result])),
        patch(f"{MODULE}.save_other_links_to_csv"),
        patch(f"{MODULE}.write_failed_csv"),
    ):
        await run(args, report)

    document = report.to_dict()
    assert document["per_site"]["amazon"] == {"SUCCESS": 1}
    assert document["save"] == {"inserted": 1, "duplicates": 0, "errors": 0}
    assert document["store_backend"] == "json"
    assert document["flags"]["ai_fallback"] is False
    assert document["ai_fallback"] is None


async def test_backend_closed_on_interrupt(tmp_path):
    """
    [Local] scrape urls: verifies a KeyboardInterrupt propagates but the backend is still closed.

    Scenario:
        Given csv.DictReader raising KeyboardInterrupt (Ctrl+C mid-run)
        When run() is called
        Then KeyboardInterrupt propagates out of run() (to main_sync, which exits 130) and
        backend.close() is still called once by run()'s finally block.

    Boundaries:
        - Covers the finally-closes-backend contract on interrupt; run() must not swallow Ctrl+C.

    On failure, first check:
        - The try/finally around the backend in run() and that KeyboardInterrupt is not caught.
    """
    csv_path = _write_csv(tmp_path, ["https://leanpub.com/a"])
    args = SimpleNamespace(input_file=str(csv_path), store_backend="json", ignore_robots=False)

    backend = MagicMock()
    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.csv.DictReader", side_effect=KeyboardInterrupt()),
        pytest.raises(KeyboardInterrupt),
    ):
        await run(args)

    backend.close.assert_called_once_with()


async def test_scrape_urls_returns_partial_when_any_failed(tmp_path):
    """
    [Local] scrape urls: verifies a run with at least one failed scrape returns PARTIAL.

    Scenario:
        Given a CSV with two leanpub URLs where one scrapes SUCCESS and one ends FAILED
        When run() is called
        Then run() returns exitcodes.PARTIAL, the one good book is saved, and the backend is closed.

    Boundaries:
        - A FAILED status is a failed item (exit-code contract); SUCCESS alone would be OK.

    On failure, first check:
        - The FAILURE_STATUSES tally feeding run()'s PARTIAL/OK decision.
    """
    csv_path = _write_csv(tmp_path, ["https://leanpub.com/good", "https://leanpub.com/bad"])
    args = SimpleNamespace(input_file=str(csv_path), store_backend="json", ignore_robots=False)

    backend = MagicMock()
    backend.save_books.return_value = SaveSummary(inserted=1, duplicates=0, errors=0)
    pw_factory, browser = _make_playwright_factory()
    book = {"title": "Good Book", "hash": "abc123"}
    ok = ScrapeResult(ScrapeStatus.SUCCESS, url="https://leanpub.com/good", site="leanpub", book=book)
    bad = ScrapeResult(ScrapeStatus.FAILED, url="https://leanpub.com/bad", site="leanpub", error="boom")
    scrape_book = AsyncMock(side_effect=[ok, bad])

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.async_playwright", pw_factory),
        patch(f"{MODULE}.scrape_book", scrape_book),
        patch(f"{MODULE}.save_other_links_to_csv"),
        patch(f"{MODULE}.write_failed_csv"),
    ):
        code = await run(args)

    assert code == exitcodes.PARTIAL
    backend.save_books.assert_called_once_with([book])
    browser.close.assert_awaited_once()
    backend.close.assert_called_once_with()


async def test_scrape_urls_returns_ok_when_only_duplicates(tmp_path):
    """
    [Local] scrape urls: verifies a run whose only non-success outcome is DUPLICATE returns OK.

    Scenario:
        Given a CSV with one leanpub URL that scrape_book reports as a DUPLICATE
        When run() is called
        Then run() returns exitcodes.OK (a duplicate is not a failure), save_books is never
        called, and the backend is closed.

    Boundaries:
        - DUPLICATE is explicitly excluded from FAILURE_STATUSES; a FAILED status is the partial case.

    On failure, first check:
        - run() must not count DUPLICATE toward the PARTIAL decision.
    """
    csv_path = _write_csv(tmp_path, ["https://leanpub.com/dup"])
    args = SimpleNamespace(input_file=str(csv_path), store_backend="json", ignore_robots=False)

    backend = MagicMock()
    pw_factory, browser = _make_playwright_factory()
    dup = ScrapeResult(ScrapeStatus.DUPLICATE, url="https://leanpub.com/dup", site="leanpub")
    scrape_book = AsyncMock(side_effect=[dup])

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.async_playwright", pw_factory),
        patch(f"{MODULE}.scrape_book", scrape_book),
        patch(f"{MODULE}.save_other_links_to_csv"),
        patch(f"{MODULE}.write_failed_csv"),
    ):
        code = await run(args)

    assert code == exitcodes.OK
    backend.save_books.assert_not_called()
    browser.close.assert_awaited_once()
    backend.close.assert_called_once_with()


@pytest.mark.parametrize(
    ("scrape_result", "expected_row"),
    [
        (
            ScrapeResult(ScrapeStatus.DUPLICATE, url="https://leanpub.com/x", site="leanpub"),
            {"url": "https://leanpub.com/x", "status": "DUPLICATE", "error": ""},
        ),
        (
            ScrapeResult(
                ScrapeStatus.FAILED, url="https://leanpub.com/x", site="leanpub", error="KeyError: 'BASE_URL'"
            ),
            {"url": "https://leanpub.com/x", "status": "FAILED", "error": "KeyError: 'BASE_URL'"},
        ),
        (
            ScrapeResult(
                ScrapeStatus.NOT_FOUND, url="https://leanpub.com/x", site="leanpub", error="404 page detected"
            ),
            {"url": "https://leanpub.com/x", "status": "NOT_FOUND", "error": "404 page detected"},
        ),
        (
            ScrapeResult(
                ScrapeStatus.TIMEOUT, url="https://leanpub.com/x", site="leanpub", error="timed out after 3 attempts"
            ),
            {"url": "https://leanpub.com/x", "status": "TIMEOUT", "error": "timed out after 3 attempts"},
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
        write_failed_csv receives exactly one {url, status, error} dict record whose
        values match the typed result.

    Boundaries:
        - Parametrized over all four non-success statuses; the SUCCESS path is a separate test.
        - The UNKNOWN_ERROR status no longer exists; every non-success carries a typed ScrapeStatus.

    On failure, first check:
        - The match/case result-type dispatch in run() and the {url, status, error} record assembly.
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
        patch(f"{MODULE}.write_failed_csv") as save_failed,
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
        patch(f"{MODULE}.write_failed_csv"),
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


async def test_scrape_urls_no_longer_sleeps_between_batches(tmp_path):
    """
    [Local] scrape urls: verifies the run scrapes every URL with no inter-batch sleep.

    Scenario:
        Given 11 leanpub URLs each returning SUCCESS
        When run() is called (bounded_gather has replaced the fixed batches + inter-batch sleep)
        Then scrape_book is awaited 11 times, asyncio.sleep is never awaited (the 3-5 s pause is
        gone - per-host pacing is the rate limiter's job now), and all books are saved together
        in input order.

    Boundaries:
        - Asserts the removal of the batch-boundary sleep; per-host pacing is tested separately
          in the rate-limiter unit tests.

    On failure, first check:
        - That run() uses bounded_gather(..., limit=concurrency) with no asyncio.sleep in the loop.
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
        patch(f"{MODULE}.write_failed_csv"),
    ):
        await run(args)

    # All 11 scraped once each, through the bounded-concurrency window.
    assert scrape_book.await_count == 11
    # No inter-batch pause remains.
    sleep.assert_not_awaited()
    backend.save_books.assert_called_once_with(books)
    backend.close.assert_called_once_with()
    browser.close.assert_awaited_once()


def test_other_link_csv_neutralizes_formula_cells(tmp_path):
    """
    [Local] scrape urls: verifies the other-links diagnostic writer quotes formula-leading cells.

    Scenario:
        Given an other-link whose URL starts with a spreadsheet formula trigger
        When save_other_links_to_csv writes the file
        Then that cell is written with a leading single quote and the ordinary URL is unchanged.

    Boundaries:
        - Covers other_links.csv; the shared failed-CSV writer's neutralization is tested in
          test_reporting.py.

    On failure, first check:
        - csv_safe_cell is applied to every cell in save_other_links_to_csv in commands/scrape_urls.py
    """
    other = tmp_path / "other_links.csv"
    save_other_links_to_csv(["@SUM(1)", "https://example.com/ok"], filename=str(other))

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
        ScrapeResult(
            ScrapeStatus.SUCCESS, url="https://leanpub.com/a", site="leanpub", book={"title": "A", "hash": "a"}
        ),
        ScrapeResult(
            ScrapeStatus.SUCCESS, url="https://leanpub.com/b", site="leanpub", book={"title": "B", "hash": "b"}
        ),
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
        patch(f"{MODULE}.write_failed_csv"),
        patch(f"{MODULE}.print_log", _record_print),
    ):
        await run(args)

    # Every scrape_book call is handed robots=None (the 6th positional arg).
    assert scrape_book.await_count == 2
    for call in scrape_book.await_args_list:
        assert call.args[5] is None

    warnings = [msg for msg, sev in printed if sev == "warning" and "--ignore-robots" in msg]
    assert len(warnings) == 1


# --------------------------------------------------------------------------- #
# --dry-run and --output-dir (subtask 05.0/04)
# --------------------------------------------------------------------------- #
def _dry_run_args(csv_path, **overrides) -> SimpleNamespace:
    """Build a scrape-urls Namespace with the 05.0/04 runtime fields set.

    :param csv_path: The input CSV path bound to input_file.
    :param overrides: Field overrides (e.g. dry_run, output_dir, json_store).
    :returns: The populated :class:`types.SimpleNamespace`.
    """
    base = {
        "input_file": str(csv_path),
        "store_backend": "json",
        "ignore_robots": False,
        "dry_run": False,
        "output_dir": ".",
        "json_store": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


async def test_dry_run_never_calls_save_books(tmp_path):
    """
    [Local] scrape urls: verifies a --dry-run run saves nothing yet still wires the backend for dedup.

    Scenario:
        Given a CSV with one leanpub URL that scrape_book resolves as a SUCCESS and --dry-run set
        When run() is called
        Then save_books is never called, the "DRY RUN - nothing was saved." line is printed, and the
             storage backend is still handed to scrape_book (so inline dedup reads can run).

    Boundaries:
        - scrape_book is mocked, so the inline dedup read is proven by the backend being passed down,
          not by a real read; the search command covers the read-call assertion directly.

    On failure, first check:
        - The runtime.dry_run branch at the end of run() that skips the save_books call.
        - That the storage backend is still the 4th positional arg to scrape_book.
    """
    csv_path = _write_csv(tmp_path, ["https://leanpub.com/good"])
    args = _dry_run_args(csv_path, dry_run=True, output_dir=str(tmp_path))

    backend = MagicMock()
    pw_factory, browser = _make_playwright_factory()
    book = {"title": "Good Book", "site": "leanpub", "hash": "abc123"}
    result = ScrapeResult(ScrapeStatus.SUCCESS, url="https://leanpub.com/good", site="leanpub", book=book)
    scrape_book = AsyncMock(side_effect=[result])
    printed: list[tuple[str, str]] = []

    def _record_print(message, severity="info", *args, **kwargs):
        """Capture (message, severity) for every print_log call."""
        printed.append((message, severity))

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.async_playwright", pw_factory),
        patch(f"{MODULE}.scrape_book", scrape_book),
        patch(f"{MODULE}.save_other_links_to_csv"),
        patch(f"{MODULE}.write_failed_csv"),
        patch(f"{MODULE}.print_log", _record_print),
    ):
        await run(args)

    backend.save_books.assert_not_called()
    backend.close.assert_called_once_with()
    browser.close.assert_awaited_once()
    assert any(msg == "DRY RUN - nothing was saved." for msg, _ in printed)
    # The backend is still threaded into scrape_book (4th positional arg) for inline dedup reads.
    assert scrape_book.call_args.args[3] is backend


async def test_dry_run_leaves_store_byte_identical(tmp_path, isolated_local_store):
    """
    [Local] scrape urls: verifies a --dry-run run leaves the json store file byte-identical.

    Scenario:
        Given a pre-populated books.json and a run that scrapes one SUCCESS book with --dry-run set,
            backed by a real JsonBackend over the isolated store
        When run() is called
        Then the store file's bytes (sha256 before and after) are unchanged - nothing was persisted.

    Boundaries:
        - Uses a real JsonBackend over isolated_local_store (never the repo-root books.json); only
          Playwright and scrape_book are mocked.

    On failure, first check:
        - That the dry-run branch skips save_books so the store's atomic write never runs.
    """
    isolated_local_store.write_text('[{"hash": "existing", "title": "Old", "site": "leanpub"}]', encoding="utf-8")
    digest_before = hashlib.sha256(isolated_local_store.read_bytes()).hexdigest()

    csv_path = _write_csv(tmp_path, ["https://leanpub.com/good"])
    args = _dry_run_args(csv_path, dry_run=True, output_dir=str(tmp_path))

    backend = JsonBackend()
    pw_factory, _browser = _make_playwright_factory()
    book = {"title": "New Book", "site": "leanpub", "hash": "brandnew"}
    result = ScrapeResult(ScrapeStatus.SUCCESS, url="https://leanpub.com/good", site="leanpub", book=book)
    scrape_book = AsyncMock(side_effect=[result])

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.async_playwright", pw_factory),
        patch(f"{MODULE}.scrape_book", scrape_book),
        patch(f"{MODULE}.save_other_links_to_csv"),
        patch(f"{MODULE}.write_failed_csv"),
    ):
        await run(args)

    digest_after = hashlib.sha256(isolated_local_store.read_bytes()).hexdigest()
    assert digest_after == digest_before


async def test_output_dir_created_and_used_for_all_diagnostics(tmp_path):
    """
    [Local] scrape urls: verifies --output-dir is created and receives every diagnostic CSV.

    Scenario:
        Given a CSV with one scrapable leanpub URL (resolved FAILED) and one example.com "other" URL,
            with --output-dir pointing at a not-yet-existing nested directory
        When run() is called with the real CSV writers
        Then the nested directory is created and both failed_books.csv and other_links.csv land in it.

    Boundaries:
        - The CSV writers are NOT mocked here (the point is where they write); scrape_book is.

    On failure, first check:
        - The Path(output_dir).mkdir(parents=True) call and the directory= kwarg on both writers.
    """
    csv_path = _write_csv(tmp_path, ["https://leanpub.com/x", "https://example.com/other"])
    out_dir = tmp_path / "nested" / "reports"
    args = _dry_run_args(csv_path, output_dir=str(out_dir))

    backend = MagicMock()
    pw_factory, _browser = _make_playwright_factory()
    result = ScrapeResult(ScrapeStatus.FAILED, url="https://leanpub.com/x", site="leanpub", error="boom")
    scrape_book = AsyncMock(side_effect=[result])

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.async_playwright", pw_factory),
        patch(f"{MODULE}.scrape_book", scrape_book),
    ):
        await run(args)

    assert out_dir.is_dir()
    assert (out_dir / "failed_books.csv").exists()
    assert (out_dir / "other_links.csv").exists()


# --------------------------------------------------------------------------- #
# LLM extraction fallback start-up validation (task 04.1, subtask 05)
# --------------------------------------------------------------------------- #
def _ai_args(csv_path: Path, **overrides) -> SimpleNamespace:
    """Build a scrape-urls Namespace with the --ai-fallback flags set to defaults."""
    base = {
        "input_file": str(csv_path),
        "store_backend": "json",
        "ignore_robots": False,
        "ai_fallback": True,
        "ai_fallback_max_pages": 20,
        "model": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


async def test_missing_api_key_with_flag_fails_before_scraping(tmp_path, monkeypatch):
    """
    [Local] scrape-urls: --ai-fallback with no ANTHROPIC_API_KEY exits FATAL before any scraping (EXT-7).

    Scenario:
        Given --ai-fallback set and ANTHROPIC_API_KEY unset in the environment
        And load_dotenv patched to a no-op so a real .env cannot supply the key
        When run() is called
        Then it returns exitcodes.FATAL and resolve_store_backend is never reached (fails at start-up)

    Boundaries:
        - The start-up create_ai_fallback call precedes resolve_store_backend and the scraping loop

    On failure, first check:
        - The --ai-fallback start-up block in run(), or create_ai_fallback's resolve_api_key.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    csv_path = _write_csv(tmp_path, ["https://www.amazon.com/dp/B0ABCDEFGH/"])
    with (
        patch(f"{MODULE}.load_dotenv"),
        patch(f"{MODULE}.resolve_store_backend") as resolve,
    ):
        code = await run(_ai_args(csv_path))

    assert code == exitcodes.FATAL
    resolve.assert_not_called()


async def test_empty_model_id_with_flag_fails_before_scraping(tmp_path, monkeypatch):
    """
    [Local] scrape-urls: --ai-fallback with an empty --model exits FATAL at start-up.

    Scenario:
        Given --ai-fallback set, a present ANTHROPIC_API_KEY, and --model as whitespace
        When run() is called
        Then it returns exitcodes.FATAL and resolve_store_backend is never reached

    Boundaries:
        - An empty/whitespace model ID is rejected by resolve_model even when the key is present

    On failure, first check:
        - create_ai_fallback's resolve_model call, or the FATAL return in run().
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-present")
    csv_path = _write_csv(tmp_path, ["https://www.amazon.com/dp/B0ABCDEFGH/"])
    with (
        patch(f"{MODULE}.load_dotenv"),
        patch(f"{MODULE}.resolve_store_backend") as resolve,
    ):
        code = await run(_ai_args(csv_path, model="   "))

    assert code == exitcodes.FATAL
    resolve.assert_not_called()
