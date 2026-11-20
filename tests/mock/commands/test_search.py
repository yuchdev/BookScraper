"""Unit/mock tests for bookscraper.commands.search.

resolve_dedup_source, the Leanpub search API, and the Leanpub detail scraper are
all mocked, so no network and no real ~/.bookscrapper is touched; the staging file is real
but lives under tmp_path (which is also the working directory). The
failed-URL report writer here normalizes each record to url/site/error (distinct from
scrape_urls.py's url/status writer).
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bookscraper.commands import search
from bookscraper.commands.search import run, save_failed_urls_to_json

MODULE = "bookscraper.commands.search"


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def _args(tmp_path) -> SimpleNamespace:
    return SimpleNamespace(staging_file=str(tmp_path / "staging" / "books.json"), check_mongo=False)


def _staged(tmp_path) -> list[dict]:
    path = tmp_path / "staging" / "books.json"
    if not path.exists():
        return []
    return [{k: v for k, v in d.items() if k != "_staging"} for d in json.loads(path.read_text(encoding="utf-8"))]


def _leanpub_book(slug: str, title: str) -> dict:
    return {
        "site": "leanpub",
        "title": title,
        "book_id": f"id-{slug}",
        "slug": slug,
        "authors": ["Some Author"],
    }


# --------------------------------------------------------------------------- #
# save_failed_urls_to_json  (search variant: list[dict] normalized to url/site/error)
# --------------------------------------------------------------------------- #
def test_save_failed_urls_empty_creates_no_file(tmp_path) -> None:
    """
    [Unit] search: verifies save failed urls empty creates no file.

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
    target = tmp_path / "failed_urls.json"
    save_failed_urls_to_json([], filename=str(target))
    assert not target.exists()


def test_save_failed_urls_writes_json_array_of_records(tmp_path) -> None:
    """
    [Unit] search: verifies save failed urls writes json array of records.

    Scenario:
        Given test inputs and fixtures for save failed urls writes json array of records
        When test_save_failed_urls_writes_json_array_of_records executes the target flow
        Then the expected outcome for save failed urls writes json array of records is confirmed.

    Boundaries:
        - Focus: save failed urls writes json array of records
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to save failed urls writes json array of records
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search
    """
    target = tmp_path / "failed_urls.json"
    items = [
        {"url": "https://leanpub.com/a", "site": "leanpub", "error": "boom"},
        {"url": "https://leanpub.com/b", "site": "leanpub", "error": "kaboom"},
    ]
    save_failed_urls_to_json(items, filename=str(target))

    assert json.loads(target.read_text(encoding="utf-8")) == items


def test_save_failed_urls_missing_key_becomes_empty_string(tmp_path) -> None:
    """
    [Unit] search: verifies save failed urls missing key becomes empty string.

    Scenario:
        Given test inputs and fixtures for save failed urls missing key becomes empty string
        When test_save_failed_urls_missing_key_becomes_empty_string executes the target flow
        Then the expected outcome for save failed urls missing key becomes empty string is confirmed.

    Boundaries:
        - Focus: save failed urls missing key becomes empty string
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to save failed urls missing key becomes empty string
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search
    """
    target = tmp_path / "failed_urls.json"
    save_failed_urls_to_json(
        [{"url": "https://leanpub.com/a", "site": "leanpub"}],
        filename=str(target),
    )

    assert json.loads(target.read_text(encoding="utf-8")) == [
        {"url": "https://leanpub.com/a", "site": "leanpub", "error": ""}
    ]


def test_save_failed_urls_extra_key_is_dropped_but_record_kept(tmp_path) -> None:
    """
    [Unit] search: verifies save failed urls extra key is dropped but record kept.

    Scenario:
        Given test inputs and fixtures for save failed urls extra key is dropped but record kept
        When test_save_failed_urls_extra_key_is_dropped_but_record_kept executes the target flow
        Then the expected outcome for save failed urls extra key is dropped but record kept is confirmed.

    Boundaries:
        - Focus: save failed urls extra key is dropped but record kept
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to save failed urls extra key is dropped but record kept
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search
    """
    target = tmp_path / "failed_urls.json"
    items = [
        {"url": "https://leanpub.com/good1", "site": "leanpub", "error": "e1"},
        {"url": "https://leanpub.com/extra", "site": "leanpub", "error": "e2", "extra": "nope"},
    ]
    save_failed_urls_to_json(items, filename=str(target))

    records = json.loads(target.read_text(encoding="utf-8"))
    assert [r["url"] for r in records] == ["https://leanpub.com/good1", "https://leanpub.com/extra"]
    # Every record has exactly the fixed key set, in order.
    assert all(list(r) == ["url", "site", "error"] for r in records)


