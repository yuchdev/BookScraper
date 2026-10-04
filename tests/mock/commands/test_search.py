"""Unit/mock tests for bookscraper.commands.search.

resolve_store_backend, the Leanpub search API, and the Leanpub detail scraper are
all mocked, so no network and no real ~/.bookscrapper or books.json is touched. The
CSV helper here is the DictWriter-based one (distinct from scrape_urls.py's).
"""

import csv
from types import SimpleNamespace
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
def test_save_failed_urls_empty_creates_no_file(tmp_path) -> None:
    """
    [Local] search: verifies save failed urls empty creates no file.

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
        - Recent changes in code paths exercised by search
    """
    target = tmp_path / "failed_urls.csv"
    save_failed_urls_to_csv([], filename=str(target))
    assert not target.exists()


def test_save_failed_urls_writes_header_and_dict_rows(tmp_path) -> None:
    """
    [Local] search: verifies save failed urls writes header and dict rows.

    Scenario:
        Given test inputs and fixtures for save failed urls writes header and dict rows
        When test_save_failed_urls_writes_header_and_dict_rows executes the target flow
        Then the expected outcome for save failed urls writes header and dict rows is confirmed.

    Boundaries:
        - Focus: save failed urls writes header and dict rows
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to save failed urls writes header and dict rows
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search
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


def test_save_failed_urls_missing_key_uses_restval(tmp_path) -> None:
    """
    [Local] search: verifies save failed urls missing key uses restval.

    Scenario:
        Given test inputs and fixtures for save failed urls missing key uses restval
        When test_save_failed_urls_missing_key_uses_restval executes the target flow
        Then the expected outcome for save failed urls missing key uses restval is confirmed.

    Boundaries:
        - Focus: save failed urls missing key uses restval
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to save failed urls missing key uses restval
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search
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


def test_save_failed_urls_row_valueerror_is_per_item_and_loop_continues(tmp_path) -> None:
    """
    [Local] search: verifies save failed urls row valueerror is per item and loop continues.

    Scenario:
        Given test inputs and fixtures for save failed urls row valueerror is per item and loop continues
        When test_save_failed_urls_row_valueerror_is_per_item_and_loop_continues executes the target flow
        Then the expected outcome for save failed urls row valueerror is per item and loop continues is confirmed.

    Boundaries:
        - Focus: save failed urls row valueerror is per item and loop continues
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to save failed urls row valueerror is per item and loop continues
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search
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


def test_save_failed_urls_row_generic_exception_is_per_item_and_loop_continues(tmp_path) -> None:
    """
    [Local] search: verifies save failed urls row generic exception is per item and loop continues.

    Scenario:
        Given test inputs and fixtures for save failed urls row generic exception is per item and loop continues
        When test_save_failed_urls_row_generic_exception_is_per_item_and_loop_continues executes the target flow
        Then the expected outcome for save failed urls row generic exception is per item and loop continues is confirmed.

    Boundaries:
        - Focus: save failed urls row generic exception is per item and loop continues
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to save failed urls row generic exception is per item and loop continues
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search
    """
    target = tmp_path / "failed_urls.csv"
    items = [
        {"url": "https://leanpub.com/good1", "site": "leanpub", "error": "e1"},
        {"url": "https://leanpub.com/bad", "site": "leanpub", "error": "e2"},
        {"url": "https://leanpub.com/good2", "site": "leanpub", "error": "e3"},
    ]

    real_writer_factory = csv.DictWriter

    def _make_writer(csvfile, **kwargs):
        """Build a DictWriter whose writerow raises csv.Error for the 'bad' URL."""
        writer = real_writer_factory(csvfile, **kwargs)
        original_writerow = writer.writerow

        def _writerow(row):
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


def test_save_failed_urls_ioerror_is_swallowed(tmp_path) -> None:
    """
    [Local] search: verifies save failed urls ioerror is swallowed.

    Scenario:
        Given test inputs and fixtures for save failed urls ioerror is swallowed
        When test_save_failed_urls_ioerror_is_swallowed executes the target flow
        Then the expected outcome for save failed urls ioerror is swallowed is confirmed.

    Boundaries:
        - Focus: save failed urls ioerror is swallowed
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to save failed urls ioerror is swallowed
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search
    """
    target = tmp_path / "failed_urls.csv"
    with patch("builtins.open", side_effect=IOError("no space left")):
        save_failed_urls_to_csv(
            [{"url": "u", "site": "leanpub", "error": "e"}],
            filename=str(target),
        )
    assert not target.exists()


def test_save_failed_urls_generic_exception_is_swallowed(tmp_path) -> None:
    """
    [Local] search: verifies save failed urls generic exception is swallowed.

    Scenario:
        Given test inputs and fixtures for save failed urls generic exception is swallowed
        When test_save_failed_urls_generic_exception_is_swallowed executes the target flow
        Then the expected outcome for save failed urls generic exception is swallowed is confirmed.

    Boundaries:
        - Focus: save failed urls generic exception is swallowed
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to save failed urls generic exception is swallowed
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search
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
async def test_run_dedup_only_new_book_proceeds_to_detail_scrape() -> None:
    """
    [Local] search: verifies run dedup only new book proceeds to detail scrape.

    Scenario:
        Given test inputs and fixtures for run dedup only new book proceeds to detail scrape
        When test_run_dedup_only_new_book_proceeds_to_detail_scrape executes the target flow
        Then the expected outcome for run dedup only new book proceeds to detail scrape is confirmed.

    Boundaries:
        - Focus: run dedup only new book proceeds to detail scrape
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to run dedup only new book proceeds to detail scrape
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search
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


async def test_run_detail_results_partition_into_saved_and_failed() -> None:
    """
    [Local] search: verifies run detail results partition into saved and failed.

    Scenario:
        Given test inputs and fixtures for run detail results partition into saved and failed
        When test_run_detail_results_partition_into_saved_and_failed executes the target flow
        Then the expected outcome for run detail results partition into saved and failed is confirmed.

    Boundaries:
        - Focus: run detail results partition into saved and failed
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to run detail results partition into saved and failed
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search
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


