"""Unit/mock tests for bookscraper.commands.scrape_urls.

Every external collaborator (the resolved storage backend, Playwright, scrape_book,
asyncio.sleep) is mocked; no network, no browser, and no real ~/.bookscrapper or
books.json is ever touched because resolve_store_backend is always patched.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bookscraper.commands.scrape_urls import (
    InputFileError,
    identify_website,
    load_input_urls,
    run,
    save_failed_urls_to_json,
    save_other_links_to_json,
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
# save_failed_urls_to_json  (scrape_urls variant: (url, status) tuples -> objects)
# --------------------------------------------------------------------------- #
def test_save_failed_urls_empty_creates_no_file(tmp_path) -> None:
    target = tmp_path / "failed_books.json"
    save_failed_urls_to_json([], filename=str(target))
    assert not target.exists()


def test_save_failed_urls_writes_json_records(tmp_path) -> None:
    target = tmp_path / "failed_books.json"
    rows = [
        ("https://leanpub.com/a", "DUPLICATE"),
        ("https://www.amazon.com/dp/1", "FAILED"),
        ("https://example.com/x", "UNKNOWN_ERROR"),
    ]
    save_failed_urls_to_json(rows, filename=str(target))

    assert json.loads(target.read_text(encoding="utf-8")) == [{"url": u, "status": st} for u, st in rows]


def test_save_failed_urls_write_failure_is_swallowed(tmp_path) -> None:
    target = tmp_path / "failed_books.json"
    with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
        # Must not propagate: the caller relies on best-effort diagnostic writes.
        save_failed_urls_to_json([("u", "FAILED")], filename=str(target))
    assert not target.exists()


# --------------------------------------------------------------------------- #
# save_other_links_to_json  ({"url": ...} objects)
# --------------------------------------------------------------------------- #
def test_save_other_links_empty_creates_no_file(tmp_path) -> None:
    target = tmp_path / "other_links.json"
    save_other_links_to_json([], filename=str(target))
    assert not target.exists()


def test_save_other_links_writes_json_records(tmp_path) -> None:
    target = tmp_path / "other_links.json"
    links = ["https://example.com/a", "https://example.org/b"]
    save_other_links_to_json(links, filename=str(target))

    assert json.loads(target.read_text(encoding="utf-8")) == [{"url": link} for link in links]


def test_save_other_links_write_failure_is_swallowed(tmp_path) -> None:
    target = tmp_path / "other_links.json"
    with patch("pathlib.Path.write_text", side_effect=OSError("read-only fs")):
        save_other_links_to_json(["https://example.com/a"], filename=str(target))
    assert not target.exists()


# --------------------------------------------------------------------------- #
# load_input_urls
# --------------------------------------------------------------------------- #
def _write_json(tmp_path, payload, name: str = "urls.json"):
    target = tmp_path / name
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def _write_input(tmp_path, urls):
    """An input file of minimal book documents, one per URL."""
    return _write_json(tmp_path, [{"title": f"Book {i}", "url": url} for i, url in enumerate(urls)])


def test_load_input_urls_reads_url_field_and_ignores_other_fields(tmp_path) -> None:
    path = _write_json(
        tmp_path,
        [
            {"title": "A", "authors": ["X"], "hash": "h1", "url": "https://leanpub.com/a"},
            {"url": "  https://www.amazon.com/dp/1  "},
        ],
    )
    assert load_input_urls(str(path)) == ["https://leanpub.com/a", "https://www.amazon.com/dp/1"]


def test_load_input_urls_accepts_empty_array(tmp_path) -> None:
    assert load_input_urls(str(_write_json(tmp_path, []))) == []


def test_load_input_urls_accepts_utf8_bom(tmp_path) -> None:
    path = tmp_path / "bom.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps([{"url": "https://leanpub.com/a"}]).encode())
    assert load_input_urls(str(path)) == ["https://leanpub.com/a"]


def test_load_input_urls_accepts_books_json_store_output(tmp_path) -> None:
    # A books.json written by the json backend is itself valid scrape-urls input.
    from bookscraper.book_utils import dumps_documents

    path = tmp_path / "books.json"
    path.write_text(dumps_documents([{"title": "T", "hash": "h", "url": "https://leanpub.com/t"}]), encoding="utf-8")
    assert load_input_urls(str(path)) == ["https://leanpub.com/t"]


def test_load_input_urls_missing_file_raises(tmp_path) -> None:
    with pytest.raises(InputFileError, match="was not found"):
        load_input_urls(str(tmp_path / "nope.json"))


def test_load_input_urls_invalid_json_raises(tmp_path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("url\nhttps://leanpub.com/a\n", encoding="utf-8")  # an old CSV, say
    with pytest.raises(InputFileError, match="Could not parse"):
        load_input_urls(str(path))


def test_load_input_urls_unreadable_file_raises(tmp_path) -> None:
    path = tmp_path / "binary.json"
    path.write_bytes(b"\xff\xfe\x00garbage")
    with pytest.raises(InputFileError, match="Could not read"):
        load_input_urls(str(path))


def test_load_input_urls_top_level_object_raises(tmp_path) -> None:
    with pytest.raises(InputFileError, match="must contain an array"):
        load_input_urls(str(_write_json(tmp_path, {"url": "https://leanpub.com/a"})))


@pytest.mark.parametrize(
    "bad_entry",
    [
        {"link": "https://leanpub.com/a"},  # no url key
        {"url": ""},
        {"url": "   "},
        {"url": None},
        {"url": 42},
        "https://leanpub.com/a",  # a bare string, not a document
    ],
)
def test_load_input_urls_bad_entry_rejects_whole_file(tmp_path, bad_entry) -> None:
    path = _write_json(tmp_path, [{"url": "https://leanpub.com/ok"}, bad_entry])
    with pytest.raises(InputFileError, match="Entry #1"):
        load_input_urls(str(path))


# --------------------------------------------------------------------------- #
# helpers for run()
# --------------------------------------------------------------------------- #
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
    json_path = _write_input(tmp_path, ["https://example.com/book"])
    args = SimpleNamespace(input_file=str(json_path), store_backend="json")

    backend = MagicMock()
    pw_factory, _browser = _make_playwright_factory()

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend) as resolve,
        patch(f"{MODULE}.async_playwright", pw_factory),
        patch(f"{MODULE}.save_other_links_to_json") as save_other,
        patch(f"{MODULE}.save_failed_urls_to_json") as save_failed,
        pytest.raises(SystemExit) as exc_info,
    ):
        await run(args)

    assert exc_info.value.code == 0
    resolve.assert_called_once_with("json")
    # The single "other" URL is handed to the skipped-links writer.
    save_other.assert_called_once_with(["https://example.com/book"])
    # Never reaches the browser section, never saves books, never writes the failed report.
    pw_factory.assert_not_called()
    save_failed.assert_not_called()
    backend.save_books.assert_not_called()


async def test_run_missing_input_file_exits_one(tmp_path) -> None:
    missing = tmp_path / "does_not_exist.json"
    args = SimpleNamespace(input_file=str(missing), store_backend="json")

    backend = MagicMock()
    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        pytest.raises(SystemExit) as exc_info,
    ):
        await run(args)

    assert exc_info.value.code == 1
    backend.save_books.assert_not_called()


async def test_run_entry_without_url_exits_one(tmp_path) -> None:
    json_path = _write_json(tmp_path, [{"link": "https://leanpub.com/a"}])
    args = SimpleNamespace(input_file=str(json_path), store_backend="json")

    backend = MagicMock()
    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        pytest.raises(SystemExit) as exc_info,
    ):
        await run(args)

    assert exc_info.value.code == 1
    backend.save_books.assert_not_called()


async def test_run_unparseable_json_exits_one(tmp_path) -> None:
    json_path = tmp_path / "urls.json"
    json_path.write_text("[{not json", encoding="utf-8")
    args = SimpleNamespace(input_file=str(json_path), store_backend="json")

    backend = MagicMock()
    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        pytest.raises(SystemExit) as exc_info,
    ):
        await run(args)

    assert exc_info.value.code == 1
    backend.save_books.assert_not_called()


# --------------------------------------------------------------------------- #
# run() - scraping loop / result type-switch
# --------------------------------------------------------------------------- #
async def test_run_successful_book_is_saved_and_backend_closed(tmp_path) -> None:
    json_path = _write_input(tmp_path, ["https://leanpub.com/good"])
    args = SimpleNamespace(input_file=str(json_path), store_backend="json")

    backend = MagicMock()
    pw_factory, browser = _make_playwright_factory()
    book = {"title": "Good Book", "hash": "abc123"}
    scrape_book = AsyncMock(side_effect=[book])

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.async_playwright", pw_factory),
        patch(f"{MODULE}.scrape_book", scrape_book),
        patch(f"{MODULE}.save_other_links_to_json"),
        patch(f"{MODULE}.save_failed_urls_to_json") as save_failed,
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
    json_path = _write_input(tmp_path, ["https://leanpub.com/x"])
    args = SimpleNamespace(input_file=str(json_path), store_backend="json")

    backend = MagicMock()
    pw_factory, browser = _make_playwright_factory()
    scrape_book = AsyncMock(side_effect=[scrape_result])

    with (
        patch(f"{MODULE}.resolve_store_backend", return_value=backend),
        patch(f"{MODULE}.async_playwright", pw_factory),
        patch(f"{MODULE}.scrape_book", scrape_book),
        patch(f"{MODULE}.save_other_links_to_json"),
        patch(f"{MODULE}.save_failed_urls_to_json") as save_failed,
    ):
        await run(args)

    # No dict result => nothing saved, but the backend is still closed.
    backend.save_books.assert_not_called()
    backend.close.assert_called_once_with()
    browser.close.assert_awaited_once()

    # The failed/duplicate diagnostic report gets exactly the one status tuple.
    save_failed.assert_called_once()
    (recorded,), _ = save_failed.call_args
    assert recorded == [("https://leanpub.com/x", expected_status)]


async def test_run_more_than_ten_urls_triggers_inter_batch_pause(tmp_path) -> None:
    # 11 recognized URLs => two batches (batch_size hardcoded at 10) => one pause.
    urls = [f"https://leanpub.com/book-{i}" for i in range(11)]
    json_path = _write_input(tmp_path, urls)
    args = SimpleNamespace(input_file=str(json_path), store_backend="json")

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
        patch(f"{MODULE}.save_other_links_to_json"),
        patch(f"{MODULE}.save_failed_urls_to_json"),
    ):
        await run(args)

    # All 11 scraped once each, across two batches.
    assert scrape_book.await_count == 11
    # Exactly one inter-batch pause (between batch 1 and batch 2).
    sleep.assert_awaited_once()
    backend.save_books.assert_called_once_with(books)
    backend.close.assert_called_once_with()
    browser.close.assert_awaited_once()