def test_save_failed_urls_non_json_native_value_is_stringified(tmp_path) -> None:
    """
    [Unit] search: verifies save failed urls non json native value is stringified.

    Scenario:
        Given test inputs and fixtures for save failed urls non json native value is stringified
        When test_save_failed_urls_non_json_native_value_is_stringified executes the target flow
        Then the expected outcome for save failed urls non json native value is stringified is confirmed.

    Boundaries:
        - Focus: save failed urls non json native value is stringified
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to save failed urls non json native value is stringified
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search
    """
    target = tmp_path / "failed_urls.json"
    # search.run() records str(e), but a raw exception object must not crash the writer.
    save_failed_urls_to_json(
        [{"url": "u", "site": "leanpub", "error": ValueError("bad")}],
        filename=str(target),
    )
    assert json.loads(target.read_text(encoding="utf-8"))[0]["error"] == "bad"


def test_save_failed_urls_write_error_is_swallowed(tmp_path) -> None:
    """
    [Unit] search: verifies save failed urls write error is swallowed.

    Scenario:
        Given test inputs and fixtures for save failed urls write error is swallowed
        When test_save_failed_urls_write_error_is_swallowed executes the target flow
        Then the expected outcome for save failed urls write error is swallowed is confirmed.

    Boundaries:
        - Focus: save failed urls write error is swallowed
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to save failed urls write error is swallowed
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search
    """
    target = tmp_path / "missing_dir" / "failed_urls.json"
    # Parent directory doesn't exist => write fails; best-effort means no propagation.
    save_failed_urls_to_json(
        [{"url": "u", "site": "leanpub", "error": "e"}],
        filename=str(target),
    )
    assert not target.exists()


# --------------------------------------------------------------------------- #
# run()
# --------------------------------------------------------------------------- #
async def test_run_dedup_only_new_book_proceeds_to_detail_scrape(tmp_path) -> None:
    """
    [Mock] search: verifies run dedup only new book proceeds to detail scrape.

    Scenario:
        Given test inputs and fixtures for run dedup only new book proceeds to detail scrape
        When test_run_dedup_only_new_book_proceeds_to_detail_scrape executes the target flow
        Then the expected outcome for run dedup only new book proceeds to detail scrape is confirmed.

    Boundaries:
        - Focus: run dedup only new book proceeds to detail scrape
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to run dedup only new book proceeds to detail scrape
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search
    """
    args = _args(tmp_path)

    existing = _leanpub_book("existing-book", "Existing Book")
    fresh = _leanpub_book("fresh-book", "Fresh Book")

    backend = MagicMock()
    # First book already staged/known, second is new.
    backend.leanpub_book_exists = MagicMock(side_effect=[True, False])

    search_api = AsyncMock(return_value=[existing, fresh])
    detail = AsyncMock(return_value={"title": "Fresh Book", "hash": "h1"})

    with (
        patch(f"{MODULE}.resolve_dedup_source", return_value=backend),
        patch(f"{MODULE}.SEARCH_QUERIES", ["Python"]),
        patch(f"{MODULE}.get_leanpub_search_results_via_api", search_api),
        patch(f"{MODULE}.get_leanpub_book_details", detail),
        patch(f"{MODULE}.save_failed_urls_to_json"),
    ):
        await run(args)

    # Only the non-duplicate book is scraped in detail.
    detail.assert_awaited_once()
    _, kwargs = detail.call_args
    assert kwargs["url"].endswith("/fresh-book.json")

    assert _staged(tmp_path) == [{"title": "Fresh Book", "hash": "h1"}]
    backend.close.assert_called_once_with()


async def test_run_detail_results_partition_into_saved_and_failed(tmp_path) -> None:
    """
    [Mock] search: verifies run detail results partition into saved and failed.

    Scenario:
        Given test inputs and fixtures for run detail results partition into saved and failed
        When test_run_detail_results_partition_into_saved_and_failed executes the target flow
        Then the expected outcome for run detail results partition into saved and failed is confirmed.

    Boundaries:
        - Focus: run detail results partition into saved and failed
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to run detail results partition into saved and failed
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search
    """
    args = _args(tmp_path)

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
        patch(f"{MODULE}.resolve_dedup_source", return_value=backend),
        patch(f"{MODULE}.SEARCH_QUERIES", ["Python"]),
        patch(f"{MODULE}.get_leanpub_search_results_via_api", search_api),
        patch(f"{MODULE}.get_leanpub_book_details", detail),
        patch(f"{MODULE}.save_failed_urls_to_json", saved_failed),
    ):
        await run(args)

    # Only the dict result is staged.
    assert _staged(tmp_path) == [{"title": "Good Book", "hash": "ok"}]
    backend.close.assert_called_once_with()

    # The Exception and the None results both land in the failed-attempts report.
    saved_failed.assert_called_once()
    (failed_arg,), _ = saved_failed.call_args
    assert len(failed_arg) == 2
    failed_titles = {f["title"] for f in failed_arg}
    assert failed_titles == {"Boom Book", "Weird Book"}
    boom_entry = next(f for f in failed_arg if f["title"] == "Boom Book")
    assert "detail scrape blew up" in boom_entry["error"]


