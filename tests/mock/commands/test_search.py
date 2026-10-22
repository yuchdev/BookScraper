"""Unit/mock tests for bookscraper.commands.search.

resolve_store_backend, the Leanpub search API, and the Leanpub detail scraper are
all mocked, so no network and no real ~/.bookscrapper or books.json is touched. The
CSV helper here is the DictWriter-based one (distinct from scrape_urls.py's).
"""

import ast
import asyncio
import csv
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bookscraper import exitcodes
from bookscraper.backends.storage import SaveSummary
from bookscraper.commands import search
from bookscraper.commands.search import resolve_search_targets, run, save_failed_urls_to_csv
from bookscraper.scraping.results import ScrapeResult, ScrapeStatus

MODULE = "bookscraper.commands.search"
# The Leanpub search API now lives behind LeanpubApiProvider, which imports the function into the
# search_providers namespace; patch it there (where the provider looks it up), not in commands.search.
PROVIDERS_MODULE = "bookscraper.scraping.search_providers"


def _leanpub_book(slug: str, title: str) -> dict:
    """Build a minimal Leanpub book dict for search command tests."""
    return {
        "site": "leanpub",
        "title": title,
        "book_id": f"id-{slug}",
        "slug": slug,
        "authors": ["Some Author"],
    }


# --------------------------------------------------------------------------- #
# save_failed_urls_to_csv  (search variant: csv.DictWriter, list[dict])
# --------------------------------------------------------------------------- #
def test_save_failed_urls_empty_creates_no_file(tmp_path):
    """
    [Local] search: verifies that an empty list passed to save_failed_urls_to_csv creates no file.

    Scenario:
        Given an empty list passed to the search command's save_failed_urls_to_csv
        When the function is called
        Then no file is created at the target path.

    Boundaries:
        - This is the search variant (DictWriter); the scrape_urls variant (csv.writer) is
          in test_scrape_urls.py.

    On failure, first check:
        - The empty-list guard in the search command's save_failed_urls_to_csv.
    """
    target = tmp_path / "failed_urls.csv"
    save_failed_urls_to_csv([], filename=str(target))
    assert not target.exists()