async def test_search_failed_record_uses_real_book_url() -> None:
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


async def test_run_no_new_books_skips_save_but_closes_backend() -> None:
    """
    [Local] search: verifies run no new books skips save but closes backend.

    Scenario:
        Given test inputs and fixtures for run no new books skips save but closes backend
        When test_run_no_new_books_skips_save_but_closes_backend executes the target flow
        Then the expected outcome for run no new books skips save but closes backend is confirmed.

    Boundaries:
        - Focus: run no new books skips save but closes backend
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to run no new books skips save but closes backend
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search
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


async def test_run_keyboard_interrupt_is_caught_and_backend_closed() -> None:
    """
    [Local] search: verifies run keyboard interrupt is caught and backend closed.

    Scenario:
        Given test inputs and fixtures for run keyboard interrupt is caught and backend closed
        When test_run_keyboard_interrupt_is_caught_and_backend_closed executes the target flow
        Then the expected outcome for run keyboard interrupt is caught and backend closed is confirmed.

    Boundaries:
        - Focus: run keyboard interrupt is caught and backend closed
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to run keyboard interrupt is caught and backend closed
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search
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


async def test_run_generic_exception_is_caught_and_backend_closed() -> None:
    """
    [Local] search: verifies run generic exception is caught and backend closed.

    Scenario:
        Given test inputs and fixtures for run generic exception is caught and backend closed
        When test_run_generic_exception_is_caught_and_backend_closed executes the target flow
        Then the expected outcome for run generic exception is caught and backend closed is confirmed.

    Boundaries:
        - Focus: run generic exception is caught and backend closed
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to run generic exception is caught and backend closed
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search
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


def test_search_module_uses_real_leanpub_only_sites() -> None:
    # Sanity guard: the run() tests monkeypatch SEARCH_QUERIES but rely on the real
    # single-site config; if this changes the run() tests may need revisiting.
    """
    [Local] search: verifies search module uses real leanpub only sites.

    Scenario:
        Given test inputs and fixtures for search module uses real leanpub only sites
        When test_search_module_uses_real_leanpub_only_sites executes the target flow
        Then the expected outcome for search module uses real leanpub only sites is confirmed.

    Boundaries:
        - Focus: search module uses real leanpub only sites
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to search module uses real leanpub only sites
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search
    """
    assert search.SITES_TO_SCRAPE == ["leanpub"]
