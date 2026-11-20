"""Mock-tier end-to-end test of the two-step staged pipeline through main.main() (05.0/02).

Each step has its own tests; this one proves they compose through the real CLI entry point
(bookscraper.main.main, driven by patched sys.argv) with one real staging file and one
in-memory collection in between. Playwright and scrape_book are faked (no browser, no
network); MongoDB is a shared FakeCollection behind a patched database.get_mongo_collection.

The whole point of the milestone is that scrape -> review -> push -> pull -> push composes
correctly: a scrape writes only to staging (Mongo untouched), a reviewer's title fix and
rejection are honoured on push, and a pull of the result pushes back as a no-op.
"""

import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

from bookscraper import main
from bookscraper.backends.mongo import database
from bookscraper.book_utils import dumps_documents
from bookscraper.commands import scrape_urls
from tests.mock.backends.fake_collection import FakeCollection


def _books() -> list[dict]:
    """Three fresh leanpub book dicts, as scrape_book would return them (order == URL order)."""
    return [
        {
            "url": "https://leanpub.com/alpha",
            "site": "leanpub.com",
            "title": "Alpha",
            "authors": ["Ann"],
            "last_published_at": "2021-01-01",
            "description": "d-alpha",
        },
        {
            "url": "https://leanpub.com/beta",
            "site": "leanpub.com",
            "title": "Beta",
            "authors": ["Bob"],
            "last_published_at": "2022-02-02",
            "description": "d-beta",
        },
        {
            "url": "https://leanpub.com/gamma",
            "site": "leanpub.com",
            "title": "Gamma",
            "authors": ["Cat"],
            "last_published_at": "2023-03-03",
            "description": "d-gamma",
        },
    ]


def _make_playwright_factory():
    """An async_playwright() stand-in: `async with factory() as p` yields a page factory
    whose chromium.launch returns a browser with an awaitable close()."""
    browser = MagicMock()
    browser.close = AsyncMock()
    page_factory = MagicMock()
    page_factory.chromium.launch = AsyncMock(return_value=browser)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=page_factory)
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=cm), browser


async def _run_main(monkeypatch, *argv: str) -> None:
    monkeypatch.setattr(sys, "argv", ["bookscraper", *argv])
    await main.main()


def _write_input(path, books: list[dict]) -> None:
    path.write_text(json.dumps([{"url": b["url"]} for b in books]), encoding="utf-8")


