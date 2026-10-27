"""Unit/mock tests for bookscraper.commands._reporting.

The shared run-summary/diagnostics helpers the two scrape commands use: the single dict-based
failed-records CSV writer (reconciled from the old tuple-based scrape_urls copy and dict-based
search copy), the status-breakdown formatter, and the elapsed-time helper. All file I/O is real
via tmp_path; no network and no real ~/.bookscrapper or books.json is touched.
"""

import csv
import json
import stat
import time
from collections import Counter
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from bookscraper.commands import _reporting, scrape_urls, search
from bookscraper.commands._reporting import RunReport, elapsed_since, format_status_breakdown, write_failed_csv
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


# --------------------------------------------------------------------------- #
# RunReport (task 07.0 subtask 03)
# --------------------------------------------------------------------------- #
def _fake_ai_runtime(**overrides) -> SimpleNamespace:
    """Build a duck-typed stand-in for AIFallbackRuntime with the attributes RunReport reads."""
    totals = overrides.pop(
        "totals",
        {
            "requests": 2,
            "replayed": 0,
            "input_tokens": 100,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "output_tokens": 30,
        },
    )
    defaults = {
        "cap": SimpleNamespace(used=2, hit=True),
        "filled": 3,
        "dropped": 1,
        "usage": SimpleNamespace(totals=lambda: totals),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_report_written_next_to_log_with_same_stem(tmp_path, monkeypatch):
    """
    [Local] reporting: verifies write() creates <log-stem>.report.json next to the run log.

    Scenario:
        Given book_utils.LOG_FILE_PATH pointed at tmp_path/bookscraper_X.log
        When a finalized RunReport.write() is called with no extra path
        Then tmp_path/bookscraper_X.report.json exists, is owner-only (0600), and parses to a
        schema-1 document carrying the command and exit code.

    Boundaries:
        - Real file I/O via tmp_path; no real ~/.bookscrapper is touched. The --report extra copy
          is a separate test.

    On failure, first check:
        - _default_report_path() deriving the sibling path from book_utils.LOG_FILE_PATH's stem.
    """
    log_path = tmp_path / "bookscraper_20260101_000000_42.log"
    monkeypatch.setattr(_reporting.book_utils, "LOG_FILE_PATH", log_path)

    report = RunReport("search", ["search", "--store-backend", "json"])
    report.finalize(0)
    report.write()

    report_path = tmp_path / "bookscraper_20260101_000000_42.report.json"
    assert report_path.exists()
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600
    document = json.loads(report_path.read_text(encoding="utf-8"))
    assert document["schema"] == 1
    assert document["command"] == "search"
    assert document["exit_code"] == 0
    assert document["duration_s"] is not None
    assert set(document["versions"]) == {"bookscraper", "python", "playwright", "chromium"}


def test_report_flag_writes_additional_copy(tmp_path, monkeypatch):
    """
    [Local] reporting: verifies write(extra_path) writes both the default and the extra copy.

    Scenario:
        Given a finalized RunReport and an explicit --report path
        When write(extra_path) is called
        Then both the log-sibling report and the extra copy exist and parse to the same document.

    Boundaries:
        - Both targets written atomically; a write failure on either is the best-effort test.

    On failure, first check:
        - The targets list (default + extra_path) in RunReport.write.
    """
    log_path = tmp_path / "bookscraper_run.log"
    monkeypatch.setattr(_reporting.book_utils, "LOG_FILE_PATH", log_path)
    extra = tmp_path / "out" / "run-report.json"

    report = RunReport("scrape-urls", ["scrape-urls", "--store-backend", "json"])
    report.finalize(0)
    report.write(str(extra))

    default_copy = json.loads((tmp_path / "bookscraper_run.report.json").read_text(encoding="utf-8"))
    extra_copy = json.loads(extra.read_text(encoding="utf-8"))
    assert default_copy == extra_copy
    assert extra_copy["command"] == "scrape-urls"


def test_report_counts_match_scrape_results():
    """
    [Local] reporting: verifies record_status tallies per-site status counts for the report.

    Scenario:
        Given SUCCESS x2 and DUPLICATE for amazon and BLOCKED for oreilly
        When they are recorded and the report is rendered
        Then per_site is {"amazon": {SUCCESS: 2, DUPLICATE: 1}, "oreilly": {BLOCKED: 1}}.

    Boundaries:
        - Pure accumulation; no I/O. A cron wrapper decides alert-or-not from per_site + exit_code.

    On failure, first check:
        - RunReport.record_status's per-site Counter keyed by ScrapeStatus.value.
    """
    report = RunReport("search", ["search"])
    report.record_status("amazon", ScrapeStatus.SUCCESS)
    report.record_status("amazon", ScrapeStatus.SUCCESS)
    report.record_status("amazon", ScrapeStatus.DUPLICATE)
    report.record_status("oreilly", ScrapeStatus.BLOCKED)

    per_site = report.to_dict()["per_site"]
    assert per_site == {
        "amazon": {"SUCCESS": 2, "DUPLICATE": 1},
        "oreilly": {"BLOCKED": 1},
    }


def test_report_argv_redacted():
    """
    [Local] reporting: verifies a secret passed on the command line is redacted in argv.

    Scenario:
        Given argv carrying a mongodb+srv URI with an inline password
        When a RunReport is built
        Then report.argv masks the userinfo (no plaintext password) and keeps the other tokens.

    Boundaries:
        - Only argv redaction; the redact() rule set is unit-tested in test_book_utils.

    On failure, first check:
        - The [redact(token) for token in argv] comprehension in RunReport.__init__.
    """
    secret_uri = "mongodb+srv://user:SuperSecret@cluster0.mongodb.net/db"
    report = RunReport("search", ["search", "--store-backend", "mongo", secret_uri])
    joined = " ".join(report.argv)
    assert "SuperSecret" not in joined
    assert "<redacted>" in joined
    assert "--store-backend" in report.argv


def test_report_ai_fallback_block_present_when_attached():
    """
    [Local] reporting: verifies an attached AI-fallback runtime yields the ai_fallback block.

    Scenario:
        Given a duck-typed runtime (2 requests, cap hit, 3 filled, 1 dropped, token totals)
        When attached and the report rendered
        Then ai_fallback carries requests/filled/dropped/cap_hit and the token totals dict - and no
        API key or prices.

    Boundaries:
        - Block shape only; the counts come straight from the runtime/UsageAccumulator.

    On failure, first check:
        - RunReport._ai_fallback_block reading cap.used/filled/dropped/cap.hit/usage.totals().
    """
    report = RunReport("scrape-urls", ["scrape-urls"])
    report.attach_ai_fallback(_fake_ai_runtime())
    block = report.to_dict()["ai_fallback"]
    assert block["requests"] == 2
    assert block["filled"] == 3
    assert block["dropped"] == 1
    assert block["cap_hit"] is True
    assert block["tokens"]["input_tokens"] == 100
    assert "key" not in block and "price" not in block


def test_report_ai_fallback_block_absent_when_not_run():
    """
    [Local] reporting: verifies the ai_fallback block is null when the fallback never ran.

    Scenario:
        Given a report with no AI-fallback runtime attached (or attached None)
        When rendered
        Then ai_fallback is None.

    Boundaries:
        - The None guard in _ai_fallback_block.

    On failure, first check:
        - attach_ai_fallback(None) and the None return in _ai_fallback_block.
    """
    report = RunReport("scrape-urls", ["scrape-urls"])
    assert report.to_dict()["ai_fallback"] is None
    report.attach_ai_fallback(None)
    assert report.to_dict()["ai_fallback"] is None


def test_report_write_is_best_effort_on_oserror(tmp_path, monkeypatch):
    """
    [Local] reporting: verifies a write OSError is swallowed with a warning, not raised.

    Scenario:
        Given atomic_write_bytes patched to raise OSError
        When RunReport.write() is called
        Then no exception propagates and print_log is called with a warning.

    Boundaries:
        - The write path's best-effort contract; main()'s finally has its own guard too.

    On failure, first check:
        - The try/except OSError around atomic_write_bytes in RunReport.write.
    """
    log_path = tmp_path / "bookscraper_run.log"
    monkeypatch.setattr(_reporting.book_utils, "LOG_FILE_PATH", log_path)
    report = RunReport("migrate", ["migrate"])
    report.finalize(0)

    with (
        patch(f"{MODULE}.atomic_write_bytes", side_effect=OSError("no space")),
        patch(f"{MODULE}.print_log") as mock_print,
    ):
        report.write()  # must not raise

    assert mock_print.called


def test_report_chromium_version_is_best_effort_null(monkeypatch):
    """
    [Local] reporting: verifies an unavailable Chromium version yields null, never a raise.

    Scenario:
        Given the chromium probe raising internally (simulated via a broken playwright import path)
        When _chromium_version() is called
        Then it returns None rather than propagating.

    Boundaries:
        - Only the chromium best-effort guard; the other versions come from importlib metadata.

    On failure, first check:
        - The broad except in _chromium_version returning None.
    """

    def _boom(*_args, **_kwargs):
        """Force the chromium probe's json.loads to blow up so the except branch is taken."""
        raise ValueError("boom")

    monkeypatch.setattr(_reporting.json, "loads", _boom)
    assert _reporting._chromium_version() is None