async def test_run_no_new_books_skips_save_but_closes_backend(tmp_path) -> None:
    """
    [Mock] search: verifies run no new books skips save but closes backend.

    Scenario:
        Given test inputs and fixtures for run no new books skips save but closes backend
        When test_run_no_new_books_skips_save_but_closes_backend executes the target flow
        Then the expected outcome for run no new books skips save but closes backend is confirmed.

    Boundaries:
        - Focus: run no new books skips save but closes backend
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to run no new books skips save but closes backend
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search
    """
    args = _args(tmp_path)

    backend = MagicMock()
    search_api = AsyncMock(return_value=[])
    detail = AsyncMock()

    with (
        patch(f"{MODULE}.resolve_dedup_source", return_value=backend),
        patch(f"{MODULE}.SEARCH_QUERIES", ["Python"]),
        patch(f"{MODULE}.get_leanpub_search_results_via_api", search_api),
        patch(f"{MODULE}.get_leanpub_book_details", detail),
        patch(f"{MODULE}.save_failed_urls_to_json"),
    ):
        await run(args)

    detail.assert_not_awaited()
    assert _staged(tmp_path) == []
    backend.close.assert_called_once_with()


async def test_run_keyboard_interrupt_is_caught_and_backend_closed(tmp_path) -> None:
    """
    [Mock] search: verifies run keyboard interrupt is caught and backend closed.

    Scenario:
        Given test inputs and fixtures for run keyboard interrupt is caught and backend closed
        When test_run_keyboard_interrupt_is_caught_and_backend_closed executes the target flow
        Then the expected outcome for run keyboard interrupt is caught and backend closed is confirmed.

    Boundaries:
        - Focus: run keyboard interrupt is caught and backend closed
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to run keyboard interrupt is caught and backend closed
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search
    """
    args = _args(tmp_path)

    backend = MagicMock()
    search_api = AsyncMock(side_effect=KeyboardInterrupt())

    with (
        patch(f"{MODULE}.resolve_dedup_source", return_value=backend),
        patch(f"{MODULE}.SEARCH_QUERIES", ["Python"]),
        patch(f"{MODULE}.get_leanpub_search_results_via_api", search_api),
        patch(f"{MODULE}.get_leanpub_book_details", AsyncMock()),
        patch(f"{MODULE}.save_failed_urls_to_json"),
    ):
        # Must not propagate: run() catches KeyboardInterrupt and finishes cleanly.
        await run(args)

    assert _staged(tmp_path) == []
    backend.close.assert_called_once_with()


async def test_run_generic_exception_is_caught_and_backend_closed(tmp_path) -> None:
    """
    [Mock] search: verifies run generic exception is caught and backend closed.

    Scenario:
        Given test inputs and fixtures for run generic exception is caught and backend closed
        When test_run_generic_exception_is_caught_and_backend_closed executes the target flow
        Then the expected outcome for run generic exception is caught and backend closed is confirmed.

    Boundaries:
        - Focus: run generic exception is caught and backend closed
        - Fixtures/params: tmp_path
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to run generic exception is caught and backend closed
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by search
    """
    args = _args(tmp_path)

    backend = MagicMock()
    search_api = AsyncMock(side_effect=RuntimeError("search API exploded"))

    with (
        patch(f"{MODULE}.resolve_dedup_source", return_value=backend),
        patch(f"{MODULE}.SEARCH_QUERIES", ["Python"]),
        patch(f"{MODULE}.get_leanpub_search_results_via_api", search_api),
        patch(f"{MODULE}.get_leanpub_book_details", AsyncMock()),
        patch(f"{MODULE}.save_failed_urls_to_json"),
    ):
        await run(args)

    assert _staged(tmp_path) == []
    backend.close.assert_called_once_with()


def test_search_module_uses_real_leanpub_only_sites() -> None:
    # Sanity guard: the run() tests monkeypatch SEARCH_QUERIES but rely on the real
    # single-site config; if this changes the run() tests may need revisiting.
    """
    [Mock] search: verifies search module uses real leanpub only sites.

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