def test_save_failed_urls_writes_header_and_dict_rows(tmp_path):
    """
    [Local] search: verifies that two dict items are written as a proper DictWriter CSV.

    Scenario:
        Given two dicts each with url/site/error keys
        When save_failed_urls_to_csv writes the file
        Then csv.DictReader can read back the correct url/site/error values for each row.

    Boundaries:
        - DictWriter-based variant (search.py); the tuple-based scrape_urls variant is separate.

    On failure, first check:
        - The fieldnames list and DictWriter.writerow call in save_failed_urls_to_csv.
    """
    target = tmp_path / "failed_urls.csv"
    items = [
        {"url": "https://leanpub.com/a", "site": "leanpub", "error": "boom"},
        {"url": "https://leanpub.com/b", "site": "leanpub", "error": "kaboom"},
    ]
    save_failed_urls_to_csv(items, filename=str(target))

    with open(target, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    assert [r["url"] for r in rows] == ["https://leanpub.com/a", "https://leanpub.com/b"]
    assert [r["error"] for r in rows] == ["boom", "kaboom"]
    assert rows[0]["site"] == "leanpub"


def test_save_failed_urls_missing_key_uses_restval(tmp_path):
    """
    [Local] search: verifies that a dict missing the "error" key is written with an empty string.

    Scenario:
        Given a dict with url and site but no "error" key
        When save_failed_urls_to_csv writes the file
        Then the row is written successfully with error == "" (DictWriter restval default).

    Boundaries:
        - Tests the restval="" behavior; extra-key ValueError is a separate test.

    On failure, first check:
        - The restval parameter on the DictWriter constructor in save_failed_urls_to_csv.
    """
    target = tmp_path / "failed_urls.csv"
    # No "error" key => restval="" fills it in, no exception.
    save_failed_urls_to_csv(
        [{"url": "https://leanpub.com/a", "site": "leanpub"}],
        filename=str(target),
    )

    with open(target, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    assert rows[0]["url"] == "https://leanpub.com/a"
    assert rows[0]["error"] == ""


def test_save_failed_urls_row_valueerror_is_per_item_and_loop_continues(tmp_path):
    """
    [Local] search: verifies that a per-row ValueError skips only that row and the loop continues.

    Scenario:
        Given three dicts where the middle one has an extra field triggering DictWriter
        ValueError (extrasaction="raise" default)
        When save_failed_urls_to_csv is called
        Then the first and third rows are written; the bad middle row is skipped.

    Boundaries:
        - Covers the per-row ValueError handler; csv.Error is a separate test.

    On failure, first check:
        - The per-row except ValueError handler inside the writerow loop.
    """
    target = tmp_path / "failed_urls.csv"
    # The middle item has an extra field => DictWriter.writerow raises ValueError
    # (extrasaction defaults to "raise"); it must be skipped, not abort the loop.
    items = [
        {"url": "https://leanpub.com/good1", "site": "leanpub", "error": "e1"},
        {"url": "https://leanpub.com/bad", "site": "leanpub", "error": "e2", "extra": "nope"},
        {"url": "https://leanpub.com/good2", "site": "leanpub", "error": "e3"},
    ]
    save_failed_urls_to_csv(items, filename=str(target))

    with open(target, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    urls = [r["url"] for r in rows]
    assert urls == ["https://leanpub.com/good1", "https://leanpub.com/good2"]


def test_save_failed_urls_row_generic_exception_is_per_item_and_loop_continues(tmp_path):
    """
    [Local] search: verifies that a per-row csv.Error skips only that row and the loop continues.

    Scenario:
        Given three items where writerow for the middle one raises csv.Error
        When save_failed_urls_to_csv is called
        Then the first and third rows are written; the bad middle row is skipped.

    Boundaries:
        - Covers the per-row csv.Error handler; ValueError is a separate test.

    On failure, first check:
        - The per-row except csv.Error / Exception handler inside the writerow loop.
    """
    target = tmp_path / "failed_urls.csv"
    items = [
        {"url": "https://leanpub.com/good1", "site": "leanpub", "error": "e1"},
        {"url": "https://leanpub.com/bad", "site": "leanpub", "error": "e2"},
        {"url": "https://leanpub.com/good2", "site": "leanpub", "error": "e3"},
    ]

    real_writer_factory = csv.DictWriter

    def _make_writer(csvfile, **kwargs) -> Any:
        """Build a DictWriter whose writerow raises csv.Error for the 'bad' URL."""
        writer = real_writer_factory(csvfile, **kwargs)
        original_writerow = writer.writerow

        def _writerow(row) -> Any:
            """Raise csv.Error for the bad URL, else delegate to the real writerow."""
            if row.get("url") == "https://leanpub.com/bad":
                # A non-ValueError csv.Error hits the inner per-row guard; the loop
                # must keep going and still write the remaining rows.
                raise csv.Error("row serialization exploded")
            return original_writerow(row)

        writer.writerow = _writerow
        return writer

    with patch(f"{MODULE}.csv.DictWriter", side_effect=_make_writer):
        save_failed_urls_to_csv(items, filename=str(target))

    with open(target, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    assert [r["url"] for r in rows] == ["https://leanpub.com/good1", "https://leanpub.com/good2"]


def test_save_failed_urls_ioerror_is_swallowed(tmp_path):
    """
    [Local] search: verifies that an IOError on file open is swallowed without propagating.

    Scenario:
        Given builtins.open patched to raise IOError("no space left")
        When save_failed_urls_to_csv is called with a non-empty list
        Then no exception reaches the caller and no file is created.

    Boundaries:
        - Best-effort write contract; csv.Error at construction is a separate test.

    On failure, first check:
        - The outer try/except IOError / OSError handler in save_failed_urls_to_csv.
    """
    target = tmp_path / "failed_urls.csv"
    with patch("builtins.open", side_effect=IOError("no space left")):
        save_failed_urls_to_csv(
            [{"url": "u", "site": "leanpub", "error": "e"}],
            filename=str(target),
        )
    assert not target.exists()


def test_save_failed_urls_generic_exception_is_swallowed(tmp_path):
    """
    [Local] search: verifies that a csv.Error at DictWriter construction is swallowed.

    Scenario:
        Given csv.DictWriter patched to raise csv.Error("boom") on construction
        When save_failed_urls_to_csv is called with a non-empty list
        Then no exception propagates to the caller.

    Boundaries:
        - Covers the outer csv.Error / Exception handler at DictWriter construction; the
          IOError/open path is a separate test.

    On failure, first check:
        - The outer try/except around DictWriter construction in save_failed_urls_to_csv.
    """
    target = tmp_path / "failed_urls.csv"
    # DictWriter construction itself raises a csv.Error => the outer guard catches it.
    with patch(f"{MODULE}.csv.DictWriter", side_effect=csv.Error("boom")):
        save_failed_urls_to_csv(
            [{"url": "u", "site": "leanpub", "error": "e"}],
            filename=str(target),
        )
    # File was opened (truncated) but nothing meaningful written / no propagation.


# --------------------------------------------------------------------------- #
# run()
# --------------------------------------------------------------------------- #
async def test_isbn13_duplicate_marked_duplicate_not_saved():
    """
    [Local] search: verifies a scraped record whose ISBN-13 is already stored is treated as a duplicate and never saved.

    Scenario:
        Given one fresh candidate (passing the pre-scrape leanpub dedup) whose detail scrape returns a
        SUCCESS book carrying an isbn13 that backend.book_exists_by_isbn13 reports already stored
        When run() is called
        Then book_exists_by_isbn13 is consulted with that isbn13, save_books is never called, and the
        run still exits OK - a duplicate is not a failure (task 03.3 subtask 05).

    Boundaries:
        - Post-detail cross-site dedup only; the pre-scrape leanpub check already passed and the
          detail-stage filter is relaxed to 0 ISBNs for leanpub.

    On failure, first check:
        - The ``book_exists_by_isbn13`` guard in run()'s SUCCESS branch and its ``continue`` before
          ``scraped_books_data.append``.
    """
    args = SimpleNamespace(store_backend="json", ignore_robots=False)

    fresh = _leanpub_book("fresh-book", "Fresh Book")

    backend = MagicMock()
    backend.leanpub_book_exists = MagicMock(return_value=False)
    backend.book_exists_by_isbn13 = MagicMock(return_value=True)

    search_api = AsyncMock(return_value=[fresh])
    detail = AsyncMock(
        return_value=ScrapeResult(
            ScrapeStatus.SUCCESS,
            url="https://leanpub.com/api/v1/cache/books/fresh-book.json",
            site="leanpub",
            book={"title": "Fresh Book", "site": "leanpub", "hash": "h1", "isbn13": "9781491908419"},
        )
    )

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.SEARCH_QUERIES", ["Python"]),
        patch(f"{PROVIDERS_MODULE}.get_leanpub_search_results_via_api", search_api),
        patch(f"{MODULE}.get_leanpub_book_details", detail),
        patch(f"{MODULE}.save_failed_urls_to_csv"),
    ):
        code = await run(args)

    # A store duplicate is not a failure.
    assert code == exitcodes.OK
    backend.book_exists_by_isbn13.assert_called_once_with("9781491908419")
    # The book is NOT saved: scraped_books_data stays empty, so save_books is never called.
    backend.save_books.assert_not_called()
    backend.close.assert_called_once_with()


async def test_run_cross_site_duplicates_collapse_before_detail_scrape():
    """
    [Local] search: verifies within-run cross-site grouping scrapes a repeated book's detail only once.

    Scenario:
        Given a search that returns two candidates with the same title and first author but different
        slugs (both fresh past the pre-scrape leanpub dedup, both passing the search-stage filter)
        When run() is called
        Then group_candidates collapses them to one survivor, so get_leanpub_book_details is awaited
        exactly once and only that one book is saved.

    Boundaries:
        - Exercises run()'s group_candidates step and its dropped-candidate logging loop; single site
          (leanpub) is enough since the grouping keys on normalized title+author, not on site.

    On failure, first check:
        - The ``survivors, dropped_pairs = group_candidates(survivors)`` step in run() and the detail
          task list built from the post-grouping survivors.
    """
    args = SimpleNamespace(store_backend="json", ignore_robots=False)

    first = _leanpub_book("edition-a", "Effective Modern C++")
    second = _leanpub_book("edition-b", "Effective Modern C++: 42 Specific Ways")

    backend = MagicMock()
    backend.save_books.return_value = SaveSummary(inserted=1, duplicates=0, errors=0)
    backend.leanpub_book_exists = MagicMock(return_value=False)
    backend.book_exists_by_isbn13 = MagicMock(return_value=False)

    search_api = AsyncMock(return_value=[first, second])
    detail = AsyncMock(
        return_value=ScrapeResult(
            ScrapeStatus.SUCCESS,
            url="https://leanpub.com/api/v1/cache/books/edition-a.json",
            site="leanpub",
            book={"title": "Effective Modern C++", "site": "leanpub", "hash": "h1"},
        )
    )

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.SEARCH_QUERIES", ["C++"]),
        patch(f"{PROVIDERS_MODULE}.get_leanpub_search_results_via_api", search_api),
        patch(f"{MODULE}.get_leanpub_book_details", detail),
        patch(f"{MODULE}.save_failed_urls_to_csv"),
    ):
        code = await run(args)

    assert code == exitcodes.OK
    # Two candidates collapsed to one: only the first-seen survivor is scraped and saved.
    detail.assert_awaited_once()
    _, kwargs = detail.call_args
    assert kwargs["url"].endswith("/edition-a.json")
    backend.save_books.assert_called_once()
    backend.close.assert_called_once_with()


async def test_run_dedup_only_new_book_proceeds_to_detail_scrape():
    """
    [Local] search: verifies that only non-duplicate books proceed to detail scrape.

    Scenario:
        Given search returns two books where leanpub_book_exists is True for the first and
        False for the second
        When run() is called
        Then get_leanpub_book_details is awaited exactly once with the fresh book's URL,
        and save_books is called with only the fresh book's data.

    Boundaries:
        - Pre-scrape dedup only; hash-based dedup at save time is the backend's responsibility.

    On failure, first check:
        - The leanpub_book_exists check and the URL construction for get_leanpub_book_details.
    """
    args = SimpleNamespace(store_backend="json", ignore_robots=False)

    existing = _leanpub_book("existing-book", "Existing Book")
    fresh = _leanpub_book("fresh-book", "Fresh Book")

    backend = MagicMock()
    backend.save_books.return_value = SaveSummary(inserted=1, duplicates=0, errors=0)
    # First book already in DB, second is new.
    backend.leanpub_book_exists = MagicMock(side_effect=[True, False])

    search_api = AsyncMock(return_value=[existing, fresh])
    detail = AsyncMock(
        return_value=ScrapeResult(
            ScrapeStatus.SUCCESS,
            url="https://leanpub.com/api/v1/cache/books/fresh-book.json",
            site="leanpub",
            book={"title": "Fresh Book", "site": "leanpub", "hash": "h1"},
        )
    )

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.SEARCH_QUERIES", ["Python"]),
        patch(f"{PROVIDERS_MODULE}.get_leanpub_search_results_via_api", search_api),
        patch(f"{MODULE}.get_leanpub_book_details", detail),
        patch(f"{MODULE}.save_failed_urls_to_csv"),
    ):
        code = await run(args)

    # An all-success run (one duplicate skipped pre-scrape) is OK, not a failure.
    assert code == exitcodes.OK
    # Only the non-duplicate book is scraped in detail.
    detail.assert_awaited_once()
    _, kwargs = detail.call_args
    assert kwargs["url"].endswith("/fresh-book.json")

    backend.save_books.assert_called_once()
    (saved,), _ = backend.save_books.call_args
    assert saved == [{"title": "Fresh Book", "site": "leanpub", "hash": "h1"}]
    backend.close.assert_called_once_with()


async def test_run_detail_results_partition_into_saved_and_failed():
    """
    [Local] search: verifies that SUCCESS results are saved and non-success results go to the CSV.

    Scenario:
        Given three candidates whose detail scrapes return SUCCESS, FAILED, and RuntimeError
        (the last simulating gather(return_exceptions=True) behaviour)
        When run() is called
        Then only the SUCCESS book is passed to save_books, and both the typed FAILED result
        and the raw RuntimeError appear in the failed-attempts CSV (2 entries).

    Boundaries:
        - Tests the partition logic for all three result shapes: SUCCESS, typed non-success,
          and raw exception.

    On failure, first check:
        - The result-partitioning loop in run() and the fallback branch for raw exceptions.
    """
    args = SimpleNamespace(store_backend="json", ignore_robots=False)

    good = _leanpub_book("good", "Good Book")
    boom = _leanpub_book("boom", "Boom Book")
    weird = _leanpub_book("weird", "Weird Book")

    backend = MagicMock()
    backend.save_books.return_value = SaveSummary(inserted=1, duplicates=0, errors=0)
    backend.leanpub_book_exists = MagicMock(return_value=False)

    search_api = AsyncMock(return_value=[good, boom, weird])
    # SUCCESS ScrapeResult => saved; non-success ScrapeResult => failed (typed status/error);
    # a raw Exception from gather(return_exceptions=True) => failed via the fallback branch.
    detail = AsyncMock(
        side_effect=[
            ScrapeResult(
                ScrapeStatus.SUCCESS,
                url="https://leanpub.com/api/v1/cache/books/good.json",
                site="leanpub",
                book={"title": "Good Book", "site": "leanpub", "hash": "ok"},
            ),
            ScrapeResult(
                ScrapeStatus.FAILED,
                url="https://leanpub.com/api/v1/cache/books/boom.json",
                site="leanpub",
                error="detail scrape blew up",
            ),
            RuntimeError("weird crash"),
        ]
    )
    saved_failed = MagicMock()

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.SEARCH_QUERIES", ["Python"]),
        patch(f"{PROVIDERS_MODULE}.get_leanpub_search_results_via_api", search_api),
        patch(f"{MODULE}.get_leanpub_book_details", detail),
        patch(f"{MODULE}.save_failed_urls_to_csv", saved_failed),
    ):
        code = await run(args)

    # At least one detail scrape failed, so the run is PARTIAL.
    assert code == exitcodes.PARTIAL
    # Only the SUCCESS result's book is persisted.
    backend.save_books.assert_called_once_with([{"title": "Good Book", "site": "leanpub", "hash": "ok"}])
    backend.close.assert_called_once_with()

    # The typed FAILED result and the raw Exception both land in the failed-attempts CSV.
    saved_failed.assert_called_once()
    (failed_arg,), _ = saved_failed.call_args
    assert len(failed_arg) == 2
    failed_titles = {f["title"] for f in failed_arg}
    assert failed_titles == {"Boom Book", "Weird Book"}
    boom_entry = next(f for f in failed_arg if f["title"] == "Boom Book")
    assert "detail scrape blew up" in boom_entry["error"]


