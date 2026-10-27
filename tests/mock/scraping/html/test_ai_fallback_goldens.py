"""LLM extraction-fallback golden (task 04.1, subtask 05).

The end-to-end success criterion for the fallback: a drifted Amazon ``DESCRIPTION`` is recovered
offline through the committed replay recording, and the record it produces is marked
``ai_extracted_fields == ["description"]``.

It runs the **unmodified** ``scrape_book`` against the committed ``cpp_primer_5e`` amazon fixture
served through the offline corpus harness (real headless Chromium, ``chromium``-marked, skips
cleanly when no browser is usable - pick one via ``BOOKSCRAPER_CHROMIUM_PATH``). The deterministic
``DESCRIPTION`` / ``DESCRIPTION_ALT`` selectors are patched to a non-matching selector so the
deterministic path leaves the description empty, exactly as a live selector drift would; the
fallback then fills it from the page HTML already in hand.

Offline guarantee: no live Anthropic call is ever made. The hand-authored response lives in
``tests/fixtures/ai_extraction/amazon.replay.json`` (``value``/``evidence`` is verbatim visible
text of the fixture). Because ``scrape_book`` reads ``page.content()`` - the Chromium-serialized
DOM, which is build-dependent - the committed key (computed from the fixture file bytes by
``scripts/generate_schema_replays.py``) cannot be assumed to match the live prompt; the test
therefore re-keys the committed *response* by the live ``page.content()`` prompt hash, keeping the
run fully offline and Chromium-build-independent while still exercising the real validation gates.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from bookscraper.scraping import ai_common, ai_extraction, scrape_details
from bookscraper.scraping.ai_common import UsageAccumulator
from bookscraper.scraping.parameters import site_constants
from bookscraper.scraping.results import ScrapeStatus
from bookscraper.scraping.scrape_details import AICallCap, AIFallbackRuntime, scrape_book

from .conftest import FIXTURES_ROOT, corpus_mapping

# The one amazon fixture whose description text survives sanitization (hand-built fixture); the live
# captured fixtures have the prose stripped, so there is nothing for the fallback to recover there.
FIXTURE_URL = "https://www.amazon.com/dp/0321714113"
EXPECTED_DESCRIPTION = "Neutralized prose omitted from the public test corpus."
AI_REPLAY_FILE = FIXTURES_ROOT / "ai_extraction" / "amazon.replay.json"

# A low per-read timeout keeps the patched-selector reads (which match nothing and so run to their
# timeout) fast; output-neutral on a static fixture where present elements resolve immediately.
FALLBACK_TIMEOUT_MS = 1000


def _committed_response() -> str:
    """Return the single hand-authored response from the committed replay fixture."""
    data = json.loads(AI_REPLAY_FILE.read_text(encoding="utf-8"))
    return next(value for key, value in data.items() if not key.startswith("_"))


@pytest.mark.chromium
@pytest.mark.asyncio(loop_scope="session")
async def test_ai_fallback_recovers_drifted_amazon_description(
    chromium_browser: Any,
    corpus_router: Any,
    monkeypatch: pytest.MonkeyPatch,
):
    """
    [Local] ai-fallback golden: a drifted amazon DESCRIPTION is recovered and marked (04.1 success criterion).

    Scenario:
        Given the cpp_primer_5e amazon fixture served offline, with DESCRIPTION/DESCRIPTION_ALT
        patched to a non-matching selector, and the fallback enabled in replay mode
        When the unmodified scrape_book runs
        Then the result is SUCCESS, description holds the recovered verbatim text, and
        ai_extracted_fields == ["description"]

    Boundaries:
        - Fully offline: no live Anthropic call; the committed response is re-keyed by the live
          page.content() prompt hash. Skips cleanly when no Chromium is available.

    On failure, first check:
        - The scrape_book fallback hook, the evidence/derivable gates, or the provenance stamp.
    """
    browser = corpus_router(chromium_browser, corpus_mapping("html"))

    async def _no_sleep(_seconds: float):
        """No-op inter-retry sleep (output-neutral)."""

    monkeypatch.setattr(scrape_details.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(scrape_details, "FIELD_TIMEOUT_MS", FALLBACK_TIMEOUT_MS)
    monkeypatch.setattr(scrape_details, "OPTIONAL_FIELD_TIMEOUT_MS", FALLBACK_TIMEOUT_MS)
    # Patch the amazon description selectors to a guaranteed non-match so the deterministic path
    # leaves description empty - the exact drift the fallback exists to cover.
    monkeypatch.setitem(site_constants["amazon"], "DESCRIPTION", "#__bookscraper_no_such_description__")
    monkeypatch.setitem(site_constants["amazon"], "DESCRIPTION_ALT", "#__bookscraper_no_such_description_alt__")

    original_new_context = scrape_details.new_scrape_context

    async def _timed_new_context(browser_arg: Any) -> Any:
        """Open a scrape context with the reduced default timeout (output-neutral on static pages)."""
        context = await original_new_context(browser_arg)
        context.set_default_timeout(FALLBACK_TIMEOUT_MS)
        return context

    monkeypatch.setattr(scrape_details, "new_scrape_context", _timed_new_context)

    # Re-key the committed hand-authored response by the live page.content() prompt hash, so the
    # offline replay matches regardless of the Chromium build's DOM serialization.
    probe = await browser.new_page()
    await probe.goto(FIXTURE_URL)
    live_html = await probe.content()
    await probe.close()
    instruction_block = ai_extraction.build_extraction_instruction_block("amazon", ["DESCRIPTION"])
    snapshot_block = ai_common.build_snapshot_block(ai_common.prune_html(live_html))
    replay = {ai_common.prompt_hash(instruction_block, snapshot_block): _committed_response()}

    runtime = AIFallbackRuntime(
        client=None,
        model="claude-sonnet-5",
        usage=UsageAccumulator(),
        cap=AICallCap(5),
        replay=replay,
    )

    result = await scrape_book(FIXTURE_URL, browser, "amazon", storage_backend=None, ai_fallback=runtime)

    assert result.status is ScrapeStatus.SUCCESS
    assert result.book["description"] == EXPECTED_DESCRIPTION
    assert result.book["ai_extracted_fields"] == ["description"]
    assert runtime.cap.used == 1
    assert runtime.filled == 1
