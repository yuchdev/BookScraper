"""Search-page & API golden (characterization) tests (task 06.0, subtask 05).

The unmodified production search code is driven against the committed, sanitized corpus
through the same offline harness the detail goldens use (subtask 03):

* ``get_search_results_via_playwright(browser, "amazon", "python")`` runs in real headless
  Chromium against the served Amazon search snapshots (``@pytest.mark.chromium``; shares the
  one session browser via ``loop_scope="session"``; skips cleanly when no Chromium is usable).
* ``get_leanpub_search_results_via_api("python")`` runs over ``httpx_corpus`` (an
  ``httpx.MockTransport``); no browser is needed, so those tests are unmarked and offline.

The Amazon snapshots are **live captures** (headed system Chromium, logged out), so they are
the first corpus fixtures that record *real* markup - and they already expose live selector
drift (see ``goldens/search_python.expected.json``'s ``_known_issues``): the detail-link
selector no longer matches, so every candidate's ``url``/``asin`` is ``None``. The goldens are
characterization - they lock in what the code does *today*; a deliberate subtask-03.3 fix
surfaces as a reviewable diff rather than vanishing. The Leanpub search API is gone (404), so
those JSON fixtures are hand-built to the JSON:API shape and verify parser/pagination control
flow only.

Regenerate with ``uv run pytest --update-goldens`` (see ``tests/conftest.py``); that rewrites
the ``goldens/*.expected.json`` files (preserving each hand-curated ``_known_issues`` list) and
then fails the run so the diff is never silently green.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import pytest

from bookscraper.scraping import search_utils
from bookscraper.scraping.search_utils import (
    get_leanpub_search_results_via_api,
    get_search_results_via_playwright,
)

from .conftest import FIXTURES_ROOT
from .test_detail_goldens import compare_or_update

# A low per-page default timeout keeps the Chromium leg fast: an Amazon snapshot with no
# result cards (the last-page / no-results fixtures) makes the production
# ``wait_for_selector(SEARCH_BOOK_CARD)`` hit its timeout, which is how the loop terminates.
# On static local fixtures a present selector resolves in <100 ms, so this never flips a
# passing fixture - it only bounds the deterministic no-card timeout.
SEARCH_TIMEOUT_MS = 2000

AMAZON_DIR = FIXTURES_ROOT / "html" / "amazon"
LEANPUB_DIR = FIXTURES_ROOT / "json" / "leanpub"
GOLDENS_DIR = Path(__file__).parent / "goldens"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _amazon_page_url(page: int) -> str:
    """The exact search URL the amazon branch builds for ``python`` page ``page``."""
    base = search_utils.site_constants["amazon"]["SEARCH_BASE_URL"]
    return base.replace("[k]", quote_plus("python")).replace("[p]", str(page))


def _meta_url(fixture: Path) -> str:
    """The captured/built request URL recorded beside a fixture (``<base>.meta.json``)."""
    meta = fixture.parent / f"{fixture.stem}.meta.json"
    return json.loads(meta.read_text(encoding="utf-8"))["url"]


async def _run_amazon_search(
    chromium_browser: Any,
    corpus_router: Any,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mapping: dict[str, Path],
    max_pages: int,
) -> list[dict[str, Any]]:
    """Drive the unmodified amazon search path offline against ``mapping``.

    ``max_pages`` overrides ``SEARCH_MAXIMUM_PAGES`` (the amazon branch scrapes
    ``range(1, SEARCH_MAXIMUM_PAGES)``), and every ``new_page`` gets the low default timeout.
    """
    monkeypatch.setitem(search_utils.site_constants["amazon"], "SEARCH_MAXIMUM_PAGES", max_pages)
    browser = corpus_router(chromium_browser, mapping)
    original_new_page = browser.new_page

    async def _timed_new_page(*args: Any, **kwargs: Any) -> Any:
        page = await original_new_page(*args, **kwargs)
        page.set_default_timeout(SEARCH_TIMEOUT_MS)
        return page

    browser.new_page = _timed_new_page  # type: ignore[method-assign]
    return await get_search_results_via_playwright(browser, "amazon", "python")


# ---------------------------------------------------------------------------
# Amazon search (real headless Chromium against live-captured snapshots)
# ---------------------------------------------------------------------------


@pytest.mark.chromium
@pytest.mark.asyncio(loop_scope="session")
async def test_amazon_search_golden(
    chromium_browser: Any,
    corpus_router: Any,
    update_goldens: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario: the unmodified amazon search scrapes two served pages into its golden.

    Boundaries: pages 1 and 2 are served from committed bytes (everything else default-denied
    and offline); the full candidate list (title/authors/date/url/asin, per-page numbering) is
    characterized field-by-field.
    On failure: a card selector drifted or the dict shape changed - either silently corrupts
    the search half of the corpus. The golden already records today's detail-link drift
    (url/asin None) via ``_known_issues``.
    """
    mapping = {
        _amazon_page_url(1): AMAZON_DIR / "search_python_p1.html",
        _amazon_page_url(2): AMAZON_DIR / "search_python_p2.html",
    }
    result = await _run_amazon_search(chromium_browser, corpus_router, monkeypatch, mapping=mapping, max_pages=3)

    # Two served pages of eight trimmed cards each, numbered per page.
    assert len(result) == 16
    assert [b["search_page_number"] for b in result] == [1] * 8 + [2] * 8
    assert all(b["site"] == "amazon" and b["query"] == "python" for b in result)
    compare_or_update(
        GOLDENS_DIR / "search_python.expected.json",
        {"candidates": result},
        update=update_goldens,
        label="amazon/search_python",
    )