async def test_search_failed_record_uses_real_book_url():
    """
    [Local] search: verifies search failed record uses real book url.

    Scenario:
        a Leanpub detail scrape fails, producing a non-success ScrapeResult.

    Boundaries:
        - the detail helper returns a typed FAILED result; the failed-attempts record must carry the result's real ``url`` (the resolved ``book_url``) and ``error``, not the old ``"url": "N/A"`` that came from reading a ``url`` key Leanpub candidates never had.

    On failure, first check:
        - the diagnostic CSV again records ``N/A`` for every Leanpub failure, erasing the URL an operator needs to retry it.
    """
    args = SimpleNamespace(store_backend="json", ignore_robots=False)

    candidate = _leanpub_book("broken-book", "Broken Book")

    backend = MagicMock()
    backend.leanpub_book_exists = MagicMock(return_value=False)

    book_url = "https://leanpub.com/api/v1/cache/books/broken-book.json"
    search_api = AsyncMock(return_value=[candidate])
    detail = AsyncMock(return_value=ScrapeResult(ScrapeStatus.FAILED, url=book_url, site="leanpub", error="HTTP 500"))
    saved_failed = MagicMock()

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.SEARCH_QUERIES", ["Python"]),
        patch(f"{PROVIDERS_MODULE}.get_leanpub_search_results_via_api", search_api),
        patch(f"{MODULE}.get_leanpub_book_details", detail),
        patch(f"{MODULE}.save_failed_urls_to_csv", saved_failed),
    ):
        await run(args)

    backend.save_books.assert_not_called()
    saved_failed.assert_called_once()
    (failed_arg,), _ = saved_failed.call_args
    assert len(failed_arg) == 1
    record = failed_arg[0]
    assert record["url"] == book_url
    assert record["url"] != "N/A"
    assert record["error"] == "HTTP 500"


