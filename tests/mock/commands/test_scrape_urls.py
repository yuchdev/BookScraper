"""Unit/mock tests for bookscraper.commands.scrape_urls.

Every external collaborator (the dedup source, Playwright, scrape_book, asyncio.sleep) is
mocked; no network, no browser, and no real ~/.bookscrapper is touched. The staging file is
real but lives under tmp_path, and the working directory is tmp_path, so the repo's own
staging/ directory and any legacy books.json are never read or written.
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


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def _args(tmp_path, json_path, check_mongo: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        input_file=str(json_path), staging_file=str(tmp_path / "staging" / "books.json"), check_mongo=check_mongo
    )


def _staged(tmp_path) -> list[dict]:
    """Book fields of everything in the tmp staging file ([] if it was never written)."""
    path = tmp_path / "staging" / "books.json"
    if not path.exists():
        return []
    return [{k: v for k, v in d.items() if k != "_staging"} for d in json.loads(path.read_text(encoding="utf-8"))]


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
    [Unit] scrape urls: verifies identify website all branches.

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
# save_failed_urls_to_json  (scrape_urls variant: (url, status) tuples -> objects)
# --------------------------------------------------------------------------- #
def test_save_failed_urls_empty_creates_no_file(tmp_path) -> None:
    """
    [Unit] scrape urls: verifies save failed urls empty creates no file.

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
    target = tmp_path / "failed_books.json"
    save_failed_urls_to_json([], filename=str(target))
    assert not target.exists()


def test_save_failed_urls_writes_json_records(tmp_path) -> None:
    """
    [Unit] scrape urls: verifies save failed urls writes json records.

    Scenario:
        Given test inputs and fixtures for save failed urls writes json records
        When test_save_failed_urls_writes_json_records executes the target flow
        Then the expected outcome for save failed urls writes json records is confirmed.

    Boundaries:
        - Focus: save failed urls writes json records
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to save failed urls writes json records
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by scrape urls
    """
    target = tmp_path / "failed_books.json"
    rows = [
        ("https://leanpub.com/a", "DUPLICATE"),
        ("https://www.amazon.com/dp/1", "FAILED"),
        ("https://example.com/x", "UNKNOWN_ERROR"),
    ]
    save_failed_urls_to_json(rows, filename=str(target))

    assert json.loads(target.read_text(encoding="utf-8")) == [{"url": u, "status": st} for u, st in rows]


def test_save_failed_urls_write_failure_is_swallowed(tmp_path) -> None:
    """
    [Unit] scrape urls: verifies save failed urls write failure is swallowed.

    Scenario:
        Given test inputs and fixtures for save failed urls write failure is swallowed
        When test_save_failed_urls_write_failure_is_swallowed executes the target flow
        Then the expected outcome for save failed urls write failure is swallowed is confirmed.

    Boundaries:
        - Focus: save failed urls write failure is swallowed
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to save failed urls write failure is swallowed
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by scrape urls
    """
    target = tmp_path / "failed_books.json"
    with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
        # Must not propagate: the caller relies on best-effort diagnostic writes.
        save_failed_urls_to_json([("u", "FAILED")], filename=str(target))
    assert not target.exists()


