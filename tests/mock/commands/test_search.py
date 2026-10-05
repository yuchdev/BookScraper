"""Unit/mock tests for bookscraper.commands.search.

resolve_store_backend, the Leanpub search API, and the Leanpub detail scraper are
all mocked, so no network and no real ~/.bookscrapper or books.json is touched. The
CSV helper here is the DictWriter-based one (distinct from scrape_urls.py's).
"""

import csv
import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from bookscraper.backends.storage import SaveSummary
from bookscraper.commands import search
from bookscraper.commands.search import run, save_failed_urls_to_csv
from bookscraper.scraping.results import ScrapeResult, ScrapeStatus

MODULE = "bookscraper.commands.search"


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
    args = SimpleNamespace(store_backend="json")

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
            book={"title": "Fresh Book", "hash": "h1"},
        )
    )

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.SEARCH_QUERIES", ["Python"]),
        patch(f"{MODULE}.get_leanpub_search_results_via_api", search_api),
        patch(f"{MODULE}.get_leanpub_book_details", detail),
        patch(f"{MODULE}.save_failed_urls_to_csv"),
    ):
        await run(args)

    # Only the non-duplicate book is scraped in detail.
    detail.assert_awaited_once()
    _, kwargs = detail.call_args
    assert kwargs["url"].endswith("/fresh-book.json")

    backend.save_books.assert_called_once()
    (saved,), _ = backend.save_books.call_args
    assert saved == [{"title": "Fresh Book", "hash": "h1"}]
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
    args = SimpleNamespace(store_backend="json")

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
                book={"title": "Good Book", "hash": "ok"},
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
        patch(f"{MODULE}.get_leanpub_search_results_via_api", search_api),
        patch(f"{MODULE}.get_leanpub_book_details", detail),
        patch(f"{MODULE}.save_failed_urls_to_csv", saved_failed),
    ):
        await run(args)

    # Only the SUCCESS result's book is persisted.
    backend.save_books.assert_called_once_with([{"title": "Good Book", "hash": "ok"}])
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
    args = SimpleNamespace(store_backend="json")

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
        patch(f"{MODULE}.get_leanpub_search_results_via_api", search_api),
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
    args = SimpleNamespace(store_backend="json")

    backend = MagicMock()
    search_api = AsyncMock(return_value=[])
    detail = AsyncMock()

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.SEARCH_QUERIES", ["Python"]),
        patch(f"{MODULE}.get_leanpub_search_results_via_api", search_api),
        patch(f"{MODULE}.get_leanpub_book_details", detail),
        patch(f"{MODULE}.save_failed_urls_to_csv"),
    ):
        await run(args)

    detail.assert_not_awaited()
    backend.save_books.assert_not_called()
    backend.close.assert_called_once_with()


async def test_run_keyboard_interrupt_is_caught_and_backend_closed():
    """
    [Local] search: verifies that a KeyboardInterrupt is caught and the backend is still closed.

    Scenario:
        Given the Leanpub search API raising KeyboardInterrupt
        When run() is called
        Then run() returns normally (KeyboardInterrupt does not propagate), save_books is
        never called, and backend.close() is called once.

    Boundaries:
        - Covers the KeyboardInterrupt catch; generic exceptions are a separate test.

    On failure, first check:
        - The except KeyboardInterrupt handler and the backend.close() in run()'s finally block.
    """
    args = SimpleNamespace(store_backend="json")

    backend = MagicMock()
    search_api = AsyncMock(side_effect=KeyboardInterrupt())

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.SEARCH_QUERIES", ["Python"]),
        patch(f"{MODULE}.get_leanpub_search_results_via_api", search_api),
        patch(f"{MODULE}.get_leanpub_book_details", AsyncMock()),
        patch(f"{MODULE}.save_failed_urls_to_csv"),
    ):
        # Must not propagate: run() catches KeyboardInterrupt and finishes cleanly.
        await run(args)

    backend.save_books.assert_not_called()
    backend.close.assert_called_once_with()


async def test_run_generic_exception_is_caught_and_backend_closed():
    """
    [Local] search: verifies that a RuntimeError from the search API is caught and the backend closed.

    Scenario:
        Given the Leanpub search API raising RuntimeError("search API exploded")
        When run() is called
        Then run() returns normally, save_books is never called, and backend.close()
        is called once.

    Boundaries:
        - Covers the broad except Exception handler; KeyboardInterrupt is a separate test.

    On failure, first check:
        - The outer except Exception handler and backend.close() in run()'s finally block.
    """
    args = SimpleNamespace(store_backend="json")

    backend = MagicMock()
    search_api = AsyncMock(side_effect=RuntimeError("search API exploded"))

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.SEARCH_QUERIES", ["Python"]),
        patch(f"{MODULE}.get_leanpub_search_results_via_api", search_api),
        patch(f"{MODULE}.get_leanpub_book_details", AsyncMock()),
        patch(f"{MODULE}.save_failed_urls_to_csv"),
    ):
        await run(args)

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
    args = SimpleNamespace(store_backend="json")

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
        patch(f"{MODULE}.get_leanpub_search_results_via_api", search_api),
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
    args = SimpleNamespace(store_backend="json")
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
            ScrapeStatus.SUCCESS, url="https://leanpub.com/x", site="leanpub", book={"title": "X", "hash": "h"}
        )
    )

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.SEARCH_QUERIES", ["Python"]),
        patch(f"{MODULE}.get_leanpub_search_results_via_api", AsyncMock(return_value=[_leanpub_book("x", "X")])),
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
    args = SimpleNamespace(store_backend="json")
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
        patch(f"{MODULE}.get_leanpub_search_results_via_api", AsyncMock(return_value=[no_slug, good])),
        patch(f"{MODULE}.get_leanpub_book_details", detail),
        patch(f"{MODULE}.save_failed_urls_to_csv"),
    ):
        await run(args)

    backend.leanpub_book_exists.assert_called_once_with("id-good-book", "good-book")
    detail.assert_awaited_once()
    assert detail.call_args.kwargs["url"].endswith("/good-book.json")