async def test_scrape_review_push_pull_cycle(tmp_path, monkeypatch, isolated_staging_file) -> None:
    """Mock: scrape -> edit+reject -> push -> pull -> push composes end-to-end through main.main().

    Scenario: scrape 3 leanpub books to staging, fix one title, reject one, push, then pull and re-push --dry-run.
    Boundaries: faked Playwright/scrape_book, shared FakeCollection; real staging file under tmp_path.
    On failure: the staged pipeline's steps don't compose - an edit is lost, a rejected book reaches Mongo, or a clean export re-pushes as changes.
    """
    staging = isolated_staging_file
    input_file = tmp_path / "input.json"
    _write_input(input_file, _books())
    export_file = tmp_path / "export.json"
    report_dry = tmp_path / "report_dry.json"
    report_real = tmp_path / "report_real.json"
    report_export = tmp_path / "report_export.json"

    collection = FakeCollection()
    pw_factory, browser = _make_playwright_factory()

    with (
        patch.object(main, "configure_logging"),
        patch.object(scrape_urls, "async_playwright", pw_factory),
        patch.object(scrape_urls, "scrape_book", AsyncMock(side_effect=_books())),
        patch.object(database, "get_mongo_collection", return_value=collection),
        patch.object(database, "close_mongo_connection"),
    ):
        # 1. Scrape to staging. No MongoDB contact at all (no --check-mongo).
        await _run_main(monkeypatch, "scrape-urls", "-f", str(input_file), "--staging-file", str(staging))
        staged = json.loads(staging.read_text(encoding="utf-8"))
        assert [d["title"] for d in staged] == ["Alpha", "Beta", "Gamma"]
        assert all(d["_staging"]["status"] == "pending" for d in staged)
        assert collection.calls == []  # step 1 never touched the database
        assert browser.close.await_count == 1

        # 2. Review: fix one title, reject one.
        staged[0]["title"] = "Alpha (fixed)"
        staged[1]["_staging"]["status"] = "rejected"
        staging.write_text(dumps_documents(staged) + "\n", encoding="utf-8")

        # 3. Dry run: zero writes; N-1 inserts, exactly one with hash_changed; the rejected one skipped.
        await _run_main(
            monkeypatch, "push", "--staging-file", str(staging), "--dry-run", "--report-file", str(report_dry)
        )
        dry = json.loads(report_dry.read_text(encoding="utf-8"))
        inserts = [r for r in dry if r["action"] == "insert"]
        assert len(inserts) == 2
        assert sum(1 for r in inserts if r["hash_changed"]) == 1
        assert [r["action"] for r in dry if r["action"].startswith("skipped")] == ["skipped_rejected"]
        assert "bulk_write" not in collection.calls and "create_index" not in collection.calls
        assert collection.documents == []

        # 4. Real push: N-1 documents in Mongo, the edited title stored, the rejected one absent.
        await _run_main(monkeypatch, "push", "--staging-file", str(staging), "--report-file", str(report_real))
        assert sorted(d["title"] for d in collection.documents) == ["Alpha (fixed)", "Gamma"]
        assert not any(d["title"] == "Beta" for d in collection.documents)
        assert all("_staging" not in d for d in collection.documents)

        # 5. Pull the result, then push it back --dry-run: everything is unchanged.
        await _run_main(monkeypatch, "pull", "-o", str(export_file))
        await _run_main(
            monkeypatch, "push", "--staging-file", str(export_file), "--dry-run", "--report-file", str(report_export)
        )

    exported = json.loads(export_file.read_text(encoding="utf-8"))
    assert len(exported) == 2
    round_trip = json.loads(report_export.read_text(encoding="utf-8"))
    assert len(round_trip) == 2
    assert {r["action"] for r in round_trip} == {"unchanged"}


async def test_rescrape_preserves_review_edits(tmp_path, monkeypatch, isolated_staging_file) -> None:
    """Mock: re-running step 1 after a review never touches documents the reviewer already edited (02.0/04).

    Scenario: scrape 3 books, fix one title, reject one, then scrape the same 3 books again.
    Boundaries: faked Playwright/scrape_book; append_to_staging dedups by hash, so all re-scrapes are duplicates.
    On failure: a second scrape overwrites or reorders a reviewer's work, losing the title fix or the rejection.
    """
    staging = isolated_staging_file
    input_file = tmp_path / "input.json"
    _write_input(input_file, _books())
    collection = FakeCollection()
    pw_factory, _browser = _make_playwright_factory()

    with (
        patch.object(main, "configure_logging"),
        patch.object(scrape_urls, "async_playwright", pw_factory),
        patch.object(database, "get_mongo_collection", return_value=collection),
        patch.object(database, "close_mongo_connection"),
    ):
        with patch.object(scrape_urls, "scrape_book", AsyncMock(side_effect=_books())):
            await _run_main(monkeypatch, "scrape-urls", "-f", str(input_file), "--staging-file", str(staging))

        staged = json.loads(staging.read_text(encoding="utf-8"))
        assert len(staged) == 3
        staged[0]["title"] = "Alpha (fixed)"
        staged[1]["_staging"]["status"] = "rejected"
        staging.write_text(dumps_documents(staged) + "\n", encoding="utf-8")

        with patch.object(scrape_urls, "scrape_book", AsyncMock(side_effect=_books())):
            await _run_main(monkeypatch, "scrape-urls", "-f", str(input_file), "--staging-file", str(staging))

    final = json.loads(staging.read_text(encoding="utf-8"))
    assert len(final) == 3  # every re-scraped book was a duplicate; nothing appended or reordered
    assert final[0]["title"] == "Alpha (fixed)"
    assert final[1]["_staging"]["status"] == "rejected"
    assert collection.calls == []  # still offline: a plain re-scrape never contacts MongoDB