# --------------------------------------------------------------------------- #
# save_other_links_to_json  ({"url": ...} objects)
# --------------------------------------------------------------------------- #
def test_save_other_links_empty_creates_no_file(tmp_path) -> None:
    """
    [Unit] scrape urls: verifies save other links empty creates no file.

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
    target = tmp_path / "other_links.json"
    save_other_links_to_json([], filename=str(target))
    assert not target.exists()


def test_save_other_links_writes_json_records(tmp_path) -> None:
    """
    [Unit] scrape urls: verifies save other links writes json records.

    Scenario:
        Given test inputs and fixtures for save other links writes json records
        When test_save_other_links_writes_json_records executes the target flow
        Then the expected outcome for save other links writes json records is confirmed.

    Boundaries:
        - Focus: save other links writes json records
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to save other links writes json records
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by scrape urls
    """
    target = tmp_path / "other_links.json"
    links = ["https://example.com/a", "https://example.org/b"]
    save_other_links_to_json(links, filename=str(target))

    assert json.loads(target.read_text(encoding="utf-8")) == [{"url": link} for link in links]


def test_save_other_links_write_failure_is_swallowed(tmp_path) -> None:
    """
    [Unit] scrape urls: verifies save other links write failure is swallowed.

    Scenario:
        Given test inputs and fixtures for save other links write failure is swallowed
        When test_save_other_links_write_failure_is_swallowed executes the target flow
        Then the expected outcome for save other links write failure is swallowed is confirmed.

    Boundaries:
        - Focus: save other links write failure is swallowed
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to save other links write failure is swallowed
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by scrape urls
    """
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
    """
    [Unit] scrape urls: verifies load input urls reads url field and ignores other fields.

    Scenario:
        Given test inputs and fixtures for load input urls reads url field and ignores other fields
        When test_load_input_urls_reads_url_field_and_ignores_other_fields executes the target flow
        Then the expected outcome for load input urls reads url field and ignores other fields is confirmed.

    Boundaries:
        - Focus: load input urls reads url field and ignores other fields
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to load input urls reads url field and ignores other fields
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by scrape urls
    """
    path = _write_json(
        tmp_path,
        [
            {"title": "A", "authors": ["X"], "hash": "h1", "url": "https://leanpub.com/a"},
            {"url": "  https://www.amazon.com/dp/1  "},
        ],
    )
    assert load_input_urls(str(path)) == ["https://leanpub.com/a", "https://www.amazon.com/dp/1"]


def test_load_input_urls_accepts_empty_array(tmp_path) -> None:
    """
    [Unit] scrape urls: verifies load input urls accepts empty array.

    Scenario:
        Given test inputs and fixtures for load input urls accepts empty array
        When test_load_input_urls_accepts_empty_array executes the target flow
        Then the expected outcome for load input urls accepts empty array is confirmed.

    Boundaries:
        - Focus: load input urls accepts empty array
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to load input urls accepts empty array
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by scrape urls
    """
    assert load_input_urls(str(_write_json(tmp_path, []))) == []


def test_load_input_urls_accepts_utf8_bom(tmp_path) -> None:
    """
    [Unit] scrape urls: verifies load input urls accepts utf8 bom.

    Scenario:
        Given test inputs and fixtures for load input urls accepts utf8 bom
        When test_load_input_urls_accepts_utf8_bom executes the target flow
        Then the expected outcome for load input urls accepts utf8 bom is confirmed.

    Boundaries:
        - Focus: load input urls accepts utf8 bom
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to load input urls accepts utf8 bom
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by scrape urls
    """
    path = tmp_path / "bom.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps([{"url": "https://leanpub.com/a"}]).encode())
    assert load_input_urls(str(path)) == ["https://leanpub.com/a"]


def test_load_input_urls_accepts_books_json_store_output(tmp_path) -> None:
    # A books.json written by the json backend is itself valid scrape-urls input.
    """
    [Unit] scrape urls: verifies load input urls accepts books json store output.

    Scenario:
        Given test inputs and fixtures for load input urls accepts books json store output
        When test_load_input_urls_accepts_books_json_store_output executes the target flow
        Then the expected outcome for load input urls accepts books json store output is confirmed.

    Boundaries:
        - Focus: load input urls accepts books json store output
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to load input urls accepts books json store output
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by scrape urls
    """
    from bookscraper.book_utils import dumps_documents

    path = tmp_path / "books.json"
    path.write_text(dumps_documents([{"title": "T", "hash": "h", "url": "https://leanpub.com/t"}]), encoding="utf-8")
    assert load_input_urls(str(path)) == ["https://leanpub.com/t"]


def test_load_input_urls_missing_file_raises(tmp_path) -> None:
    """
    [Unit] scrape urls: verifies load input urls missing file raises.

    Scenario:
        Given test inputs and fixtures for load input urls missing file raises
        When test_load_input_urls_missing_file_raises executes the target flow
        Then the expected outcome for load input urls missing file raises is confirmed.

    Boundaries:
        - Focus: load input urls missing file raises
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to load input urls missing file raises
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by scrape urls
    """
    with pytest.raises(InputFileError, match="was not found"):
        load_input_urls(str(tmp_path / "nope.json"))


def test_load_input_urls_invalid_json_raises(tmp_path) -> None:
    """
    [Unit] scrape urls: verifies load input urls invalid json raises.

    Scenario:
        Given test inputs and fixtures for load input urls invalid json raises
        When test_load_input_urls_invalid_json_raises executes the target flow
        Then the expected outcome for load input urls invalid json raises is confirmed.

    Boundaries:
        - Focus: load input urls invalid json raises
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to load input urls invalid json raises
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by scrape urls
    """
    path = tmp_path / "bad.json"
    path.write_text("url\nhttps://leanpub.com/a\n", encoding="utf-8")  # an old CSV, say
    with pytest.raises(InputFileError, match="Could not parse"):
        load_input_urls(str(path))


def test_load_input_urls_unreadable_file_raises(tmp_path) -> None:
    """
    [Unit] scrape urls: verifies load input urls unreadable file raises.

    Scenario:
        Given test inputs and fixtures for load input urls unreadable file raises
        When test_load_input_urls_unreadable_file_raises executes the target flow
        Then the expected outcome for load input urls unreadable file raises is confirmed.

    Boundaries:
        - Focus: load input urls unreadable file raises
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to load input urls unreadable file raises
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by scrape urls
    """
    path = tmp_path / "binary.json"
    path.write_bytes(b"\xff\xfe\x00garbage")
    with pytest.raises(InputFileError, match="Could not read"):
        load_input_urls(str(path))


def test_load_input_urls_top_level_object_raises(tmp_path) -> None:
    """
    [Unit] scrape urls: verifies load input urls top level object raises.

    Scenario:
        Given test inputs and fixtures for load input urls top level object raises
        When test_load_input_urls_top_level_object_raises executes the target flow
        Then the expected outcome for load input urls top level object raises is confirmed.

    Boundaries:
        - Focus: load input urls top level object raises
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to load input urls top level object raises
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by scrape urls
    """
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
    """
    [Unit] scrape urls: verifies load input urls bad entry rejects whole file.

    Scenario:
        Given test inputs and fixtures for load input urls bad entry rejects whole file
        When test_load_input_urls_bad_entry_rejects_whole_file executes the target flow
        Then the expected outcome for load input urls bad entry rejects whole file is confirmed.

    Boundaries:
        - Focus: load input urls bad entry rejects whole file
        - Fixtures/params: tmp_path, bad_entry (parametrized, 6 cases)
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to load input urls bad entry rejects whole file
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by scrape urls
    """
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
    """
    [E2E] scrape urls: verifies run no valid urls saves other links and exits zero.

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
    json_path = _write_input(tmp_path, ["https://example.com/book"])
    args = _args(tmp_path, json_path)

    backend = MagicMock()
    pw_factory, _browser = _make_playwright_factory()

    with (
        patch(f"{MODULE}.resolve_dedup_source", return_value=backend) as resolve,
        patch(f"{MODULE}.async_playwright", pw_factory),
        patch(f"{MODULE}.save_other_links_to_json") as save_other,
        patch(f"{MODULE}.save_failed_urls_to_json") as save_failed,
        pytest.raises(SystemExit) as exc_info,
    ):
        await run(args)

    assert exc_info.value.code == 0
    resolve.assert_called_once_with(tmp_path / "staging" / "books.json", False)
    # The single "other" URL is handed to the skipped-links writer.
    save_other.assert_called_once_with(["https://example.com/book"])
    # Never reaches the browser section, never stages books, never writes the failed report.
    pw_factory.assert_not_called()
    save_failed.assert_not_called()
    assert _staged(tmp_path) == []