async def test_run_no_new_books_skips_save_but_closes_backend():
    """
    [Local] search: verifies that empty search results skip detail scraping but still close the backend.

    Scenario:
        Given the Leanpub search API returning an empty list
        When run() is called
        Then get_leanpub_book_details is never awaited, save_books is never called, and
        backend.close() is called exactly once.

    Boundaries:
        - Covers the empty-candidate-list fast path; non-empty but all-duplicate is separate.

    On failure, first check:
        - The "no candidates" guard and the backend.close() in run()'s finally block.
    """
    args = SimpleNamespace(store_backend="json", ignore_robots=False)

    backend = MagicMock()
    search_api = AsyncMock(return_value=[])
    detail = AsyncMock()

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.SEARCH_QUERIES", ["Python"]),
        patch(f"{PROVIDERS_MODULE}.get_leanpub_search_results_via_api", search_api),
        patch(f"{MODULE}.get_leanpub_book_details", detail),
        patch(f"{MODULE}.save_failed_urls_to_csv"),
    ):
        await run(args)

    detail.assert_not_awaited()
    backend.save_books.assert_not_called()
    backend.close.assert_called_once_with()


async def test_run_keyboard_interrupt_propagates_and_backend_closed():
    """
    [Local] search: verifies that a KeyboardInterrupt propagates and the backend is still closed.

    Scenario:
        Given the Leanpub search API raising KeyboardInterrupt
        When run() is called
        Then KeyboardInterrupt propagates out of run() (to main_sync, which exits 130), save_books
        is never called, and backend.close() is still called once by the finally block.

    Boundaries:
        - Covers that Ctrl+C is no longer swallowed by run(); generic exceptions are a separate test.

    On failure, first check:
        - run() must NOT catch KeyboardInterrupt, and backend.close() must be in run()'s finally block.
    """
    args = SimpleNamespace(store_backend="json", ignore_robots=False)

    backend = MagicMock()
    search_api = AsyncMock(side_effect=KeyboardInterrupt())

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.SEARCH_QUERIES", ["Python"]),
        patch(f"{PROVIDERS_MODULE}.get_leanpub_search_results_via_api", search_api),
        patch(f"{MODULE}.get_leanpub_book_details", AsyncMock()),
        patch(f"{MODULE}.save_failed_urls_to_csv"),
        pytest.raises(KeyboardInterrupt),
    ):
        # Must propagate: run() no longer swallows KeyboardInterrupt.
        await run(args)

    backend.save_books.assert_not_called()
    backend.close.assert_called_once_with()


async def test_run_generic_exception_is_caught_and_returns_fatal():
    """
    [Local] search: verifies that a RuntimeError from the search API is caught and returns FATAL.

    Scenario:
        Given the Leanpub search API raising RuntimeError("search API exploded")
        When run() is called
        Then run() returns exitcodes.FATAL, save_books is never called, and backend.close()
        is called once.

    Boundaries:
        - Covers the broad except Exception handler; KeyboardInterrupt is a separate test.

    On failure, first check:
        - The outer except Exception handler, its FATAL return, and backend.close() in run()'s finally block.
    """
    args = SimpleNamespace(store_backend="json", ignore_robots=False)

    backend = MagicMock()
    search_api = AsyncMock(side_effect=RuntimeError("search API exploded"))

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.SEARCH_QUERIES", ["Python"]),
        patch(f"{PROVIDERS_MODULE}.get_leanpub_search_results_via_api", search_api),
        patch(f"{MODULE}.get_leanpub_book_details", AsyncMock()),
        patch(f"{MODULE}.save_failed_urls_to_csv"),
    ):
        code = await run(args)

    assert code == exitcodes.FATAL
    backend.save_books.assert_not_called()
    backend.close.assert_called_once_with()


async def test_search_reuses_single_async_client():
    """
    [Local] search: verifies that one AsyncClient is constructed per run and reused for every book.

    Scenario:
        Given a run with three fresh (non-duplicate) Leanpub books and httpx.AsyncClient patched
        to count its constructions
        When run() is called
        Then httpx.AsyncClient is constructed exactly once, and every get_leanpub_book_details
        call is passed that same client instance as its client kwarg.

    Boundaries:
        - Pins the connection-reuse requirement (one pooled client per run, not one per book);
          the detail helper itself is mocked, so no real request is made.

    On failure, first check:
        - run() builds a new httpx.AsyncClient per book, or stops passing the shared client into
          get_leanpub_book_details.
    """
    args = SimpleNamespace(store_backend="json", ignore_robots=False)

    books = [_leanpub_book(f"book-{i}", f"Book {i}") for i in range(3)]

    backend = MagicMock()
    backend.save_books.return_value = SaveSummary(inserted=3, duplicates=0, errors=0)
    backend.leanpub_book_exists = MagicMock(return_value=False)

    search_api = AsyncMock(return_value=books)
    detail = AsyncMock(
        side_effect=[
            ScrapeResult(
                ScrapeStatus.SUCCESS,
                url=f"https://leanpub.com/api/v1/cache/books/book-{i}.json",
                site="leanpub",
                book={"title": f"Book {i}", "hash": f"h{i}"},
            )
            for i in range(3)
        ]
    )

    # One AsyncClient instance, reused as an async context manager across all three books.
    fake_client = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=fake_client)
    cm.__aexit__ = AsyncMock(return_value=False)
    client_factory = MagicMock(return_value=cm)

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.SEARCH_QUERIES", ["Python"]),
        patch(f"{PROVIDERS_MODULE}.get_leanpub_search_results_via_api", search_api),
        patch(f"{MODULE}.get_leanpub_book_details", detail),
        patch(f"{MODULE}.httpx.AsyncClient", client_factory),
        patch(f"{MODULE}.save_failed_urls_to_csv"),
    ):
        await run(args)

    # Exactly one client built for the whole run, reused across all three detail scrapes.
    client_factory.assert_called_once()
    assert detail.await_count == 3
    for call in detail.call_args_list:
        assert call.kwargs["client"] is fake_client


