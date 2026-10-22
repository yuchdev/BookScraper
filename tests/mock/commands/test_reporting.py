"""Unit/mock tests for bookscraper.commands._reporting.

The shared run-summary/diagnostics helpers the two scrape commands use: the single dict-based
failed-records CSV writer (reconciled from the old tuple-based scrape_urls copy and dict-based
search copy), the status-breakdown formatter, and the elapsed-time helper. All file I/O is real
via tmp_path; no network and no real ~/.bookscrapper or books.json is touched.
"""

import csv
import time
from collections import Counter
from typing import Any
from unittest.mock import patch

from bookscraper.commands import _reporting, scrape_urls, search
from bookscraper.commands._reporting import elapsed_since, format_status_breakdown, write_failed_csv
from bookscraper.scraping.results import ScrapeStatus

MODULE = "bookscraper.commands._reporting"

SEARCH_FIELDNAMES = ["url", "site", "title", "error"]
SCRAPE_URLS_FIELDNAMES = ["url", "status", "error"]


# --------------------------------------------------------------------------- #
# the single shared writer
# --------------------------------------------------------------------------- #
def test_single_failed_csv_writer_used_by_both_commands():
    """
    [Local] reporting: verifies both scrape commands use the one shared write_failed_csv.

    Scenario:
        Given the search and scrape_urls command modules
        When their write_failed_csv attribute is compared to _reporting.write_failed_csv
        Then all three are the same function object - there is exactly one failed-CSV writer.

    Boundaries:
        - Identity check only; the writer's behavior is covered by the other tests here. Guards the
          subtask success criterion that no per-command save_failed_urls_to_csv survives.

    On failure, first check:
        - That both commands import write_failed_csv from _reporting rather than defining their own.
    """
    assert search.write_failed_csv is _reporting.write_failed_csv
    assert scrape_urls.write_failed_csv is _reporting.write_failed_csv


def test_empty_rows_creates_no_file(tmp_path):
    """
    [Local] reporting: verifies an empty rows list writes no file.

    Scenario:
        Given an empty list passed to write_failed_csv
        When the function is called
        Then no file is created at the target path.

    Boundaries:
        - Only the empty-guard early return is exercised.

    On failure, first check:
        - The ``if not rows`` guard in write_failed_csv.
    """
    target = tmp_path / "failed.csv"
    write_failed_csv([], fieldnames=SEARCH_FIELDNAMES, filename=str(target))
    assert not target.exists()


def test_writes_search_shape_dict_rows(tmp_path):
    """
    [Local] reporting: verifies search-shape dict rows are written under their fieldnames.

    Scenario:
        Given two url/site/title/error dict rows and the search fieldnames
        When write_failed_csv writes the file
        Then csv.DictReader reads back the header and both rows' values in order.

    Boundaries:
        - The search variant's columns; the scrape_urls columns are a separate test.

    On failure, first check:
        - The DictWriter fieldnames/writerow loop in write_failed_csv.
    """
    target = tmp_path / "failed_urls.csv"
    rows = [
        {"url": "https://leanpub.com/a", "site": "leanpub", "title": "A", "error": "boom"},
        {"url": "https://leanpub.com/b", "site": "leanpub", "title": "B", "error": "kaboom"},
    ]
    write_failed_csv(rows, fieldnames=SEARCH_FIELDNAMES, filename=str(target))

    with open(target, encoding="utf-8", newline="") as fh:
        read_rows = list(csv.DictReader(fh))

    assert [r["url"] for r in read_rows] == ["https://leanpub.com/a", "https://leanpub.com/b"]
    assert [r["title"] for r in read_rows] == ["A", "B"]
    assert [r["error"] for r in read_rows] == ["boom", "kaboom"]


def test_writes_scrape_urls_shape_dict_rows(tmp_path):
    """
    [Local] reporting: verifies scrape_urls-shape dict rows yield the url/status/error header.

    Scenario:
        Given three url/status/error dict rows (DUPLICATE/NOT_FOUND/TIMEOUT) and the scrape_urls
        fieldnames
        When write_failed_csv writes the file
        Then the CSV header is ["url", "status", "error"] and each body row matches its record.

    Boundaries:
        - Pins that the reconciled dict-based writer reproduces the old tuple-based scrape_urls CSV
          exactly (behavior-preserving refactor).

    On failure, first check:
        - The fieldnames threaded from FAILED_BOOKS_FIELDNAMES in commands/scrape_urls.py.
    """
    target = tmp_path / "failed_books.csv"
    rows = [
        {"url": "https://leanpub.com/a", "status": "DUPLICATE", "error": ""},
        {"url": "https://www.amazon.com/dp/1", "status": "NOT_FOUND", "error": "404 page detected"},
        {"url": "https://www.oreilly.com/x", "status": "TIMEOUT", "error": "timed out after 3 attempts"},
    ]
    write_failed_csv(rows, fieldnames=SCRAPE_URLS_FIELDNAMES, filename=str(target))

    with open(target, encoding="utf-8", newline="") as fh:
        read_rows = list(csv.reader(fh))

    assert read_rows[0] == ["url", "status", "error"]
    assert read_rows[1:] == [[r["url"], r["status"], r["error"]] for r in rows]