async def test_run_missing_input_file_exits_one(tmp_path) -> None:
    """
    [Mock] scrape urls: verifies run missing input file exits one.

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
    missing = tmp_path / "does_not_exist.json"
    args = _args(tmp_path, missing)

    backend = MagicMock()
    with (
        patch(f"{MODULE}.resolve_dedup_source", return_value=backend),
        pytest.raises(SystemExit) as exc_info,
    ):
        await run(args)

    assert exc_info.value.code == 1
    assert _staged(tmp_path) == []


async def test_run_entry_without_url_exits_one(tmp_path) -> None:
    """
    [Mock] scrape urls: verifies run entry without url exits one.

    Scenario:
        Given test inputs and fixtures for run entry without url exits one
        When test_run_entry_without_url_exits_one executes the target flow
        Then the expected outcome for run entry without url exits one is confirmed.

    Boundaries:
        - Focus: run entry without url exits one
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to run entry without url exits one
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by scrape urls
    """
    json_path = _write_json(tmp_path, [{"link": "https://leanpub.com/a"}])
    args = _args(tmp_path, json_path)

    backend = MagicMock()
    with (
        patch(f"{MODULE}.resolve_dedup_source", return_value=backend),
        pytest.raises(SystemExit) as exc_info,
    ):
        await run(args)

    assert exc_info.value.code == 1
    assert _staged(tmp_path) == []


async def test_run_unparseable_json_exits_one(tmp_path) -> None:
    """
    [Mock] scrape urls: verifies run unparseable json exits one.

    Scenario:
        Given test inputs and fixtures for run unparseable json exits one
        When test_run_unparseable_json_exits_one executes the target flow
        Then the expected outcome for run unparseable json exits one is confirmed.

    Boundaries:
        - Focus: run unparseable json exits one
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to run unparseable json exits one
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by scrape urls
    """
    json_path = tmp_path / "urls.json"
    json_path.write_text("[{not json", encoding="utf-8")
    args = _args(tmp_path, json_path)

    backend = MagicMock()
    with (
        patch(f"{MODULE}.resolve_dedup_source", return_value=backend),
        pytest.raises(SystemExit) as exc_info,
    ):
        await run(args)

    assert exc_info.value.code == 1
    assert _staged(tmp_path) == []


# --------------------------------------------------------------------------- #
# run() - scraping loop / result type-switch
# --------------------------------------------------------------------------- #
async def test_run_successful_book_is_saved_and_backend_closed(tmp_path) -> None:
    """
    [E2E] scrape urls: verifies run successful book is saved and backend closed.

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
    json_path = _write_input(tmp_path, ["https://leanpub.com/good"])
    args = _args(tmp_path, json_path)

    backend = MagicMock()
    pw_factory, browser = _make_playwright_factory()
    book = {"title": "Good Book", "hash": "abc123"}
    scrape_book = AsyncMock(side_effect=[book])

    with (
        patch(f"{MODULE}.resolve_dedup_source", return_value=backend),
        patch(f"{MODULE}.async_playwright", pw_factory),
        patch(f"{MODULE}.scrape_book", scrape_book),
        patch(f"{MODULE}.save_other_links_to_json"),
        patch(f"{MODULE}.save_failed_urls_to_json") as save_failed,
    ):
        await run(args)

    assert _staged(tmp_path) == [book]
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
    """
    [E2E] scrape urls: verifies run non dict results recorded not saved.

    Scenario:
        Given test inputs and fixtures for run non dict results recorded not saved
        When test_run_non_dict_results_recorded_not_saved executes the target flow
        Then the expected outcome for run non dict results recorded not saved is confirmed.

    Boundaries:
        - Focus: run non dict results recorded not saved
        - Fixtures/params: tmp_path, scrape_result, expected_status (parametrized, 3 cases)
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to run non dict results recorded not saved
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by scrape urls
    """
    json_path = _write_input(tmp_path, ["https://leanpub.com/x"])
    args = _args(tmp_path, json_path)

    backend = MagicMock()
    pw_factory, browser = _make_playwright_factory()
    scrape_book = AsyncMock(side_effect=[scrape_result])

    with (
        patch(f"{MODULE}.resolve_dedup_source", return_value=backend),
        patch(f"{MODULE}.async_playwright", pw_factory),
        patch(f"{MODULE}.scrape_book", scrape_book),
        patch(f"{MODULE}.save_other_links_to_json"),
        patch(f"{MODULE}.save_failed_urls_to_json") as save_failed,
    ):
        await run(args)

    # No dict result => nothing saved, but the backend is still closed.
    assert _staged(tmp_path) == []
    backend.close.assert_called_once_with()
    browser.close.assert_awaited_once()

    # The failed/duplicate diagnostic report gets exactly the one status tuple.
    save_failed.assert_called_once()
    (recorded,), _ = save_failed.call_args
    assert recorded == [("https://leanpub.com/x", expected_status)]


async def test_run_more_than_ten_urls_triggers_inter_batch_pause(tmp_path) -> None:
    # 11 recognized URLs => two batches (batch_size hardcoded at 10) => one pause.
    """
    [E2E] scrape urls: verifies run more than ten urls triggers inter batch pause.

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
    json_path = _write_input(tmp_path, urls)
    args = _args(tmp_path, json_path)

    backend = MagicMock()
    pw_factory, browser = _make_playwright_factory()
    books = [{"title": f"Book {i}", "hash": f"h{i}"} for i in range(11)]
    scrape_book = AsyncMock(side_effect=books)
    sleep = AsyncMock()

    with (
        patch(f"{MODULE}.resolve_dedup_source", return_value=backend),
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
    assert _staged(tmp_path) == books
    backend.close.assert_called_once_with()
    browser.close.assert_awaited_once()