@pytest.mark.chromium
@pytest.mark.asyncio(loop_scope="session")
async def test_amazon_search_stops_on_last_page(
    chromium_browser: Any,
    corpus_router: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario: page 1 has results, page 2 is the exhausted (no-card) last page.

    Boundaries: ``SEARCH_MAXIMUM_PAGES`` is raised to force a page-2 request; page 2 is the
    result-exhausted snapshot, whose absent SEARCH_BOOK_CARD makes the production
    ``wait_for_selector`` time out and stop the run with page 1 already collected.
    On failure: the loop either kept going past an empty page or dropped page-1 results -
    both change which books a search run reports as new.
    """
    mapping = {
        _amazon_page_url(1): AMAZON_DIR / "search_python_p1.html",
        _amazon_page_url(2): AMAZON_DIR / "search_python_last_page.html",
    }
    result = await _run_amazon_search(chromium_browser, corpus_router, monkeypatch, mapping=mapping, max_pages=3)

    # Only page 1's cards survive: the empty page 2 terminated scraping, nothing from it leaked in.
    assert len(result) == 8
    assert {b["search_page_number"] for b in result} == {1}
    assert all(b["title"] for b in result)


@pytest.mark.chromium
@pytest.mark.asyncio(loop_scope="session")
async def test_amazon_search_no_results_returns_empty(
    chromium_browser: Any,
    corpus_router: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario: a no-results search page yields no candidates at all.

    Boundaries: the served page carries no SEARCH_BOOK_CARD, so the production
    ``wait_for_selector`` times out and the function returns an empty list (today amazon has no
    SEARCH_NO_BOOKS_FOUND marker, so this is the real termination path).
    On failure: a no-results page that returned phantom candidates would inject empty/``None``
    rows into the corpus while the run still reported "N books scraped".
    """
    mapping = {_amazon_page_url(1): AMAZON_DIR / "search_no_results.html"}
    result = await _run_amazon_search(chromium_browser, corpus_router, monkeypatch, mapping=mapping, max_pages=2)

    assert result == []


@pytest.mark.chromium
@pytest.mark.asyncio(loop_scope="session")
async def test_amazon_sponsored_card_unwrapped(
    chromium_browser: Any,
    corpus_router: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario: a sponsored ``/sspa/click`` result card is unwrapped to the real product URL.

    Boundaries: the card's detail link is an ``/sspa/click`` redirect carrying the true path in
    its ``url`` query param; the parser must decode it, rebuild an absolute product URL, and
    recover the 10-char ASIN from it.
    On failure: a sponsored card stored with its ``/sspa/click`` tracking URL (or no ASIN) would
    dedup wrong and re-fetch the same book forever.
    """
    mapping = {_amazon_page_url(1): AMAZON_DIR / "search_sponsored.html"}
    result = await _run_amazon_search(chromium_browser, corpus_router, monkeypatch, mapping=mapping, max_pages=2)

    assert len(result) == 1
    book = result[0]
    assert book["url"] == "https://www.amazon.com/Sponsored-Python-Book/dp/B0SPONSOR1/ref=sr_1_1_sspa"
    assert book["asin"] == "B0SPONSOR1"
    assert "/sspa/click" not in book["url"]
    assert book["title"] == "Sponsored Python Book"


# ---------------------------------------------------------------------------
# Leanpub search API (offline httpx_corpus; hand-built JSON:API fixtures)
# ---------------------------------------------------------------------------


async def test_leanpub_search_api_golden_paginates_until_short_page(
    httpx_corpus: Any,
    update_goldens: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario: a full (100-item) page is followed to the next, which is short and stops it.

    Boundaries: page 1 holds exactly 100 items (so the ``len < 100`` guard keeps paginating) and
    page 2 holds 7 (so it stops); the ``SimpleAuthor`` lookup from the ``included`` section is
    resolved onto each book. Both pages are served from committed bytes via ``httpx_corpus``.
    On failure: pagination that stopped early loses page 2, or over-ran into an unmapped page 3
    (default-denied); an author-lookup regression blanks the authors that feed ``hash_book``.
    """
    monkeypatch.setattr(search_utils.random, "uniform", lambda _a, _b: 0)
    p1 = LEANPUB_DIR / "search_python_p1.json"
    last = LEANPUB_DIR / "search_python_last.json"
    httpx_corpus({_meta_url(p1): p1, _meta_url(last): last})

    result = await get_leanpub_search_results_via_api("python")

    assert len(result) == 107  # 100 (full page 1) + 7 (short page 2)
    assert all(b["site"] == "leanpub" for b in result)
    assert result[0]["authors"] == ["Grace Hopper"]  # SimpleAuthor lookup resolved
    assert all(b["authors"] for b in result)  # every book resolved at least one author
    compare_or_update(
        GOLDENS_DIR / "leanpub_search_python.expected.json",
        {"candidates": result},
        update=update_goldens,
        label="leanpub/search_python",
    )


async def test_leanpub_search_api_empty_result(httpx_corpus: Any) -> None:
    """Scenario: a search whose first page has an empty ``data`` array returns no candidates.

    Boundaries: the single served page's empty ``data`` triggers the immediate break before any
    pagination; the result is an empty list.
    On failure: an empty API page that returned non-empty results would fabricate books with no
    identity, corrupting dedup downstream.
    """
    empty = LEANPUB_DIR / "search_empty.json"
    httpx_corpus({_meta_url(empty): empty})

    result = await get_leanpub_search_results_via_api("python")

    assert result == []