def test_search_module_uses_real_leanpub_only_sites():
    # Sanity guard: the run() tests monkeypatch SEARCH_QUERIES but rely on the real
    # single-site config; if this changes the run() tests may need revisiting.
    """
    [Local] search: verifies that SITES_TO_SCRAPE is exactly ["leanpub"].

    Scenario:
        Given the search module's module-level SITES_TO_SCRAPE constant
        When its value is read
        Then it equals ["leanpub"] (the single wired-up site).

    Boundaries:
        - Sanity guard: the run() tests monkeypatch SEARCH_QUERIES but rely on the real
          single-site config; if SITES_TO_SCRAPE changes, those tests need revisiting.

    On failure, first check:
        - scraping/parameters.py's SITES_TO_SCRAPE and any test that patches SEARCH_QUERIES.
    """
    assert search.SITES_TO_SCRAPE == ["leanpub"]


def test_save_failed_urls_keeps_rows_that_carry_a_title(tmp_path):
    """
    [Local] search: verifies real failure records (which carry a title) are written and formula cells neutralized.

    Scenario:
        Given failure records shaped like run() builds them (url/site/title/error) with one '=' leading title
        When save_failed_urls_to_csv writes the file
        Then every row is present with its title column and the formula-leading cell is prefixed with a quote.

    Boundaries:
        - Regression for the DictWriter fieldnames bug that silently dropped every row carrying a title key

    On failure, first check:
        - The fieldnames list in save_failed_urls_to_csv covers every key run() puts in a failure record
        - csv_safe_cell is applied to each value before writerow
    """
    target = tmp_path / "failed_urls.csv"
    items = [
        {"url": "https://leanpub.com/a", "site": "leanpub", "title": "=HYPERLINK(1)", "error": "HTTP 500"},
        {"url": "https://leanpub.com/b", "site": "leanpub", "title": "Book B", "error": "invalid JSON"},
    ]
    save_failed_urls_to_csv(items, filename=str(target))

    with open(target, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    assert [r["url"] for r in rows] == ["https://leanpub.com/a", "https://leanpub.com/b"]
    assert [r["title"] for r in rows] == ["'=HYPERLINK(1)", "Book B"]
    assert [r["error"] for r in rows] == ["HTTP 500", "invalid JSON"]


async def test_run_saves_books_off_the_event_loop_thread():
    """
    [Local] search: verifies save_books runs in a worker thread rather than on the event loop.

    Scenario:
        Given one fresh Leanpub book whose detail scrape succeeds and a backend whose save_books records its thread
        When run() is called
        Then save_books is called once on a thread other than the one running the event loop.

    Boundaries:
        - save_books is blocking (MongoDB insert_many or the JSON store's fsync) so it must use asyncio.to_thread

    On failure, first check:
        - The asyncio.to_thread(storage_backend.save_books, ...) call near the end of run() in commands/search.py
    """
    args = SimpleNamespace(store_backend="json", ignore_robots=False)
    loop_thread = threading.get_ident()
    save_threads = []

    def _save_books(books) -> SaveSummary:
        """Record the calling thread and report one insert."""
        save_threads.append(threading.get_ident())
        return SaveSummary(inserted=len(books), duplicates=0, errors=0)

    backend = MagicMock()
    backend.leanpub_book_exists = MagicMock(return_value=False)
    backend.save_books = MagicMock(side_effect=_save_books)
    detail = AsyncMock(
        return_value=ScrapeResult(
            ScrapeStatus.SUCCESS,
            url="https://leanpub.com/x",
            site="leanpub",
            book={"title": "X", "site": "leanpub", "hash": "h"},
        )
    )

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.SEARCH_QUERIES", ["Python"]),
        patch(
            f"{PROVIDERS_MODULE}.get_leanpub_search_results_via_api", AsyncMock(return_value=[_leanpub_book("x", "X")])
        ),
        patch(f"{MODULE}.get_leanpub_book_details", detail),
        patch(f"{MODULE}.save_failed_urls_to_csv"),
    ):
        await run(args)

    assert len(save_threads) == 1
    assert save_threads[0] != loop_thread


async def test_run_skips_search_result_without_slug():
    """
    [Local] search: verifies a Leanpub search result with no slug is skipped instead of crashing the run.

    Scenario:
        Given search results where one book has slug None and the other a real slug
        When run() is called
        Then only the book with a slug is checked for duplicates and detail-scraped.

    Boundaries:
        - Pre-scrape loop only; the API path for a real slug is covered by the dedup test

    On failure, first check:
        - The empty-slug guard before the SINGLE_BOOK_API URL is built in commands/search.py
    """
    args = SimpleNamespace(store_backend="json", ignore_robots=False)
    no_slug = _leanpub_book("ignored", "No Slug")
    no_slug["slug"] = None
    good = _leanpub_book("good-book", "Good Book")

    backend = MagicMock()
    backend.leanpub_book_exists = MagicMock(return_value=False)
    backend.save_books.return_value = SaveSummary(inserted=1, duplicates=0, errors=0)
    detail = AsyncMock(
        return_value=ScrapeResult(
            ScrapeStatus.SUCCESS, url="https://leanpub.com/g", site="leanpub", book={"title": "Good", "hash": "g"}
        )
    )

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.SEARCH_QUERIES", ["Python"]),
        patch(f"{PROVIDERS_MODULE}.get_leanpub_search_results_via_api", AsyncMock(return_value=[no_slug, good])),
        patch(f"{MODULE}.get_leanpub_book_details", detail),
        patch(f"{MODULE}.save_failed_urls_to_csv"),
    ):
        await run(args)

    backend.leanpub_book_exists.assert_called_once_with("id-good-book", "good-book")
    detail.assert_awaited_once()
    assert detail.call_args.kwargs["url"].endswith("/good-book.json")