def test_missing_key_uses_restval(tmp_path):
    """
    [Local] reporting: verifies a row missing a fieldname is filled with an empty string.

    Scenario:
        Given a row with url and site but no title or error key, under the search fieldnames
        When write_failed_csv writes the file
        Then the row is written with the missing columns empty (DictWriter restval="").

    Boundaries:
        - restval behavior only; an extra (unknown) key is the separate per-row ValueError test.

    On failure, first check:
        - The restval="" argument on the DictWriter constructor in write_failed_csv.
    """
    target = tmp_path / "failed_urls.csv"
    write_failed_csv(
        [{"url": "https://leanpub.com/a", "site": "leanpub"}],
        fieldnames=SEARCH_FIELDNAMES,
        filename=str(target),
    )

    with open(target, encoding="utf-8", newline="") as fh:
        read_rows = list(csv.DictReader(fh))

    assert read_rows[0]["url"] == "https://leanpub.com/a"
    assert read_rows[0]["title"] == ""
    assert read_rows[0]["error"] == ""


def test_row_valueerror_is_per_item_and_loop_continues(tmp_path):
    """
    [Local] reporting: verifies an extra-key ValueError skips only that row.

    Scenario:
        Given three rows where the middle one carries a key outside the fieldnames (DictWriter
        extrasaction="raise" default) under the search fieldnames
        When write_failed_csv is called
        Then the first and third rows are written and the bad middle row is skipped.

    Boundaries:
        - The per-row ValueError handler; a per-row csv.Error is the next test.

    On failure, first check:
        - The inner try/except ValueError around writerow in write_failed_csv.
    """
    target = tmp_path / "failed_urls.csv"
    rows = [
        {"url": "https://leanpub.com/good1", "site": "leanpub", "title": "G1", "error": "e1"},
        {"url": "https://leanpub.com/bad", "site": "leanpub", "title": "B", "error": "e2", "extra": "nope"},
        {"url": "https://leanpub.com/good2", "site": "leanpub", "title": "G2", "error": "e3"},
    ]
    write_failed_csv(rows, fieldnames=SEARCH_FIELDNAMES, filename=str(target))

    with open(target, encoding="utf-8", newline="") as fh:
        urls = [r["url"] for r in csv.DictReader(fh)]
    assert urls == ["https://leanpub.com/good1", "https://leanpub.com/good2"]


def test_row_csv_error_is_per_item_and_loop_continues(tmp_path):
    """
    [Local] reporting: verifies a per-row csv.Error skips only that row.

    Scenario:
        Given three rows and a DictWriter whose writerow raises csv.Error for the "bad" URL
        When write_failed_csv is called
        Then the first and third rows are written and the bad middle row is skipped.

    Boundaries:
        - The per-row csv.Error handler; the ValueError case is a separate test.

    On failure, first check:
        - The inner try/except csv.Error around writerow in write_failed_csv.
    """
    target = tmp_path / "failed_urls.csv"
    rows = [
        {"url": "https://leanpub.com/good1", "site": "leanpub", "title": "G1", "error": "e1"},
        {"url": "https://leanpub.com/bad", "site": "leanpub", "title": "B", "error": "e2"},
        {"url": "https://leanpub.com/good2", "site": "leanpub", "title": "G2", "error": "e3"},
    ]
    real_factory = csv.DictWriter

    def _make_writer(csvfile, **kwargs) -> Any:
        """Build a DictWriter whose writerow raises csv.Error for the 'bad' URL."""
        writer = real_factory(csvfile, **kwargs)
        original = writer.writerow

        def _writerow(row) -> Any:
            """Raise csv.Error for the bad URL, else delegate to the real writerow."""
            if row.get("url") == "https://leanpub.com/bad":
                raise csv.Error("row serialization exploded")
            return original(row)

        writer.writerow = _writerow
        return writer

    with patch(f"{MODULE}.csv.DictWriter", side_effect=_make_writer):
        write_failed_csv(rows, fieldnames=SEARCH_FIELDNAMES, filename=str(target))

    with open(target, encoding="utf-8", newline="") as fh:
        urls = [r["url"] for r in csv.DictReader(fh)]
    assert urls == ["https://leanpub.com/good1", "https://leanpub.com/good2"]


