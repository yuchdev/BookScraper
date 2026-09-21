"""Unit/mock tests for bookscraper.commands.search.

resolve_store_backend, the Leanpub search API, and the Leanpub detail scraper are
all mocked, so no network and no real ~/.bookscrapper or books.json is touched. The
CSV helper here is the DictWriter-based one (distinct from scrape_urls.py's).
"""

import csv
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bookscraper.commands import search
from bookscraper.commands.search import run, save_failed_urls_to_csv

MODULE = "bookscraper.commands.search"


def _leanpub_book(slug: str, title: str) -> dict:
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
    target = tmp_path / "failed_urls.csv"
    save_failed_urls_to_csv([], filename=str(target))
    assert not target.exists()


def test_save_failed_urls_writes_header_and_dict_rows(tmp_path) -> None:
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
    target = tmp_path / "failed_urls.csv"
    items = [
        {"url": "https://leanpub.com/good1", "site": "leanpub", "error": "e1"},
        {"url": "https://leanpub.com/bad", "site": "leanpub", "error": "e2"},
        {"url": "https://leanpub.com/good2", "site": "leanpub", "error": "e3"},
    ]

    real_writer_factory = csv.DictWriter

    def _make_writer(csvfile, **kwargs):
        writer = real_writer_factory(csvfile, **kwargs)
        original_writerow = writer.writerow

        def _writerow(row):
            if row.get("url") == "https://leanpub.com/bad":
                # A non-ValueError error hits the inner `except Exception` guard;
                # the loop must keep going and still write the remaining rows.
                raise RuntimeError("row serialization exploded")
            return original_writerow(row)

        writer.writerow = _writerow
        return writer

    with patch(f"{MODULE}.csv.DictWriter", side_effect=_make_writer):
        save_failed_urls_to_csv(items, filename=str(target))

    with open(target, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    assert [r["url"] for r in rows] == ["https://leanpub.com/good1", "https://leanpub.com/good2"]


def test_save_failed_urls_ioerror_is_swallowed(tmp_path) -> None:
    target = tmp_path / "failed_urls.csv"
    with patch("builtins.open", side_effect=IOError("no space left")):
        save_failed_urls_to_csv(
            [{"url": "u", "site": "leanpub", "error": "e"}],
            filename=str(target),
        )
    assert not target.exists()


def test_save_failed_urls_generic_exception_is_swallowed(tmp_path) -> None:
    target = tmp_path / "failed_urls.csv"
    # DictWriter construction itself explodes => outer `except Exception` catches it.
    with patch(f"{MODULE}.csv.DictWriter", side_effect=RuntimeError("boom")):
        save_failed_urls_to_csv(
            [{"url": "u", "site": "leanpub", "error": "e"}],
            filename=str(target),
        )
    # File was opened (truncated) but nothing meaningful written / no propagation.


# --------------------------------------------------------------------------- #
# run()
# --------------------------------------------------------------------------- #
async def test_run_dedup_only_new_book_proceeds_to_detail_scrape() -> None:
    args = SimpleNamespace(store_backend="json")

    existing = _leanpub_book("existing-book", "Existing Book")
    fresh = _leanpub_book("fresh-book", "Fresh Book")

    backend = MagicMock()
    # First book already in DB, second is new.
    backend.leanpub_book_exists = MagicMock(side_effect=[True, False])

    search_api = AsyncMock(return_value=[existing, fresh])
    detail = AsyncMock(return_value={"title": "Fresh Book", "hash": "h1"})

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
    args = SimpleNamespace(store_backend="json")

    good = _leanpub_book("good", "Good Book")
    boom = _leanpub_book("boom", "Boom Book")
    weird = _leanpub_book("weird", "Weird Book")

    backend = MagicMock()
    backend.leanpub_book_exists = MagicMock(return_value=False)

    search_api = AsyncMock(return_value=[good, boom, weird])
    # dict => saved; Exception => captured by gather(return_exceptions=True) => failed;
    # None => unexpected type => failed. None of these raise out of run().
    detail = AsyncMock(
        side_effect=[
            {"title": "Good Book", "hash": "ok"},
            RuntimeError("detail scrape blew up"),
            None,
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

    # Only the dict result is persisted.
    backend.save_books.assert_called_once_with([{"title": "Good Book", "hash": "ok"}])
    backend.close.assert_called_once_with()

    # The Exception and the None results both land in the failed-attempts CSV.
    saved_failed.assert_called_once()
    (failed_arg,), _ = saved_failed.call_args
    assert len(failed_arg) == 2
    failed_titles = {f["title"] for f in failed_arg}
    assert failed_titles == {"Boom Book", "Weird Book"}
    boom_entry = next(f for f in failed_arg if f["title"] == "Boom Book")
    assert "detail scrape blew up" in boom_entry["error"]


async def test_run_no_new_books_skips_save_but_closes_backend() -> None:
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
    assert search.SITES_TO_SCRAPE == ["leanpub"]