async def test_search_detail_scrape_is_bounded():
    """
    [Local] search: verifies detail scraping runs through the bounded-concurrency window.

    Scenario:
        Given 12 new Leanpub candidates and a detail scraper instrumented to record the live
        in-flight count (default concurrency is 4)
        When run() is called
        Then every candidate is scraped once, the peak observed concurrency never exceeds 4 (the
        old unbounded gather(*all) is gone), and all 12 books are saved.

    Boundaries:
        - Exercises the bounded_gather wiring in search.run; the primitive itself is unit-tested
          in test_politeness.

    On failure, first check:
        - That run() scrapes details via bounded_gather(..., limit=concurrency) rather than a
          bare asyncio.gather over every task at once.
    """
    args = SimpleNamespace(store_backend="json", ignore_robots=False)
    candidates = [_leanpub_book(f"book-{i}", f"Book {i}") for i in range(12)]

    backend = MagicMock()
    backend.leanpub_book_exists = MagicMock(return_value=False)
    backend.save_books.return_value = SaveSummary(inserted=12, duplicates=0, errors=0)

    in_flight = 0
    peak = 0
    scraped = 0

    async def fake_detail(*, url, client, limiters, robots) -> ScrapeResult:
        """Record peak detail-scrape concurrency while occupying a slot then return SUCCESS."""
        nonlocal in_flight, peak, scraped
        in_flight += 1
        peak = max(peak, in_flight)
        scraped += 1
        await asyncio.sleep(0)
        in_flight -= 1
        return ScrapeResult(
            ScrapeStatus.SUCCESS, url=url, site="leanpub", book={"title": url, "site": "leanpub", "hash": url}
        )

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.SEARCH_QUERIES", ["Python"]),
        patch(f"{PROVIDERS_MODULE}.get_leanpub_search_results_via_api", AsyncMock(return_value=candidates)),
        patch(f"{MODULE}.get_leanpub_book_details", fake_detail),
        patch(f"{MODULE}.save_failed_urls_to_csv"),
    ):
        await run(args)

    assert scraped == 12
    assert peak == 4
    backend.save_books.assert_called_once()
    (saved_arg,), _ = backend.save_books.call_args
    assert len(saved_arg) == 12


# --------------------------------------------------------------------------- #
# resolve_search_targets  (subtask 05.0/02)
# --------------------------------------------------------------------------- #
def _targets_args(**overrides) -> SimpleNamespace:
    """Build a search Namespace for resolve_search_targets with all targeting attrs defaulted."""
    base = {"sites": None, "queries": None, "queries_file": None, "max_search_pages": None}
    base.update(overrides)
    return SimpleNamespace(**base)


def test_query_and_queries_file_are_merged_and_deduplicated(tmp_path):
    """
    [Local] search: resolve_search_targets merges --query and --queries-file (order-preserved, deduped).

    Scenario:
        Given --query ["Rust", "Go"] and a queries-file listing ["Go", "Python"]
        When resolve_search_targets resolves the queries
        Then the result is ["Rust", "Go", "Python"] - CLI queries first, then file queries, with the
        duplicate "Go" dropped after its first occurrence.

    Boundaries:
        - Real file read via tmp_path; no network, no backend
        - Only the query-merge path; empty-list and unsupported-site errors are separate tests

    On failure, first check:
        - The _resolve_queries ordering (CLI then file) and the seen-set de-duplication in search.py
    """
    qfile = tmp_path / "queries.txt"
    qfile.write_text("Go\nPython\n", encoding="utf-8")
    args = _targets_args(queries=["Rust", "Go"], queries_file=str(qfile))

    _, queries, _ = resolve_search_targets(args)

    assert queries == ["Rust", "Go", "Python"]


def test_queries_file_skips_comments_and_blanks(tmp_path):
    """
    [Local] search: resolve_search_targets skips blank lines and '#' comments in a queries file.

    Scenario:
        Given a queries file with comment lines, blank lines, and whitespace around real queries
        When resolve_search_targets resolves the queries (no --query given)
        Then only the stripped non-comment, non-blank queries remain, in file order.

    Boundaries:
        - Real file read via tmp_path; no network, no backend
        - Covers the comment/blank filter and strip; the merge-with-CLI path is a separate test

    On failure, first check:
        - The line.strip()/startswith("#")/blank filter in _read_queries_file in search.py
    """
    qfile = tmp_path / "queries.txt"
    qfile.write_text("# a comment\n\n  Rust  \n# another\nData Science\n\n", encoding="utf-8")
    args = _targets_args(queries_file=str(qfile))

    _, queries, _ = resolve_search_targets(args)

    assert queries == ["Rust", "Data Science"]


def test_defaults_fall_back_to_parameters_constants():
    """
    [Local] search: with no targeting flags, resolve_search_targets falls back to parameters.py constants.

    Scenario:
        Given a Namespace with sites/queries/queries_file None and max_search_pages None
        When resolve_search_targets resolves the targets
        Then sites equals SITES_TO_SCRAPE, queries equals SEARCH_QUERIES, and max_search_pages is 3.

    Boundaries:
        - Pure logic; reads the real module-level SITES_TO_SCRAPE/SEARCH_QUERIES (not patched)
        - The returned lists are fresh copies, not the module constants themselves

    On failure, first check:
        - The "fall back to SITES_TO_SCRAPE / SEARCH_QUERIES when unset" branches in search.py
        - The "max_search_pages or 3" default in resolve_search_targets
    """
    args = _targets_args()

    sites, queries, max_search_pages = resolve_search_targets(args)

    assert sites == list(search.SITES_TO_SCRAPE)
    assert queries == list(search.SEARCH_QUERIES)
    assert max_search_pages == 3


def test_unsupported_site_errors_clearly():
    """
    [Local] search: selecting a site with no wired provider exits 2 with a message naming supported sites.

    Scenario:
        Given --site packtpub (a valid site key but no search provider wired yet - leanpub and
        amazon are the only wired providers after subtask 03.3/02)
        When resolve_search_targets is called
        Then SystemExit(2) is raised and the error message names the unsupported site and a
        supported one (leanpub).

    Boundaries:
        - Pure logic; no network, no backend; print_log is patched to capture the message
        - A genuinely unknown site is rejected earlier by argparse choices, not here

    On failure, first check:
        - The PROVIDERS membership check and _target_error (exit 2) in search.py
    """
    args = _targets_args(sites=["packtpub"], queries=["Rust"])

    with patch(f"{MODULE}.print_log") as print_log, pytest.raises(SystemExit) as exc_info:
        resolve_search_targets(args)

    assert exc_info.value.code == 2
    message = " ".join(str(call.args[0]) for call in print_log.call_args_list)
    assert "packtpub" in message
    assert "leanpub" in message


