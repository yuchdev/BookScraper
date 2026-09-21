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

from bookscraper.commands.scrape_urls import (
    identify_website,
    run,
    save_failed_urls_to_csv,
    save_other_links_to_csv,
)

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
    assert identify_website(url) == expected


# --------------------------------------------------------------------------- #
# save_failed_urls_to_csv  (scrape_urls variant: csv.writer, (url, status) tuples)
# --------------------------------------------------------------------------- #
def test_save_failed_urls_empty_creates_no_file(tmp_path) -> None:
    target = tmp_path / "failed_books.csv"
    save_failed_urls_to_csv([], filename=str(target))
    assert not target.exists()


def test_save_failed_urls_writes_header_and_rows(tmp_path) -> None:
    target = tmp_path / "failed_books.csv"
    rows = [
        ("https://leanpub.com/a", "DUPLICATE"),
        ("https://www.amazon.com/dp/1", "FAILED"),
        ("https://example.com/x", "UNKNOWN_ERROR"),
    ]
    save_failed_urls_to_csv(rows, filename=str(target))

    with open(target, encoding="utf-8", newline="") as fh:
        read_rows = list(csv.reader(fh))

    assert read_rows[0] == ["url", "status"]
    assert read_rows[1:] == [list(r) for r in rows]


def test_save_failed_urls_open_failure_is_swallowed(tmp_path) -> None:
    target = tmp_path / "failed_books.csv"
    with patch("builtins.open", side_effect=OSError("disk full")):
        # Must not propagate: the caller relies on best-effort diagnostic writes.
        save_failed_urls_to_csv([("u", "FAILED")], filename=str(target))
    assert not target.exists()


# --------------------------------------------------------------------------- #
# save_other_links_to_csv  (csv.writer, single "url" column)
# --------------------------------------------------------------------------- #
def test_save_other_links_empty_creates_no_file(tmp_path) -> None:
    target = tmp_path / "other_links.csv"
    save_other_links_to_csv([], filename=str(target))
    assert not target.exists()


def test_save_other_links_writes_header_and_rows(tmp_path) -> None:
    target = tmp_path / "other_links.csv"
    links = ["https://example.com/a", "https://example.org/b"]
    save_other_links_to_csv(links, filename=str(target))

    with open(target, encoding="utf-8", newline="") as fh:
        read_rows = list(csv.reader(fh))

    assert read_rows[0] == ["url"]
    assert read_rows[1:] == [[link] for link in links]


def test_save_other_links_open_failure_is_swallowed(tmp_path) -> None:
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
    csv_path = _write_csv(tmp_path, ["https://leanpub.com/good"])
    args = SimpleNamespace(input_file=str(csv_path), store_backend="json")

    backend = MagicMock()
    pw_factory, browser = _make_playwright_factory()
    book = {"title": "Good Book", "hash": "abc123"}
    scrape_book = AsyncMock(side_effect=[book])

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
    ("scrape_result", "expected_status"),
    [
        ((None, "DUPLICATE"), "DUPLICATE"),
        ((None, "FAILED"), "FAILED"),
        ("some-unexpected-string", "UNKNOWN_ERROR"),
    ],
)
async def test_run_non_dict_results_recorded_not_saved(tmp_path, scrape_result, expected_status: str) -> None:
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

    # No dict result => nothing saved, but the backend is still closed.
    backend.save_books.assert_not_called()
    backend.close.assert_called_once_with()
    browser.close.assert_awaited_once()

    # The failed/duplicate diagnostic CSV gets exactly the one status tuple.
    save_failed.assert_called_once()
    (recorded,), _ = save_failed.call_args
    assert recorded == [("https://leanpub.com/x", expected_status)]


async def test_run_more_than_ten_urls_triggers_inter_batch_pause(tmp_path) -> None:
    # 11 recognized URLs => two batches (batch_size hardcoded at 10) => one pause.
    urls = [f"https://leanpub.com/book-{i}" for i in range(11)]
    csv_path = _write_csv(tmp_path, urls)
    args = SimpleNamespace(input_file=str(csv_path), store_backend="json")

    backend = MagicMock()
    pw_factory, browser = _make_playwright_factory()
    books = [{"title": f"Book {i}", "hash": f"h{i}"} for i in range(11)]
    scrape_book = AsyncMock(side_effect=books)
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