def test_open_ioerror_is_swallowed(tmp_path):
    """
    [Local] reporting: verifies an IOError on file open is swallowed.

    Scenario:
        Given builtins.open patched to raise OSError("no space left")
        When write_failed_csv is called with a non-empty list
        Then no exception reaches the caller and no file is created.

    Boundaries:
        - Best-effort write contract; a construction-time csv.Error is a separate test.

    On failure, first check:
        - The outer except IOError handler in write_failed_csv.
    """
    target = tmp_path / "failed.csv"
    with patch("builtins.open", side_effect=OSError("no space left")):
        write_failed_csv(
            [{"url": "u", "status": "FAILED", "error": "e"}],
            fieldnames=SCRAPE_URLS_FIELDNAMES,
            filename=str(target),
        )
    assert not target.exists()


def test_dictwriter_construction_error_is_swallowed(tmp_path):
    """
    [Local] reporting: verifies a csv.Error at DictWriter construction is swallowed.

    Scenario:
        Given csv.DictWriter patched to raise csv.Error("boom") on construction
        When write_failed_csv is called with a non-empty list
        Then no exception propagates to the caller.

    Boundaries:
        - The outer csv.Error handler; the open/IOError path is a separate test.

    On failure, first check:
        - The outer try/except csv.Error around DictWriter construction in write_failed_csv.
    """
    target = tmp_path / "failed.csv"
    with patch(f"{MODULE}.csv.DictWriter", side_effect=csv.Error("boom")):
        write_failed_csv(
            [{"url": "u", "status": "FAILED", "error": "e"}],
            fieldnames=SCRAPE_URLS_FIELDNAMES,
            filename=str(target),
        )


def test_formula_cells_are_neutralized(tmp_path):
    """
    [Local] reporting: verifies every cell is run through csv_safe_cell to neutralize formulas.

    Scenario:
        Given a scrape_urls-shape row whose error begins with a spreadsheet formula trigger
        When write_failed_csv writes the file
        Then that cell is written with a leading single quote and ordinary cells are unchanged.

    Boundaries:
        - Regression for the formula-injection guard shared by both commands' failed CSVs.

    On failure, first check:
        - The csv_safe_cell call inside the writerow comprehension in write_failed_csv.
    """
    target = tmp_path / "failed_books.csv"
    write_failed_csv(
        [{"url": "https://x/1", "status": "FAILED", "error": "=cmd|' /C calc'!A0"}],
        fieldnames=SCRAPE_URLS_FIELDNAMES,
        filename=str(target),
    )
    with open(target, encoding="utf-8", newline="") as fh:
        assert list(csv.reader(fh))[1] == ["https://x/1", "FAILED", "'=cmd|' /C calc'!A0"]


# --------------------------------------------------------------------------- #
# format_status_breakdown
# --------------------------------------------------------------------------- #
def test_format_status_breakdown_only_includes_seen_statuses():
    """
    [Local] reporting: verifies the breakdown names only statuses that occurred, in enum order.

    Scenario:
        Given a Counter with SUCCESS=2, TIMEOUT=1 and DUPLICATE=0
        When format_status_breakdown renders it
        Then the result is "SUCCESS=2, TIMEOUT=1" - zero-count statuses are omitted and the order
        follows the ScrapeStatus enum.

    Boundaries:
        - Pure formatting; no I/O. The empty-counter case is a separate test.

    On failure, first check:
        - The enum-order generator and the ``if status_counts[status]`` filter in
          format_status_breakdown.
    """
    counts = Counter({ScrapeStatus.SUCCESS: 2, ScrapeStatus.TIMEOUT: 1, ScrapeStatus.DUPLICATE: 0})
    assert format_status_breakdown(counts) == "SUCCESS=2, TIMEOUT=1"


def test_format_status_breakdown_empty_is_empty_string():
    """
    [Local] reporting: verifies an empty tally renders as the empty string.

    Scenario:
        Given an empty Counter
        When format_status_breakdown renders it
        Then it returns "".

    Boundaries:
        - Edge case guarding the join over no entries.

    On failure, first check:
        - The generator expression in format_status_breakdown over an empty Counter.
    """
    assert format_status_breakdown(Counter()) == ""


# --------------------------------------------------------------------------- #
# elapsed_since
# --------------------------------------------------------------------------- #
def test_elapsed_since_returns_nonnegative_duration():
    """
    [Local] reporting: verifies elapsed_since returns a non-negative seconds delta.

    Scenario:
        Given a start captured just before the call
        When elapsed_since is called with it
        Then the returned value is a float >= 0.

    Boundaries:
        - Wall-clock delta; no monotonic guarantees are asserted beyond non-negativity.

    On failure, first check:
        - The ``time.time() - start_time`` expression in elapsed_since.
    """
    start = time.time()
    delta = elapsed_since(start)
    assert isinstance(delta, float)
    assert delta >= 0