def test_empty_resolved_query_list_errors(tmp_path):
    """
    [Local] search: an empty resolved query list exits 2.

    Scenario:
        Given a queries file that contains only comments and blanks (and no --query) so the merged
        query list is empty
        When resolve_search_targets is called
        Then SystemExit(2) is raised.

    Boundaries:
        - Real file read via tmp_path; print_log patched to keep the console quiet
        - Edge case guarding the "no queries" branch distinct from the unsupported-site branch

    On failure, first check:
        - The "if not queries" guard in resolve_search_targets in search.py
    """
    qfile = tmp_path / "empty.txt"
    qfile.write_text("# only a comment\n\n", encoding="utf-8")
    args = _targets_args(queries_file=str(qfile))

    with patch(f"{MODULE}.print_log"), pytest.raises(SystemExit) as exc_info:
        resolve_search_targets(args)

    assert exc_info.value.code == 2


async def test_max_search_pages_reaches_search_provider():
    """
    [Local] search: the resolved --max-search-pages reaches the Leanpub search provider as max_pages.

    Scenario:
        Given a search run with --query "Rust" and --max-search-pages 1
        When run() executes
        Then get_leanpub_search_results_via_api is awaited exactly once (one query, one site) with
        the query "Rust" and max_pages=1.

    Boundaries:
        - The provider is mocked, so no real pagination happens; this pins that the flag is wired
          through, not that pagination stops (that is search_utils' own unit coverage)

    On failure, first check:
        - run() passes max_pages=max_search_pages into get_leanpub_search_results_via_api
        - resolve_search_targets returns the parsed max_search_pages unchanged
    """
    args = SimpleNamespace(
        store_backend="json",
        ignore_robots=False,
        sites=None,
        queries=["Rust"],
        queries_file=None,
        max_search_pages=1,
    )

    backend = MagicMock()
    search_api = AsyncMock(return_value=[])

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{PROVIDERS_MODULE}.get_leanpub_search_results_via_api", search_api),
        patch(f"{MODULE}.get_leanpub_book_details", AsyncMock()),
        patch(f"{MODULE}.save_failed_urls_to_csv"),
    ):
        await run(args)

    search_api.assert_awaited_once()
    assert search_api.call_args.args[0] == "Rust"
    assert search_api.call_args.kwargs["max_pages"] == 1
    backend.close.assert_called_once_with()


# --------------------------------------------------------------------------- #
# --dry-run (subtask 05.0/04)
# --------------------------------------------------------------------------- #
def _dry_run_search_args(**overrides) -> SimpleNamespace:
    """Build a search Namespace carrying the 05.0/04 runtime fields.

    :param overrides: Field overrides (e.g. dry_run, output_dir, json_store).
    :returns: The populated :class:`types.SimpleNamespace`.
    """
    base = {
        "store_backend": "json",
        "ignore_robots": False,
        "dry_run": False,
        "output_dir": ".",
        "json_store": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


async def test_dry_run_never_calls_save_books():
    """
    [Local] search: verifies a --dry-run run saves nothing but still closes the backend.

    Scenario:
        Given a single non-duplicate Leanpub candidate whose detail scrape SUCCEEDS and --dry-run set
        When run() is called
        Then save_books is never called, the "DRY RUN - nothing was saved." line is printed, and the
             backend is still closed.

    Boundaries:
        - Search API and detail scraper mocked; only the save decision is under test.

    On failure, first check:
        - The runtime.dry_run branch at the end of run() that skips save_books.
    """
    args = _dry_run_search_args(dry_run=True)

    candidate = _leanpub_book("good-book", "Good Book")
    backend = MagicMock()
    backend.leanpub_book_exists = MagicMock(return_value=False)
    search_api = AsyncMock(return_value=[candidate])
    detail = AsyncMock(
        return_value=ScrapeResult(
            ScrapeStatus.SUCCESS,
            url="https://leanpub.com/api/v1/cache/books/good-book.json",
            site="leanpub",
            book={"title": "Good Book", "site": "leanpub", "hash": "ok"},
        )
    )
    printed: list[tuple[str, str]] = []

    def _record_print(message, severity="info", *args, **kwargs):
        """Capture (message, severity) for every print_log call."""
        printed.append((message, severity))

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.SEARCH_QUERIES", ["Python"]),
        patch(f"{PROVIDERS_MODULE}.get_leanpub_search_results_via_api", search_api),
        patch(f"{MODULE}.get_leanpub_book_details", detail),
        patch(f"{MODULE}.save_failed_urls_to_csv"),
        patch(f"{MODULE}.print_log", _record_print),
    ):
        await run(args)

    backend.save_books.assert_not_called()
    backend.close.assert_called_once_with()
    assert any(msg == "DRY RUN - nothing was saved." for msg, _ in printed)


async def test_dry_run_still_performs_dedup_reads():
    """
    [Local] search: verifies a --dry-run run still runs the pre-scrape duplicate-check read.

    Scenario:
        Given a single Leanpub candidate and --dry-run set
        When run() is called
        Then the backend's leanpub_book_exists dedup read is still called while save_books is not.

    Boundaries:
        - Proves "reads for dedup still run" directly, since the search command owns the read call
          (unlike scrape-urls, where scrape_book owns it).

    On failure, first check:
        - That the dry-run branch only gates save_books, leaving the dedup loop intact.
    """
    args = _dry_run_search_args(dry_run=True)

    candidate = _leanpub_book("good-book", "Good Book")
    backend = MagicMock()
    backend.leanpub_book_exists = MagicMock(return_value=False)
    search_api = AsyncMock(return_value=[candidate])
    detail = AsyncMock(
        return_value=ScrapeResult(
            ScrapeStatus.SUCCESS,
            url="https://leanpub.com/api/v1/cache/books/good-book.json",
            site="leanpub",
            book={"title": "Good Book", "site": "leanpub", "hash": "ok"},
        )
    )

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.SEARCH_QUERIES", ["Python"]),
        patch(f"{PROVIDERS_MODULE}.get_leanpub_search_results_via_api", search_api),
        patch(f"{MODULE}.get_leanpub_book_details", detail),
        patch(f"{MODULE}.save_failed_urls_to_csv"),
    ):
        await run(args)

    backend.leanpub_book_exists.assert_called_once_with("id-good-book", "good-book")
    backend.save_books.assert_not_called()


def test_search_run_has_no_site_specific_branches():
    """
    [Local] search: commands/search.py carries no "leanpub" string literal (no per-site branch).

    Scenario:
        Given the parsed AST of commands/search.py
        When every string-constant node is inspected
        Then none equals "leanpub" (case-insensitively), proving the run loop dispatches through the
        provider registry rather than a hard-coded per-site branch.

    Boundaries:
        - Pure source-AST check; identifiers like get_leanpub_book_details and prose comments are not
          string literals, so they do not count. A docstring mentioning "Leanpub" inside a longer
          sentence is one larger constant, never equal to the bare "leanpub".

    On failure, first check:
        - Any reintroduced `if site_name == "leanpub"` branch or bare "leanpub" literal in
          commands/search.py.
    """
    source = Path(search.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    offending = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.lower() == "leanpub"
    ]
    assert offending == []


async def test_filtered_books_written_to_csv_and_not_saved(tmp_path):
    """
    [Local] search: a book dropped by the detail-stage SCRAPE_FILTERS is written to filtered_books.csv and not saved.

    Scenario:
        Given two fresh Leanpub candidates whose detail scrapes both SUCCEED, where book A carries an
        ISBN-13 and book B carries none, under SCRAPE_FILTERS requiring min_isbns_required 1
        When run() is called
        Then only book A is passed to save_books, book B is absent from the saved batch, and
        filtered_books.csv names book B with its reason while book A is not in it.

    Boundaries:
        - Exercises the detail-stage filter and its CSV diagnostic; the rating stage is covered by the
          pure filters unit tests.

    On failure, first check:
        - The passes_detail_filters branch in run() and the save_filtered_books_to_csv call.
    """
    args = SimpleNamespace(store_backend="json", ignore_robots=False, output_dir=str(tmp_path))
    filters = {
        "enable_rating_filter": False,
        "min_rating": 3.5,
        "include_new_books_without_rating": True,
        "min_isbns_required": 1,
        "per_site": {},
    }

    book_a = _leanpub_book("book-a", "Book A")
    book_b = _leanpub_book("book-b", "Book B")

    backend = MagicMock()
    backend.save_books.return_value = SaveSummary(inserted=1, duplicates=0, errors=0)
    backend.leanpub_book_exists = MagicMock(side_effect=[False, False])
    # Book A carries an ISBN-13 and reaches the post-detail store check; it is fresh (not yet stored),
    # so the book survives to save_books and only the ISBN-less book B is filtered out.
    backend.book_exists_by_isbn13 = MagicMock(return_value=False)

    search_api = AsyncMock(return_value=[book_a, book_b])
    detail = AsyncMock(
        side_effect=[
            ScrapeResult(
                ScrapeStatus.SUCCESS,
                url="https://leanpub.com/api/v1/cache/books/book-a.json",
                site="leanpub",
                book={"title": "Book A", "site": "leanpub", "hash": "ha", "isbn10": None, "isbn13": "9780306406157"},
            ),
            ScrapeResult(
                ScrapeStatus.SUCCESS,
                url="https://leanpub.com/api/v1/cache/books/book-b.json",
                site="leanpub",
                book={"title": "Book B", "site": "leanpub", "hash": "hb", "isbn10": None, "isbn13": None},
            ),
        ]
    )

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.SEARCH_QUERIES", ["Python"]),
        patch(f"{MODULE}.SCRAPE_FILTERS", filters),
        patch(f"{PROVIDERS_MODULE}.get_leanpub_search_results_via_api", search_api),
        patch(f"{MODULE}.get_leanpub_book_details", detail),
        patch(f"{MODULE}.save_failed_urls_to_csv"),
    ):
        code = await run(args)

    # A filtered-out book is not a failure.
    assert code == exitcodes.OK
    backend.save_books.assert_called_once()
    (saved,), _ = backend.save_books.call_args
    saved_titles = {doc["title"] for doc in saved}
    assert saved_titles == {"Book A"}

    csv_path = tmp_path / "filtered_books.csv"
    assert csv_path.exists()
    with open(csv_path, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    titles = {row["title"] for row in rows}
    assert titles == {"Book B"}
    assert rows[0]["stage"] == "detail"
    assert "ISBN" in rows[0]["reason"]


async def test_no_filters_disables_both_stages(tmp_path):
    """
    [Local] search: --no-filters keeps a book that SCRAPE_FILTERS would otherwise drop and writes no filtered CSV.

    Scenario:
        Given a fresh Leanpub candidate whose detail scrape SUCCEEDS with no ISBNs, under
        SCRAPE_FILTERS requiring min_isbns_required 1, but with no_filters True on the run
        When run() is called
        Then the book is saved despite carrying no ISBN and no filtered_books.csv is written.

    Boundaries:
        - Exercises the runtime.no_filters bypass on the detail stage; the search stage bypass shares
          the same flag.

    On failure, first check:
        - The ``runtime.no_filters`` short-circuit guarding passes_detail_filters in run().
    """
    args = SimpleNamespace(store_backend="json", ignore_robots=False, output_dir=str(tmp_path), no_filters=True)
    filters = {
        "enable_rating_filter": True,
        "min_rating": 3.5,
        "include_new_books_without_rating": True,
        "min_isbns_required": 1,
        "per_site": {},
    }

    book_b = _leanpub_book("book-b", "Book B")
    backend = MagicMock()
    backend.save_books.return_value = SaveSummary(inserted=1, duplicates=0, errors=0)
    backend.leanpub_book_exists = MagicMock(side_effect=[False])

    search_api = AsyncMock(return_value=[book_b])
    detail = AsyncMock(
        return_value=ScrapeResult(
            ScrapeStatus.SUCCESS,
            url="https://leanpub.com/api/v1/cache/books/book-b.json",
            site="leanpub",
            book={"title": "Book B", "site": "leanpub", "hash": "hb", "isbn10": None, "isbn13": None},
        )
    )

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.SEARCH_QUERIES", ["Python"]),
        patch(f"{MODULE}.SCRAPE_FILTERS", filters),
        patch(f"{PROVIDERS_MODULE}.get_leanpub_search_results_via_api", search_api),
        patch(f"{MODULE}.get_leanpub_book_details", detail),
        patch(f"{MODULE}.save_failed_urls_to_csv"),
    ):
        code = await run(args)

    assert code == exitcodes.OK
    backend.save_books.assert_called_once()
    (saved,), _ = backend.save_books.call_args
    assert {doc["title"] for doc in saved} == {"Book B"}
    assert not (tmp_path / "filtered_books.csv").exists()
