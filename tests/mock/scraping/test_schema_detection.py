"""Mock-tier tests for bookscraper.scraping.schema_detection.

Playwright is faked via tests/mock/scraping/playwright_fakes.py and the anthropic client
is always mocked - this suite MUST NEVER make a real network call to Anthropic's API or
spend real credits. Covers: the happy path, a missing ANTHROPIC_API_KEY, malformed AI
JSON, a selector that matches some sample URLs but not others (flagged, not silently
accepted), and a Playwright page-load failure.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from bookscraper.scraping import schema_detection as sd
from bookscraper.scraping.parameters import site_constants
from tests.mock.scraping.playwright_fakes import (
    FakeBrowser,
    FakeLocator,
    FakePage,
    build_playwright_factory,
)

MODULE = "bookscraper.scraping.schema_detection"


def _detail_replay_for(content: str, response_json: str, site: str = "amazon") -> dict[str, str]:
    """Build a replay mapping whose single key is the exact prompt hash the detail pipeline
    will compute for a page whose ``content()`` is ``content`` - using the real prompt builders
    so the test can never drift from the runtime's key derivation."""
    fields = sd.detail_selector_fields(site_constants[site])
    instruction_block = sd.build_instruction_block(site, fields, page_type="detail")
    snapshot_block = sd.build_snapshot_block(sd.prune_html(content))
    return {sd.prompt_hash(instruction_block, snapshot_block): response_json}


def _fake_usage(
    input_tokens: int = 0,
    cache_read: int = 0,
    cache_creation: int = 0,
    output_tokens: int = 0,
) -> SimpleNamespace:
    """Build a stand-in for an anthropic ``Message.usage`` object."""
    return SimpleNamespace(
        input_tokens=input_tokens,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_creation,
        output_tokens=output_tokens,
    )


def _fake_message(
    text: str,
    stop_reason: str = "end_turn",
    stop_details: object = None,
    usage: object = None,
    blocks: object = None,
) -> SimpleNamespace:
    """Build a stand-in for an anthropic Message with typed content blocks + usage."""
    content = blocks if blocks is not None else [SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        stop_details=stop_details,
        usage=usage if usage is not None else _fake_usage(),
    )


def _fake_client(message: object, input_tokens: int = 100) -> MagicMock:
    """A mocked anthropic client whose ``count_tokens`` and ``create`` are pre-wired."""
    client = MagicMock()
    client.messages.count_tokens.return_value = SimpleNamespace(input_tokens=input_tokens)
    client.messages.create.return_value = message
    return client


# --------------------------------------------------------------------------- #
# call_anthropic (anthropic SDK mocked)
# --------------------------------------------------------------------------- #
def test_call_anthropic_returns_reply_text_and_usage() -> None:
    """
    [Local] schema detection: verifies call anthropic returns reply text and usage.

    Scenario:
        a well-formed reply is returned with its usage object.

    Boundaries:
        - the anthropic client (count_tokens + create) is fully mocked; no real call.

    On failure, first check:
        - the model kwarg is not threaded through, or usage is dropped.
    """
    usage = _fake_usage(input_tokens=42, output_tokens=7)
    client = _fake_client(_fake_message('{"BOOK_TITLE": {"selector": "#t"}}', usage=usage))

    with patch.object(sd, "anthropic") as mock_anthropic:
        mock_anthropic.Anthropic.return_value = client
        text, returned_usage = sd.call_anthropic("instructions", "snapshot", "test-key", "model-x")

    assert text == '{"BOOK_TITLE": {"selector": "#t"}}'
    assert returned_usage is usage
    _, kwargs = client.messages.create.call_args
    assert kwargs["model"] == "model-x"


def test_call_anthropic_sets_bounded_retries() -> None:
    """
    [Local] schema detection: verifies call anthropic sets bounded retries.

    Scenario:
        the client is built with a single retry, matching the module's own bound.

    Boundaries:
        - only the client constructor kwargs are asserted; no network.

    On failure, first check:
        - stacked/unbounded retries would multiply spend on a transient error.
    """
    client = _fake_client(_fake_message('{"BOOK_TITLE": {"selector": "#t"}}'))
    with patch.object(sd, "anthropic") as mock_anthropic:
        mock_anthropic.Anthropic.return_value = client
        sd.call_anthropic("instructions", "snapshot", "test-key", "model-x")

    _, kwargs = mock_anthropic.Anthropic.call_args
    assert kwargs["max_retries"] == sd.MAX_API_RETRIES == 1


def test_instruction_block_marked_cacheable_and_snapshot_not() -> None:
    """
    [Local] schema detection: verifies instruction block marked cacheable and snapshot not.

    Scenario:
        cache_control sits on the static instruction block, never the per-page snapshot.

    Boundaries:
        - asserts only the create() kwargs shape; no network.

    On failure, first check:
        - caching the volatile snapshot would write a fresh entry every request.
    """
    client = _fake_client(_fake_message('{"BOOK_TITLE": {"selector": "#t"}}'))
    with patch.object(sd, "anthropic") as mock_anthropic:
        mock_anthropic.Anthropic.return_value = client
        sd.call_anthropic("INSTRUCTIONS", "SNAPSHOT", "test-key", "model-x")

    _, kwargs = client.messages.create.call_args
    system_block = kwargs["system"][0]
    assert system_block["text"] == "INSTRUCTIONS"
    assert system_block["cache_control"] == {"type": "ephemeral"}
    user_content = kwargs["messages"][0]["content"]
    assert user_content == "SNAPSHOT"
    # The snapshot is a plain string, not a dict carrying a cache_control marker.
    assert not isinstance(user_content, dict)


def test_call_anthropic_wraps_sdk_error_without_body_or_key() -> None:
    """
    [Local] schema detection: verifies call anthropic wraps sdk error without body or key.

    Scenario:
        an SDK error surfaces only the exception class + status, never its body/key.

    Boundaries:
        - the client constructor raises; the fake key must appear nowhere in the error.

    On failure, first check:
        - a leaked body/key is a credential/PII disclosure (AIS-8/AIS-16).
    """

    class _FakeStatusError(Exception):
        """Minimal Anthropic-style status error with a status_code attribute for mocking HTTP 429."""

        status_code = 429

    secret_body = "rate limited for key sk-ant-test-XXXX; request body: <pruned html>"
    with patch.object(sd, "anthropic") as mock_anthropic:
        mock_anthropic.Anthropic.side_effect = _FakeStatusError(secret_body)
        with pytest.raises(sd.SchemaDetectionError) as exc_info:
            sd.call_anthropic("instructions", "snapshot", "sk-ant-test-XXXX")

    message = str(exc_info.value)
    assert "Anthropic API request failed" in message
    assert "_FakeStatusError" in message
    assert "HTTP 429" in message
    assert "sk-ant-test-XXXX" not in message
    assert secret_body not in message


def test_call_anthropic_raises_on_truncated_response() -> None:
    """
    [Local] schema detection: verifies call anthropic raises on truncated response.

    Scenario:
        a max_tokens stop reason is treated as truncation and never parsed.

    Boundaries:
        - client mocked; create returns stop_reason=max_tokens.

    On failure, first check:
        - a half-written JSON answer would be parsed as if complete.
    """
    client = _fake_client(_fake_message('{"BOOK_TITLE":', stop_reason="max_tokens"))
    with patch.object(sd, "anthropic") as mock_anthropic:
        mock_anthropic.Anthropic.return_value = client
        with pytest.raises(sd.SchemaDetectionError, match="truncated"):
            sd.call_anthropic("instructions", "snapshot", "test-key")


def test_call_anthropic_raises_on_context_window_exceeded() -> None:
    """
    [Local] schema detection: verifies call anthropic raises on context window exceeded.

    Scenario:
        a model_context_window_exceeded stop reason is reported as truncation, not bad JSON.

    Boundaries:
        - client mocked; create returns stop_reason=model_context_window_exceeded.

    On failure, first check:
        - the partial answer is parsed, or the operator sees a misleading "not valid JSON".
    """
    client = _fake_client(_fake_message('{"BOOK_TITLE":', stop_reason="model_context_window_exceeded"))
    with patch.object(sd, "anthropic") as mock_anthropic:
        mock_anthropic.Anthropic.return_value = client
        with pytest.raises(sd.SchemaDetectionError, match="context window"):
            sd.call_anthropic("instructions", "snapshot", "test-key")


def test_call_anthropic_raises_on_refusal_naming_category() -> None:
    """
    [Local] schema detection: verifies call anthropic raises on refusal naming category.

    Scenario:
        a refusal stop reason raises and names stop_details.category when present.

    Boundaries:
        - client mocked; create returns stop_reason=refusal + a category.

    On failure, first check:
        - a refusal would be parsed as content, or the category would be dropped.
    """
    details = SimpleNamespace(category="cyber", explanation="nope")
    client = _fake_client(_fake_message("", stop_reason="refusal", stop_details=details))
    with patch.object(sd, "anthropic") as mock_anthropic:
        mock_anthropic.Anthropic.return_value = client
        with pytest.raises(sd.SchemaDetectionError, match="refused.*category: cyber"):
            sd.call_anthropic("instructions", "snapshot", "test-key")


def test_call_anthropic_skips_thinking_blocks() -> None:
    """
    [Local] schema detection: verifies call anthropic skips thinking blocks.

    Scenario:
        _extract_text concatenates only text blocks, skipping thinking blocks.

    Boundaries:
        - client mocked; content carries a thinking block before the text block.

    On failure, first check:
        - a thinking summary would leak into the JSON parse.
    """
    blocks = [
        SimpleNamespace(type="thinking", thinking="let me reason about selectors"),
        SimpleNamespace(type="text", text='{"BOOK_TITLE": {"selector": "#t"}}'),
    ]
    client = _fake_client(_fake_message("", blocks=blocks))
    with patch.object(sd, "anthropic") as mock_anthropic:
        mock_anthropic.Anthropic.return_value = client
        text, _ = sd.call_anthropic("instructions", "snapshot", "test-key")

    assert text == '{"BOOK_TITLE": {"selector": "#t"}}'
    assert "reason about selectors" not in text


def test_call_anthropic_raises_on_empty_content() -> None:
    """
    [Local] schema detection: verifies call anthropic raises on empty content.

    Scenario:
        a response with no text blocks fails loudly.

    Boundaries:
        - client mocked; content is empty.

    On failure, first check:
        - an empty reply would be treated as a valid (empty) proposal set.
    """
    client = _fake_client(_fake_message("", blocks=[]))
    with patch.object(sd, "anthropic") as mock_anthropic:
        mock_anthropic.Anthropic.return_value = client
        with pytest.raises(sd.SchemaDetectionError, match="no text content"):
            sd.call_anthropic("instructions", "snapshot", "test-key")


# --------------------------------------------------------------------------- #
# token-budget pre-flight (_fit_snapshot_to_budget via call_anthropic)
# --------------------------------------------------------------------------- #
def test_over_budget_prompt_is_repruned_then_sent() -> None:
    """
    [Local] schema detection: verifies over budget prompt is repruned then sent.

    Scenario:
        an over-budget prompt is re-pruned until it fits, then sent once.

    Boundaries:
        - count_tokens returns over-budget first, then under-budget; create mocked.

    On failure, first check:
        - the oversized prompt would be sent, or the snapshot never shrunk.
    """
    client = MagicMock()
    client.messages.count_tokens.side_effect = [
        SimpleNamespace(input_tokens=90_000),
        SimpleNamespace(input_tokens=20_000),
    ]
    client.messages.create.return_value = _fake_message('{"BOOK_TITLE": {"selector": "#t"}}')
    big_snapshot = "x" * 50_000

    with patch.object(sd, "anthropic") as mock_anthropic:
        mock_anthropic.Anthropic.return_value = client
        text, _ = sd.call_anthropic("instructions", big_snapshot, "test-key", max_input_tokens=40_000)

    assert text == '{"BOOK_TITLE": {"selector": "#t"}}'
    client.messages.create.assert_called_once()
    # The snapshot was halved before the second (fitting) count.
    _, kwargs = client.messages.create.call_args
    assert len(kwargs["messages"][0]["content"]) < len(big_snapshot)


def test_still_over_budget_after_min_prune_raises_without_sending() -> None:
    """
    [Local] schema detection: verifies still over budget after min prune raises without sending.

    Scenario:
        a prompt still over budget at the 10k-char floor raises without any send.

    Boundaries:
        - count_tokens always returns over-budget; create must never be called.

    On failure, first check:
        - unbounded spend on a prompt that can never fit.
    """
    client = MagicMock()
    client.messages.count_tokens.return_value = SimpleNamespace(input_tokens=99_999)
    big_snapshot = "x" * 50_000

    with patch.object(sd, "anthropic") as mock_anthropic:
        mock_anthropic.Anthropic.return_value = client
        with pytest.raises(sd.SchemaDetectionError, match="still exceeds.*budget"):
            sd.call_anthropic("instructions", big_snapshot, "test-key", max_input_tokens=40_000)

    client.messages.create.assert_not_called()


def test_count_tokens_sdk_error_is_wrapped_body_free() -> None:
    """
    [Local] schema detection: verifies count tokens sdk error is wrapped body free.

    Scenario:
        an SDK error during the token-count pre-flight is wrapped without its body.

    Boundaries:
        - count_tokens raises; create must never run.

    On failure, first check:
        - a raw SDK body (possibly echoing the prompt) would leak.
    """
    client = MagicMock()
    client.messages.count_tokens.side_effect = RuntimeError("secret body sk-ant-test-XXXX")

    with patch.object(sd, "anthropic") as mock_anthropic:
        mock_anthropic.Anthropic.return_value = client
        with pytest.raises(sd.SchemaDetectionError) as exc_info:
            sd.call_anthropic("instructions", "snapshot", "sk-ant-test-XXXX")

    message = str(exc_info.value)
    assert "RuntimeError" in message
    assert "sk-ant-test-XXXX" not in message
    client.messages.create.assert_not_called()


def test_create_sdk_error_is_wrapped_body_free() -> None:
    """
    [Local] schema detection: verifies create sdk error is wrapped body free.

    Scenario:
        an SDK error from messages.create is wrapped without its body.

    Boundaries:
        - count_tokens is fine; create raises.

    On failure, first check:
        - a raw SDK body would leak into the error surface.
    """
    client = MagicMock()
    client.messages.count_tokens.return_value = SimpleNamespace(input_tokens=100)
    client.messages.create.side_effect = RuntimeError("secret body sk-ant-test-XXXX")

    with patch.object(sd, "anthropic") as mock_anthropic:
        mock_anthropic.Anthropic.return_value = client
        with pytest.raises(sd.SchemaDetectionError) as exc_info:
            sd.call_anthropic("instructions", "snapshot", "sk-ant-test-XXXX")

    message = str(exc_info.value)
    assert "Anthropic API request failed" in message
    assert "sk-ant-test-XXXX" not in message


# --------------------------------------------------------------------------- #
# fetch_pruned_html
# --------------------------------------------------------------------------- #
async def test_fetch_pruned_html_returns_pruned_snapshot() -> None:
    """
    [Local] schema detection: verifies that fetch_pruned_html strips scripts and returns a pruned snapshot containing visible text.

    Scenario:
        Given a FakePage whose content has a <script> block and an <h1>Title</h1>
        When sd.fetch_pruned_html("https://x/book", page) is called
        Then "Title" is in the result, "x()" (the script body) is not, and page.goto/page.route are each called once.

    Boundaries:
        - The pruning path and Playwright navigation/route calls; no real browser

    On failure, first check:
        - The prune_html logic and route_handler setup in fetch_pruned_html
    """
    page = FakePage(content="<html><script>x()</script><h1>Title</h1></html>")
    result = await sd.fetch_pruned_html("https://x/book", page)
    assert "Title" in result
    assert "x()" not in result
    page.goto.assert_awaited_once()
    page.route.assert_awaited_once()


async def test_fetch_pruned_html_raises_on_timeout() -> None:
    """
    [Local] schema detection: verifies that a PlaywrightTimeoutError from page.goto raises SchemaDetectionError("Timed out loading").

    Scenario:
        Given a FakePage whose goto raises PlaywrightTimeoutError
        When sd.fetch_pruned_html("https://x/book", page) is called
        Then SchemaDetectionError matching "Timed out loading" is raised.

    Boundaries:
        - The navigation-timeout error path; no real browser

    On failure, first check:
        - The PlaywrightTimeoutError -> SchemaDetectionError conversion in fetch_pruned_html
    """
    page = FakePage()
    page.goto = AsyncMock(side_effect=PlaywrightTimeoutError("nav timeout"))
    with pytest.raises(sd.SchemaDetectionError, match="Timed out loading"):
        await sd.fetch_pruned_html("https://x/book", page)


async def test_fetch_pruned_html_raises_on_generic_load_error() -> None:
    """
    [Local] schema detection: verifies that a generic PlaywrightError from page.goto raises SchemaDetectionError("Failed to load").

    Scenario:
        Given a FakePage whose goto raises PlaywrightError("connection refused")
        When sd.fetch_pruned_html("https://x/book", page) is called
        Then SchemaDetectionError matching "Failed to load" is raised.

    Boundaries:
        - The generic navigation-error path; no real browser

    On failure, first check:
        - The PlaywrightError -> SchemaDetectionError conversion in fetch_pruned_html
    """
    page = FakePage()
    page.goto = AsyncMock(side_effect=PlaywrightError("connection refused"))
    with pytest.raises(sd.SchemaDetectionError, match="Failed to load"):
        await sd.fetch_pruned_html("https://x/book", page)


# --------------------------------------------------------------------------- #
# validate_across_samples
# --------------------------------------------------------------------------- #
async def test_validate_across_samples_consistent_match() -> None:
    """
    [Local] schema detection: verifies that a selector matching on every sample is marked consistent with per-URL snippets.

    Scenario:
        Given two FakePages both matching "#t" and a BOOK_TITLE proposal with selector "#t"
        When sd.validate_across_samples(proposals, samples) is called
        Then BOOK_TITLE is consistent=True, all per_url entries are matched, and the first snippet is "Book One".

    Boundaries:
        - The "consistent match" path; FakeLocator/FakePage, no real browser

    On failure, first check:
        - The consistency logic and snippet extraction in validate_across_samples
    """
    page1 = FakePage(locators={"#t": FakeLocator("Book One")})
    page2 = FakePage(locators={"#t": FakeLocator("Book Two")})
    samples = [{"url": "u1", "page": page1}, {"url": "u2", "page": page2}]
    proposals = {"BOOK_TITLE": {"selector": "#t", "rationale": ""}}

    result = await sd.validate_across_samples(proposals, samples)

    assert result["BOOK_TITLE"]["consistent"] is True
    per_url = result["BOOK_TITLE"]["per_url"]
    assert all(entry["matched"] for entry in per_url)
    assert per_url[0]["snippet"] == "Book One"


async def test_validate_across_samples_flags_inconsistency() -> None:
    # ".tag" matches on page1 only -> inconsistent, must be flagged.
    """
    [Local] schema detection: verifies that a selector matching on some but not all samples is marked inconsistent.

    Scenario:
        Given page1 matches ".tag" but page2 does not, and a TAGS proposal with selector ".tag"
        When sd.validate_across_samples(proposals, samples) is called
        Then TAGS is consistent=False and per_url matched entries are [True, False].

    Boundaries:
        - The "inconsistent" detection path; FakeLocator/FakePage, no real browser

    On failure, first check:
        - The cross-sample consistency check in validate_across_samples
    """
    page1 = FakePage(locators={".tag": FakeLocator(["a", "b"])})
    page2 = FakePage(locators={})
    samples = [{"url": "u1", "page": page1}, {"url": "u2", "page": page2}]
    proposals = {"TAGS": {"selector": ".tag", "rationale": ""}}

    result = await sd.validate_across_samples(proposals, samples)

    assert result["TAGS"]["consistent"] is False
    matched = [entry["matched"] for entry in result["TAGS"]["per_url"]]
    assert matched == [True, False]


async def test_validate_across_samples_null_selector_is_miss() -> None:
    """
    [Local] schema detection: verifies that a null/None selector is recorded as a miss with snippet "null selector".

    Scenario:
        Given a proposal with selector=None for ISBN10
        When sd.validate_across_samples(proposals, samples) is called
        Then the per_url entry has matched=False and snippet="null selector".

    Boundaries:
        - The null-selector handling path; no real browser

    On failure, first check:
        - The None-selector branch in validate_across_samples
    """
    page = FakePage(locators={})
    samples = [{"url": "u1", "page": page}]
    proposals = {"ISBN10": {"selector": None, "rationale": "absent"}}

    result = await sd.validate_across_samples(proposals, samples)

    entry = result["ISBN10"]["per_url"][0]
    assert entry["matched"] is False
    assert entry["snippet"] == "null selector"


async def test_validate_across_samples_bad_selector_does_not_abort() -> None:
    """
    [Local] schema detection: verifies that a PlaywrightError from a bad selector is caught and recorded as "query error" without aborting.

    Scenario:
        Given a FakePage whose locator for "::bad::" raises PlaywrightError
        When sd.validate_across_samples(proposals, samples) is called
        Then the per_url entry has matched=False and snippet="query error", and no exception propagates.

    Boundaries:
        - The selector-evaluation error path; FakePage raises on the bad selector

    On failure, first check:
        - The PlaywrightError catch in validate_across_samples
    """
    page = FakePage(locators={"::bad::": FakeLocator([], raises=PlaywrightError("bad selector"))})
    samples = [{"url": "u1", "page": page}]
    proposals = {"TAGS": {"selector": "::bad::", "rationale": ""}}

    result = await sd.validate_across_samples(proposals, samples)

    entry = result["TAGS"]["per_url"][0]
    assert entry["matched"] is False
    assert entry["snippet"] == "query error"


class _HangingLocator(FakeLocator):
    """A locator whose ``count()`` never resolves - stands in for a pathological selector whose
    evaluation would block the event loop forever absent the AIS-7 timeout."""

    async def count(self) -> int:
        """Raise after a configurable number of calls to test timeout behavior."""
        await asyncio.Event().wait()  # pragma: no cover - cancelled by the AIS-7 wait_for timeout
        return 0


async def test_pathological_selector_is_bounded_by_timeout_not_hung(monkeypatch) -> None:
    """
    [Local] schema detection: verifies pathological selector is bounded by timeout not hung.

    Scenario:
        a selector whose count() never resolves is bounded by the AIS-7 timeout, recorded as a non-match, and the run completes instead of hanging.

    Boundaries:
        - SELECTOR_EVAL_TIMEOUT_S is shrunk to 50 ms; a hanging fake locator is queried.

    On failure, first check:
        - a crafted selector/page could block the tool (and a CI drift job) indefinitely.
    """
    monkeypatch.setattr(sd, "SELECTOR_EVAL_TIMEOUT_S", 0.05)
    samples = [{"url": "u0", "page": FakePage(locators={"#hang": _HangingLocator()})}]
    proposals = {"BOOK_TITLE": {"selector": "#hang", "rationale": ""}}

    result = await asyncio.wait_for(sd.validate_across_samples(proposals, samples), timeout=5)

    entry = result["BOOK_TITLE"]["per_url"][0]
    assert entry["matched"] is False
    assert entry["snippet"] == "query error"


async def test_check_current_only_still_evaluates_has_text_current_selector(monkeypatch) -> None:
    """
    [Local] schema detection: verifies check current only still evaluates has text current selector.

    Scenario:
        --check-current-only still evaluates a current :has-text selector - the AIS-6 rejection applies to AI PROPOSALS only, never to the current selectors the drift check queries.

    Boundaries:
        - a one-field site whose ISBN13 is a :has-text selector the FakePage matches; no AI.

    On failure, first check:
        - the drift check would wrongly reject/skip the project's real :has-text selectors.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    has_text_sel = "li:has-text('ISBN:') > span.value.t-isbn"
    monkeypatch.setattr(sd, "site_constants", {"tiny": {"BASE_URL": "https://x", "ISBN13": has_text_sel}})
    page = FakePage(locators={has_text_sel: FakeLocator("9781234567890")}, content="<html></html>")
    browser = FakeBrowser(page_factory=lambda *a, **k: page)

    with patch.object(sd, "async_playwright", build_playwright_factory(browser)):
        report, usage_line, exit_code = await sd.detect_schema("tiny", urls=["u0"], check_current_only=True)

    # The current :has-text selector was queried, matched, and is NOT shown as rejected.
    assert exit_code == 0
    assert has_text_sel in report
    assert "rejected" not in report
    assert "no AI call" in usage_line
    browser.close.assert_awaited_once()


# --------------------------------------------------------------------------- #
# detect_schema (full pipeline)
# --------------------------------------------------------------------------- #
def _amazon_pages() -> tuple[FakePage, FakePage]:
    """Two sample pages: BOOK_TITLE matches on both (consistent); TAGS matches only the
    first (inconsistent, must be flagged)."""
    page1 = FakePage(
        locators={"#productTitle": FakeLocator("Book One"), ".tag": FakeLocator(["t1", "t2"])},
        content="<html><h1 id='productTitle'>Book One</h1></html>",
    )
    page2 = FakePage(
        locators={"#productTitle": FakeLocator("Book Two")},
        content="<html><h1 id='productTitle'>Book Two</h1></html>",
    )
    return page1, page2


async def test_detect_schema_happy_path_flags_inconsistency(monkeypatch) -> None:
    """
    [Local] schema detection: verifies the full detect_schema pipeline: consistent proposals show [MATCH], inconsistent ones show INCONSISTENT, and call_anthropic is invoked once per URL.

    Scenario:
        Given two FakePages where BOOK_TITLE matches both and TAGS matches only page1, and call_anthropic returning proposals for those two fields
        When sd.detect_schema("amazon", ["https://x/1", "https://x/2"]) is called
        Then "#productTitle" and "[MATCH]" are in the report, "INCONSISTENT" is in the report, call_anthropic was called twice, and the browser is closed.

    Boundaries:
        - Full pipeline with Playwright and Anthropic both mocked; 2 URLs

    On failure, first check:
        - The validation loop, inconsistency detection, and per-URL call_anthropic invocation in detect_schema
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    page1, page2 = _amazon_pages()
    pages = iter([page1, page2])
    browser = FakeBrowser(page_factory=lambda *a, **k: next(pages))

    proposals_json = json.dumps(
        {
            "BOOK_TITLE": {"selector": "#productTitle", "rationale": "stable id"},
            "TAGS": {"selector": ".tag", "rationale": "class chain"},
        }
    )

    with (
        patch.object(sd, "async_playwright", build_playwright_factory(browser)),
        patch.object(sd, "call_anthropic", MagicMock(return_value=(proposals_json, _fake_usage()))) as call_ai,
    ):
        report, usage_line, _exit = await sd.detect_schema("amazon", ["https://x/1", "https://x/2"])

    assert "#productTitle" in report
    assert "[MATCH]" in report
    # TAGS matched page1 but not page2 -> flagged, not silently accepted.
    assert "INCONSISTENT" in report
    assert "proposal only" in report.lower()
    # One request per sample URL.
    assert call_ai.call_count == 2
    assert "requests=2" in usage_line
    browser.close.assert_awaited_once()


async def test_detect_schema_sums_usage_across_one_request_per_url(monkeypatch) -> None:
    """
    [Local] schema detection: verifies detect schema sums usage across one request per url.

    Scenario:
        a 3-URL run makes 3 requests and sums each response's usage into one line.

    Boundaries:
        - call_anthropic is mocked per URL; cache-read is non-zero on requests 2-3.

    On failure, first check:
        - the usage line would reflect one request, or drop cached-token totals.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    pages = iter(
        [
            FakePage(locators={"#productTitle": FakeLocator("A")}, content="<h1 id='productTitle'>A</h1>"),
            FakePage(locators={"#productTitle": FakeLocator("B")}, content="<h1 id='productTitle'>B</h1>"),
            FakePage(locators={"#productTitle": FakeLocator("C")}, content="<h1 id='productTitle'>C</h1>"),
        ]
    )
    browser = FakeBrowser(page_factory=lambda *a, **k: next(pages))
    proposals_json = json.dumps({"BOOK_TITLE": {"selector": "#productTitle", "rationale": "id"}})
    usages = [
        (proposals_json, _fake_usage(input_tokens=1000, cache_creation=500, output_tokens=10)),
        (proposals_json, _fake_usage(input_tokens=100, cache_read=900, output_tokens=12)),
        (proposals_json, _fake_usage(input_tokens=100, cache_read=900, output_tokens=8)),
    ]

    with (
        patch.object(sd, "async_playwright", build_playwright_factory(browser)),
        patch.object(sd, "call_anthropic", MagicMock(side_effect=usages)) as call_ai,
    ):
        _, usage_line, _exit = await sd.detect_schema("amazon", ["https://x/1", "https://x/2", "https://x/3"])

    assert call_ai.call_count == 3
    assert "requests=3" in usage_line
    assert "input_tokens=1200" in usage_line
    assert "cache_read_input_tokens=1800" in usage_line
    assert "cache_creation_input_tokens=500" in usage_line
    assert "output_tokens=30" in usage_line
    assert "model=claude-sonnet-5" in usage_line


async def test_detect_schema_rejects_too_many_urls(monkeypatch) -> None:
    """
    [Local] schema detection: verifies detect schema rejects too many urls.

    Scenario:
        more than MAX_SAMPLE_URLS sample URLs is rejected before any work.

    Boundaries:
        - no API key set, no browser/anthropic patched - the cap must trip first.

    On failure, first check:
        - an unbounded sample set runs up unbounded Anthropic spend.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    too_many = [f"https://x/{i}" for i in range(sd.MAX_SAMPLE_URLS + 1)]
    with pytest.raises(sd.SchemaDetectionError, match="per-run cap is 5"):
        await sd.detect_schema("amazon", too_many)


async def test_detect_schema_no_detail_fields_fails_loudly(monkeypatch) -> None:
    """
    [Local] schema detection: verifies detect schema no detail fields fails loudly.

    Scenario:
        a site whose entry defines no detail-page selectors fails loudly.

    Boundaries:
        - site_constants is monkeypatched to an endpoint-only entry; no browser launches.

    On failure, first check:
        - a run with nothing to propose would proceed to a pointless API call.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(sd, "site_constants", {"emptysite": {"BASE_URL": "https://x"}})
    with pytest.raises(sd.SchemaDetectionError, match="No detail-page selector fields"):
        await sd.detect_schema("emptysite", ["https://x/1"])


async def test_detect_schema_missing_api_key(monkeypatch) -> None:
    """
    [Local] schema detection: verifies that a missing ANTHROPIC_API_KEY raises SchemaDetectionError before any browser or API call.

    Scenario:
        Given ANTHROPIC_API_KEY is deleted from the environment
        When sd.detect_schema("amazon", ["https://x/1"]) is called
        Then SchemaDetectionError matching "ANTHROPIC_API_KEY is not set" is raised.

    Boundaries:
        - The missing-API-key pre-flight; no browser or Anthropic call should be made

    On failure, first check:
        - The ANTHROPIC_API_KEY presence check in detect_schema
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(sd.SchemaDetectionError, match="ANTHROPIC_API_KEY is not set"):
        await sd.detect_schema("amazon", ["https://x/1"])


async def test_detect_schema_malformed_ai_json_fails_loudly(monkeypatch) -> None:
    """
    [Local] schema detection: verifies that a non-JSON AI response raises SchemaDetectionError and the browser is still closed.

    Scenario:
        Given call_anthropic returning "not json at all" and a loadable FakePage
        When sd.detect_schema("amazon", ["https://x/1"]) is called
        Then SchemaDetectionError matching "not valid JSON" is raised and browser.close is called.

    Boundaries:
        - The malformed-AI-response path; browser must be closed even on failure

    On failure, first check:
        - The JSON parse and browser cleanup in detect_schema
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    page1, _ = _amazon_pages()
    browser = FakeBrowser(page_factory=lambda *a, **k: page1)

    with (
        patch.object(sd, "async_playwright", build_playwright_factory(browser)),
        patch.object(sd, "call_anthropic", MagicMock(return_value=("not json at all", _fake_usage()))),
    ):
        with pytest.raises(sd.SchemaDetectionError, match="not valid JSON"):
            await sd.detect_schema("amazon", ["https://x/1"])

    # Browser is still closed even on a mid-pipeline failure.
    browser.close.assert_awaited_once()


async def test_detect_schema_page_load_failure_fails_loudly(monkeypatch) -> None:
    """
    [Local] schema detection: verifies that a page-load failure raises SchemaDetectionError, skips the AI call, and still closes the browser.

    Scenario:
        Given a FakePage whose goto raises PlaywrightError("connection refused")
        When sd.detect_schema("amazon", ["https://x/1"]) is called
        Then SchemaDetectionError matching "Failed to load" is raised, call_anthropic is never called, and browser.close is called.

    Boundaries:
        - The page-load failure path; AI must not be consulted and browser must be closed

    On failure, first check:
        - The page-load error propagation and browser cleanup in detect_schema
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    bad_page = FakePage()
    bad_page.goto = AsyncMock(side_effect=PlaywrightError("connection refused"))
    browser = FakeBrowser(page_factory=lambda *a, **k: bad_page)

    with (
        patch.object(sd, "async_playwright", build_playwright_factory(browser)),
        patch.object(sd, "call_anthropic", MagicMock()) as call_ai,
    ):
        with pytest.raises(sd.SchemaDetectionError, match="Failed to load"):
            await sd.detect_schema("amazon", ["https://x/1"])

    # AI is never consulted if a page won't load; browser still closed.
    call_ai.assert_not_called()
    browser.close.assert_awaited_once()


async def test_detect_schema_unknown_site() -> None:
    """
    [Local] schema detection: verifies that an unrecognized site name raises SchemaDetectionError("Unknown site").

    Scenario:
        Given no API key or browser setup
        When sd.detect_schema("bogus", ["https://x/1"]) is called
        Then SchemaDetectionError matching "Unknown site" is raised.

    Boundaries:
        - The site-validation pre-flight; nothing else runs

    On failure, first check:
        - The site-name check in detect_schema
    """
    with pytest.raises(sd.SchemaDetectionError, match="Unknown site"):
        await sd.detect_schema("bogus", ["https://x/1"])


async def test_detect_schema_no_urls() -> None:
    """
    [Local] schema detection: verifies that an empty URL list raises SchemaDetectionError("At least one sample").

    Scenario:
        Given an empty urls list
        When sd.detect_schema("amazon", []) is called
        Then SchemaDetectionError matching "At least one sample" is raised.

    Boundaries:
        - The empty-url pre-flight; nothing else runs

    On failure, first check:
        - The url-count check in detect_schema
    """
    with pytest.raises(sd.SchemaDetectionError, match="At least one sample"):
        await sd.detect_schema("amazon", [])


# --------------------------------------------------------------------------- #
# validate_search_across_samples (card-relative fractions)
# --------------------------------------------------------------------------- #
def _search_page(card_sel="CARD", title_sel="TITLE", n_cards=8, n_title=8, next_sel="NEXT", content="<html></html>") -> FakePage:
    """A FakePage for a search-results page: ``n_cards`` cards, ``n_title`` of which carry a
    card-relative title element, plus a page-level next-page selector."""
    cards = []
    for i in range(n_cards):
        subs = {title_sel: FakeLocator(f"Book {i}")} if i < n_title else {}
        cards.append({"text": f"card {i}", "sub": subs})
    locators = {card_sel: FakeLocator(cards), next_sel: FakeLocator(["Next"])}
    return FakePage(locators=locators, content=content)


def _search_proposals() -> dict[str, dict[str, str]]:
    """Build a minimal search-proposal dict for search-validation tests."""
    return {
        "SEARCH_BOOK_CARD": {"selector": "CARD", "rationale": "card container"},
        "SEARCH_TITLE": {"selector": "TITLE", "rationale": "card title"},
        "SEARCH_NEXT_PAGE_SELECTOR": {"selector": "NEXT", "rationale": "pagination"},
    }


async def test_search_validation_reports_card_count_and_match_fraction() -> None:
    """
    [Local] schema detection: verifies search validation reports card count and match fraction.

    Scenario:
        each card-relative selector reports the card count and per-sample match fraction.

    Boundaries:
        - FakePage enumerates 8 cards; the title selector matches all of them.

    On failure, first check:
        - a fully-matching per-card selector would not report its 8/8 fraction.
    """
    samples = [{"url": "u0", "page": _search_page(n_title=8)}]
    result = await sd.validate_search_across_samples(_search_proposals(), samples)

    # Card container (page-level) sees all 8 cards.
    assert result["SEARCH_BOOK_CARD"]["per_url"][0]["count"] == 8
    assert result["SEARCH_BOOK_CARD"]["per_url"][0]["matched"] is True
    # Card-relative title matched every card.
    title = result["SEARCH_TITLE"]["per_url"][0]
    assert title["card_count"] == 8
    assert title["match_count"] == 8
    assert title["fraction"] == 1.0
    assert title["matched"] is True
    assert result["SEARCH_TITLE"]["partial"] is False
    assert title["snippet"] == "Book 0"
    # Page-level pagination selector validated against the whole page.
    assert result["SEARCH_NEXT_PAGE_SELECTOR"]["per_url"][0]["matched"] is True


async def test_search_validation_caps_cards_per_sample() -> None:
    """
    [Local] schema detection: verifies search validation caps cards per sample.

    Scenario:
        card-relative validation evaluates at most MAX_CARDS_PER_SAMPLE cards per sample, and the reported fraction is computed over the evaluated cards (auditor card-cap caveat).

    Boundaries:
        - a page with 60 matching cards; the card-relative work must stop at the cap.

    On failure, first check:
        - a page-controlled card count could multiply the per-card locator work without bound.
    """
    samples = [{"url": "u0", "page": _search_page(n_cards=60, n_title=60)}]
    result = await sd.validate_search_across_samples(_search_proposals(), samples)

    title = result["SEARCH_TITLE"]["per_url"][0]
    assert title["card_count"] == sd.MAX_CARDS_PER_SAMPLE == 50
    assert title["match_count"] == sd.MAX_CARDS_PER_SAMPLE
    assert title["fraction"] == 1.0
    assert title["matched"] is True


async def test_card_relative_selector_below_80_percent_flagged_partial() -> None:
    """
    [Local] schema detection: verifies card relative selector below 80 percent flagged partial.

    Scenario:
        a card-relative selector matching under 80% of cards is flagged partial.

    Boundaries:
        - 8 cards, only 5 carry the title element -> fraction 0.625.

    On failure, first check:
        - a per-card selector that silently misses a third of the cards looks valid.
    """
    samples = [{"url": "u0", "page": _search_page(n_cards=8, n_title=5)}]
    result = await sd.validate_search_across_samples(_search_proposals(), samples)

    title = result["SEARCH_TITLE"]["per_url"][0]
    assert title["card_count"] == 8
    assert title["match_count"] == 5
    assert title["fraction"] == 0.625
    assert title["matched"] is False
    assert result["SEARCH_TITLE"]["partial"] is True


async def test_search_validation_card_relative_query_error_counts_as_miss() -> None:
    """
    [Local] schema detection: verifies search validation card relative query error counts as miss.

    Scenario:
        a card-relative selector Playwright rejects on a card counts as a miss, never aborts.

    Boundaries:
        - one card's title locator raises; the run must continue and flag partial.

    On failure, first check:
        - a hostile/garbage selector would abort the whole validation.
    """
    card = {"text": "card", "sub": {"TITLE": FakeLocator([], raises=PlaywrightError("bad selector"))}}
    page = FakePage(locators={"CARD": FakeLocator([card]), "NEXT": FakeLocator(["Next"])})
    samples = [{"url": "u0", "page": page}]
    result = await sd.validate_search_across_samples(_search_proposals(), samples)

    title = result["SEARCH_TITLE"]["per_url"][0]
    assert title["match_count"] == 0
    assert result["SEARCH_TITLE"]["partial"] is True


async def test_search_validation_null_card_relative_selector_is_partial() -> None:
    """
    [Local] schema detection: verifies search validation null card relative selector is partial.

    Scenario:
        a null card-relative proposal matches no card and is flagged partial.

    Boundaries:
        - cards exist, but the title proposal's selector is None.

    On failure, first check:
        - a null per-card selector would silently appear to validate.
    """
    proposals = _search_proposals()
    proposals["SEARCH_TITLE"] = {"selector": None, "rationale": "not found"}
    samples = [{"url": "u0", "page": _search_page(n_cards=4, n_title=4)}]
    result = await sd.validate_search_across_samples(proposals, samples)

    title = result["SEARCH_TITLE"]["per_url"][0]
    assert title["card_count"] == 4
    assert title["match_count"] == 0
    assert result["SEARCH_TITLE"]["partial"] is True


async def test_search_validation_null_card_selector_yields_zero_cards() -> None:
    """
    [Local] schema detection: verifies search validation null card selector yields zero cards.

    Scenario:
        a null card-container proposal yields zero cards, so card-relative fields are partial.

    Boundaries:
        - the SEARCH_BOOK_CARD proposal's selector is None.

    On failure, first check:
        - card-relative validation would crash instead of reporting zero cards.
    """
    proposals = _search_proposals()
    proposals["SEARCH_BOOK_CARD"] = {"selector": None, "rationale": "not found"}
    samples = [{"url": "u0", "page": _search_page()}]
    result = await sd.validate_search_across_samples(proposals, samples)

    assert result["SEARCH_TITLE"]["per_url"][0]["card_count"] == 0
    assert result["SEARCH_TITLE"]["partial"] is True
    # The card container field itself records a null-selector miss.
    assert result["SEARCH_BOOK_CARD"]["per_url"][0]["snippet"] == "null selector"


async def test_search_validation_bad_card_selector_does_not_abort() -> None:
    """
    [Local] schema detection: verifies search validation bad card selector does not abort.

    Scenario:
        a card-container selector whose enumeration raises yields zero cards, not a crash.

    Boundaries:
        - page.locator(card).all() raises; card-relative fields must still report.

    On failure, first check:
        - a bad card selector would abort the whole search validation.
    """

    class _RaisingAllPage:
        """Fake Playwright page whose locator.all() always raises PlaywrightError, simulating a broken card selector."""

        def locator(self, selector) -> MagicMock:
            """Return a MagicMock locator whose .all() raises and .count() returns 0."""
            loc = MagicMock()
            loc.all = AsyncMock(side_effect=PlaywrightError("all boom"))
            loc.count = AsyncMock(return_value=0)
            first = MagicMock()
            first.text_content = AsyncMock(return_value="")
            loc.first = first
            return loc

    samples = [{"url": "u0", "page": _RaisingAllPage()}]
    result = await sd.validate_search_across_samples(_search_proposals(), samples)

    assert result["SEARCH_TITLE"]["per_url"][0]["card_count"] == 0
    assert result["SEARCH_TITLE"]["partial"] is True


async def test_search_validation_no_card_field_in_proposals() -> None:
    """
    [Local] schema detection: verifies search validation no card field in proposals.

    Scenario:
        proposals with no card-container key still validate card-relative fields (zero cards).

    Boundaries:
        - only a card-relative field is proposed; next((card key)) is None.

    On failure, first check:
        - the missing-card-field branch would raise a KeyError.
    """
    proposals = {"SEARCH_TITLE": {"selector": "TITLE", "rationale": "title"}}
    samples = [{"url": "u0", "page": _search_page()}]
    result = await sd.validate_search_across_samples(proposals, samples)

    assert result["SEARCH_TITLE"]["per_url"][0]["card_count"] == 0
    assert result["SEARCH_TITLE"]["partial"] is True


# --------------------------------------------------------------------------- #
# detect_schema - search page_type end to end
# --------------------------------------------------------------------------- #
async def test_detect_schema_search_page_type_end_to_end(monkeypatch) -> None:
    """
    [Local] schema detection: verifies detect schema search page type end to end.

    Scenario:
        page_type='search' proposes SEARCH_* selectors and validates per-card fractions.

    Boundaries:
        - Playwright faked, anthropic mocked; two sample search pages.

    On failure, first check:
        - a search run would use detail-page selectors or skip card-fraction validation.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    # Page 1: title on all 8 cards; page 2: title on only 4 of 8 -> partial.
    pages = iter([_search_page(n_cards=8, n_title=8), _search_page(n_cards=8, n_title=4)])
    browser = FakeBrowser(page_factory=lambda *a, **k: next(pages))
    proposals_json = json.dumps(
        {
            "SEARCH_BOOK_CARD": {"selector": "CARD", "rationale": "card container"},
            "SEARCH_TITLE": {"selector": "TITLE", "rationale": "card title"},
            "SEARCH_NEXT_PAGE_SELECTOR": {"selector": "NEXT", "rationale": "pagination"},
        }
    )

    with (
        patch.object(sd, "async_playwright", build_playwright_factory(browser)),
        patch.object(sd, "call_anthropic", MagicMock(return_value=(proposals_json, _fake_usage()))) as call_ai,
    ):
        report, usage_line, _exit = await sd.detect_schema("amazon", ["https://x/1", "https://x/2"], page_type="search")

    # The instruction block sent is the search variant (card-relative semantics).
    instruction_arg = call_ai.call_args.args[0]
    assert "INSIDE a single card" in instruction_arg
    assert "cards matched" in report
    assert "!! PARTIAL" in report
    assert "requests=2" in usage_line
    browser.close.assert_awaited_once()


# --------------------------------------------------------------------------- #
# Offline helpers: prompt_hash / replay files / record files / html-file read
# (task 04.0 subtask 07; AIS-9 / AIS-11 / AIS-14)
# --------------------------------------------------------------------------- #
def test_prompt_hash_is_deterministic_and_joins_blocks() -> None:
    """
    [Local] schema detection: verifies prompt hash is deterministic and joins blocks.

    Scenario:
        prompt_hash is a stable sha256 of instruction + blank line + snapshot.

    Boundaries:
        - pure hashing; the same inputs always yield the same 64-char hex key.

    On failure, first check:
        - a drifting key would strand every committed replay recording.
    """
    key = sd.prompt_hash("INSTRUCTION", "SNAPSHOT")
    assert key == sd.prompt_hash("INSTRUCTION", "SNAPSHOT")
    assert len(key) == 64 and all(c in "0123456789abcdef" for c in key)
    assert sd.prompt_hash("INSTRUCTION", "OTHER") != key


def test_format_replay_usage_line_reports_zero_tokens_and_replayed_count() -> None:
    """
    [Local] schema detection: verifies format replay usage line reports zero tokens and replayed count.

    Scenario:
        the replay usage line reports zero tokens and the replayed count, no key.

    Boundaries:
        - pure formatting.

    On failure, first check:
        - a replay run would imply live token spend it never incurred.
    """
    line = sd.format_replay_usage_line("claude-sonnet-5", 3)
    assert "requests=0" in line
    assert "replayed=3" in line
    assert "input_tokens=0" in line
    assert "offline replay" in line
    assert "$" not in line


def test_load_replay_file_ignores_underscore_keys(tmp_path) -> None:
    """
    [Local] schema detection: verifies load replay file ignores underscore keys.

    Scenario:
        load_replay_file drops _-prefixed keys (e.g. a _provenance note).

    Boundaries:
        - real file I/O via tmp_path.

    On failure, first check:
        - a provenance note would be treated as a prompt hash and never match.
    """
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"_provenance": "hand-authored", "abc123": "{}"}), encoding="utf-8")
    loaded = sd.load_replay_file(str(path))
    assert loaded == {"abc123": "{}"}


def test_load_replay_file_missing_path_fails_loudly(tmp_path) -> None:
    """
    [Local] schema detection: verifies that a nonexistent replay file path raises SchemaDetectionError("Cannot read --replay file").

    Scenario:
        Given a path to a file that does not exist
        When sd.load_replay_file(path) is called
        Then SchemaDetectionError matching "Cannot read --replay file" is raised.

    Boundaries:
        - The missing-file path for the replay loader

    On failure, first check:
        - The file-open error handling in load_replay_file
    """
    with pytest.raises(sd.SchemaDetectionError, match="Cannot read --replay file"):
        sd.load_replay_file(str(tmp_path / "nope.json"))


def test_load_replay_file_invalid_json_fails_loudly(tmp_path) -> None:
    """
    [Local] schema detection: verifies that a replay file with invalid JSON raises SchemaDetectionError("not valid JSON").

    Scenario:
        Given a file containing "not json" (not parseable as JSON)
        When sd.load_replay_file(path) is called
        Then SchemaDetectionError matching "not valid JSON" is raised.

    Boundaries:
        - The JSON parse error path in load_replay_file

    On failure, first check:
        - The JSON decode error handling in load_replay_file
    """
    path = tmp_path / "bad.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(sd.SchemaDetectionError, match="not valid JSON"):
        sd.load_replay_file(str(path))


def test_load_replay_file_non_object_fails_loudly(tmp_path) -> None:
    """
    [Local] schema detection: verifies that a replay file whose top-level value is not a JSON object raises SchemaDetectionError("must be a JSON object").

    Scenario:
        Given a file containing "[1, 2, 3]" (a JSON array, not an object)
        When sd.load_replay_file(path) is called
        Then SchemaDetectionError matching "must be a JSON object" is raised.

    Boundaries:
        - The non-object top-level JSON path in load_replay_file

    On failure, first check:
        - The isinstance(data, dict) check in load_replay_file
    """
    path = tmp_path / "arr.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(sd.SchemaDetectionError, match="must be a JSON object"):
        sd.load_replay_file(str(path))


def test_replay_lookup_returns_recorded_response() -> None:
    """
    [Local] schema detection: verifies that replay_lookup returns the recorded response for a matching prompt hash.

    Scenario:
        Given a replay dict keyed by sha256(instructions+snapshot) and the same instructions/snapshot
        When sd.replay_lookup(replay, "I", "S") is called
        Then the stored JSON string is returned.

    Boundaries:
        - The "found" path; pure dict lookup, no file I/O

    On failure, first check:
        - The prompt_hash computation or dict lookup in replay_lookup
    """
    replay = {sd.prompt_hash("I", "S"): '{"BOOK_TITLE": {"selector": "#t"}}'}
    assert sd.replay_lookup(replay, "I", "S") == '{"BOOK_TITLE": {"selector": "#t"}}'


def test_replay_lookup_missing_prompt_fails_loudly() -> None:
    """
    [Local] schema detection: verifies replay lookup missing prompt fails loudly.

    Scenario:
        a prompt absent from the replay map is a hard error, never a live fallthrough.

    Boundaries:
        - pure dict lookup.

    On failure, first check:
        - a drifted offline run would silently reach for the live API (AIS-9 violation).
    """
    with pytest.raises(sd.SchemaDetectionError, match="No recorded response"):
        sd.replay_lookup({}, "I", "S")


def test_write_record_file_writes_hash_keyed_responses_and_provenance(tmp_path) -> None:
    """
    [Local] schema detection: verifies write record file writes hash keyed responses and provenance.

    Scenario:
        write_record_file persists hash->response plus a provenance note, no key.

    Boundaries:
        - real file I/O via tmp_path; the fake key must appear nowhere.

    On failure, first check:
        - a leaked key in a committed recording is a credential disclosure (AIS-8).
    """
    path = tmp_path / "rec.json"
    sd.write_record_file(str(path), {"deadbeef": '{"BOOK_TITLE": {"selector": "#t"}}'})
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["deadbeef"] == '{"BOOK_TITLE": {"selector": "#t"}}'
    assert "_provenance" in data
    assert "sk-ant" not in path.read_text(encoding="utf-8")


def test_write_record_file_oserror_fails_loudly(tmp_path) -> None:
    """
    [Local] schema detection: verifies that an OSError from Path.write_text raises SchemaDetectionError("Cannot write --record file").

    Scenario:
        Given Path.write_text is patched to raise OSError("read-only")
        When sd.write_record_file(path, {}) is called
        Then SchemaDetectionError matching "Cannot write --record file" is raised.

    Boundaries:
        - The write-failure path in write_record_file; no real filesystem dependency

    On failure, first check:
        - The OSError -> SchemaDetectionError conversion in write_record_file
    """
    with patch.object(sd.Path, "write_text", side_effect=OSError("read-only")):
        with pytest.raises(sd.SchemaDetectionError, match="Cannot write --record file"):
            sd.write_record_file(str(tmp_path / "rec.json"), {})


async def test_block_all_requests_aborts() -> None:
    """
    [Local] schema detection: verifies block all requests aborts.

    Scenario:
        the html-file route handler aborts every request (default-deny, AIS-9).

    Boundaries:
        - a fake Route; assert abort() is awaited and continue_() never is.

    On failure, first check:
        - a set_content page could fetch a subresource and leave the offline boundary.
    """
    route = MagicMock()
    route.abort = AsyncMock()
    route.continue_ = AsyncMock()
    await sd._block_all_requests(route)
    route.abort.assert_awaited_once()
    route.continue_.assert_not_called()


def test_read_html_file_reads_utf8(tmp_path) -> None:
    """
    [Local] schema detection: verifies that _read_html_file reads a UTF-8 HTML file and returns its content as a string.

    Scenario:
        Given a UTF-8 file containing "<h1>Tïtle</h1>"
        When sd._read_html_file(path) is called
        Then the string "<h1>Tïtle</h1>" is returned.

    Boundaries:
        - The happy path; real file I/O via tmp_path

    On failure, first check:
        - The UTF-8 read in _read_html_file
    """
    path = tmp_path / "p.html"
    path.write_text("<h1>Tïtle</h1>", encoding="utf-8")
    assert sd._read_html_file(str(path)) == "<h1>Tïtle</h1>"


def test_read_html_file_missing_path_fails_loudly(tmp_path) -> None:
    """
    [Local] schema detection: verifies that a nonexistent HTML file raises SchemaDetectionError("Cannot access --html-file").

    Scenario:
        Given a path to a file that does not exist
        When sd._read_html_file(path) is called
        Then SchemaDetectionError matching "Cannot access --html-file" is raised.

    Boundaries:
        - The missing-file path in _read_html_file

    On failure, first check:
        - The file-access error handling in _read_html_file
    """
    with pytest.raises(sd.SchemaDetectionError, match="Cannot access --html-file"):
        sd._read_html_file(str(tmp_path / "nope.html"))


def test_read_html_file_oversized_fails_loudly(tmp_path) -> None:
    """
    [Local] schema detection: verifies read html file oversized fails loudly.

    Scenario:
        a file over the AIS-11 size cap fails loudly before being read into memory.

    Boundaries:
        - the cap is monkeypatched tiny so a small file trips it (no huge fixture needed).

    On failure, first check:
        - an oversized/binary file read + set_content is a memory/parse DoS.
    """
    path = tmp_path / "big.html"
    path.write_text("x" * 100, encoding="utf-8")
    with patch.object(sd, "HTML_FILE_MAX_BYTES", 10):
        with pytest.raises(sd.SchemaDetectionError, match="over the 10-byte cap"):
            sd._read_html_file(str(path))


def test_read_html_file_non_utf8_fails_loudly(tmp_path) -> None:
    """
    [Local] schema detection: verifies that a non-UTF-8 binary file raises SchemaDetectionError("as UTF-8 text").

    Scenario:
        Given a file containing non-UTF-8 bytes (b"\\xff\\xfe\\x00\\x01not utf8")
        When sd._read_html_file(path) is called
        Then SchemaDetectionError matching "as UTF-8 text" is raised.

    Boundaries:
        - The encoding-error path in _read_html_file

    On failure, first check:
        - The UnicodeDecodeError handling in _read_html_file
    """
    path = tmp_path / "bin.html"
    path.write_bytes(b"\xff\xfe\x00\x01not utf8")
    with pytest.raises(sd.SchemaDetectionError, match="as UTF-8 text"):
        sd._read_html_file(str(path))


# --------------------------------------------------------------------------- #
# detect_schema - source resolution (urls vs html_files)
# --------------------------------------------------------------------------- #
async def test_detect_schema_rejects_both_url_and_html_file(monkeypatch) -> None:
    """
    [Local] schema detection: verifies that passing both --url and --html-file raises SchemaDetectionError("either --url or --html-file").

    Scenario:
        Given ANTHROPIC_API_KEY is set and both urls and html_files are non-empty
        When sd.detect_schema("amazon", urls=["https://x/1"], html_files=["/tmp/a.html"]) is called
        Then SchemaDetectionError matching "either --url or --html-file" is raised.

    Boundaries:
        - The mutually-exclusive source validation pre-flight

    On failure, first check:
        - The mutual-exclusion check for urls and html_files in detect_schema
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with pytest.raises(sd.SchemaDetectionError, match="either --url or --html-file"):
        await sd.detect_schema("amazon", urls=["https://x/1"], html_files=["/tmp/a.html"])


async def test_detect_schema_rejects_no_source(monkeypatch) -> None:
    """
    [Local] schema detection: verifies that calling detect_schema with neither urls nor html_files raises SchemaDetectionError("At least one sample").

    Scenario:
        Given ANTHROPIC_API_KEY is set and neither urls nor html_files are provided
        When sd.detect_schema("amazon") is called with no source arguments
        Then SchemaDetectionError matching "At least one sample" is raised.

    Boundaries:
        - The no-source validation pre-flight

    On failure, first check:
        - The empty-source check in detect_schema
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with pytest.raises(sd.SchemaDetectionError, match="At least one sample"):
        await sd.detect_schema("amazon")


async def test_detect_schema_rejects_too_many_html_files(monkeypatch) -> None:
    """
    [Local] schema detection: verifies that more than MAX_SAMPLE_URLS html_files raises SchemaDetectionError("--html-file paths").

    Scenario:
        Given ANTHROPIC_API_KEY is set and MAX_SAMPLE_URLS+1 html file paths are provided
        When sd.detect_schema("amazon", html_files=too_many) is called
        Then SchemaDetectionError matching "--html-file paths" is raised.

    Boundaries:
        - The html_files cap enforcement; mirrors the urls cap test

    On failure, first check:
        - The html_files count cap in detect_schema
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    too_many = [f"/tmp/{i}.html" for i in range(sd.MAX_SAMPLE_URLS + 1)]
    with pytest.raises(sd.SchemaDetectionError, match="--html-file paths"):
        await sd.detect_schema("amazon", html_files=too_many)


# --------------------------------------------------------------------------- #
# detect_schema - replay mode (AIS-9: no key, no client constructed, no network)
# --------------------------------------------------------------------------- #
async def test_replay_mode_requires_no_api_key(monkeypatch, tmp_path) -> None:
    """
    [Local] schema detection: verifies replay mode requires no api key.

    Scenario:
        a replay run with NO ANTHROPIC_API_KEY succeeds and constructs no client.

    Boundaries:
        - the key is deleted and sd.anthropic.Anthropic is wired to raise if ever constructed; call_anthropic is left real so any live attempt would blow up.

    On failure, first check:
        - a silent fall-through to a live API call (cost + key requirement) - the Boundary-4 / AIS-9 violation this gate exists to prevent.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    content = "<html><h1 id='productTitle'>Book One</h1></html>"
    response = json.dumps({"BOOK_TITLE": {"selector": "#productTitle", "rationale": "stable id"}})
    replay_path = tmp_path / "amazon.replay.json"
    replay_path.write_text(
        json.dumps({"_provenance": "test", **_detail_replay_for(content, response)}), encoding="utf-8"
    )

    page = FakePage(locators={"#productTitle": FakeLocator("Book One")}, content=content)
    browser = FakeBrowser(page_factory=lambda *a, **k: page)

    with (
        patch.object(sd, "async_playwright", build_playwright_factory(browser)),
        patch.object(sd, "anthropic") as mock_anthropic,
    ):
        mock_anthropic.Anthropic.side_effect = AssertionError("no client may be constructed in replay mode")
        report, usage_line, _exit = await sd.detect_schema("amazon", urls=["https://x/1"], replay=str(replay_path))

    assert "#productTitle" in report
    assert "[MATCH]" in report
    assert "replayed=1" in usage_line
    mock_anthropic.Anthropic.assert_not_called()
    browser.close.assert_awaited_once()


async def test_replay_missing_prompt_fails_loudly(monkeypatch, tmp_path) -> None:
    """
    [Local] schema detection: verifies replay missing prompt fails loudly.

    Scenario:
        a replay file that lacks the run's prompt hash fails loudly, no live call.

    Boundaries:
        - the replay file is a valid-but-unrelated recording; no key is set.

    On failure, first check:
        - a stale recording would silently trigger live spend (AIS-9).
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    replay_path = tmp_path / "stale.replay.json"
    replay_path.write_text(json.dumps({"unrelated_hash": "{}"}), encoding="utf-8")

    page = FakePage(locators={"#productTitle": FakeLocator("X")}, content="<h1 id='productTitle'>X</h1>")
    browser = FakeBrowser(page_factory=lambda *a, **k: page)

    with patch.object(sd, "async_playwright", build_playwright_factory(browser)):
        with pytest.raises(sd.SchemaDetectionError, match="No recorded response"):
            await sd.detect_schema("amazon", urls=["https://x/1"], replay=str(replay_path))
    browser.close.assert_awaited_once()


async def test_detect_schema_html_file_offline_replay_end_to_end(monkeypatch, tmp_path) -> None:
    """
    [Local] schema detection: verifies detect schema html file offline replay end to end.

    Scenario:
        html_files + replay drives the pipeline fully offline through load_html_file_page.

    Boundaries:
        - FakePage (with set_content) stands in for Chromium; no key, no client.

    On failure, first check:
        - the from-files branch or the set_content load path would not be exercised offline.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    content = "<html><h1 id='productTitle'>From File</h1></html>"
    html_path = tmp_path / "book.html"
    html_path.write_text(content, encoding="utf-8")
    response = json.dumps({"BOOK_TITLE": {"selector": "#productTitle", "rationale": "id"}})
    replay_path = tmp_path / "amazon.replay.json"
    replay_path.write_text(json.dumps(_detail_replay_for(content, response)), encoding="utf-8")

    page = FakePage(locators={"#productTitle": FakeLocator("From File")}, content=content)
    browser = FakeBrowser(page_factory=lambda *a, **k: page)

    with patch.object(sd, "async_playwright", build_playwright_factory(browser)):
        report, usage_line, _exit = await sd.detect_schema(
            "amazon", html_files=[str(html_path)], replay=str(replay_path)
        )

    assert "#productTitle" in report
    assert str(html_path) in report  # the file path is the sample label
    assert "replayed=1" in usage_line
    page.set_content.assert_awaited_once()
    page.route.assert_awaited()  # the default-deny route was installed


# --------------------------------------------------------------------------- #
# detect_schema - record mode (mocked client; AIS-8 no key in the file)
# --------------------------------------------------------------------------- #
async def test_record_mode_writes_prompt_hash_keyed_responses(monkeypatch, tmp_path) -> None:
    """
    [Local] schema detection: verifies record mode writes prompt hash keyed responses.

    Scenario:
        a live run with --record writes {sha256(prompt): response}, no key in the file.

    Boundaries:
        - call_anthropic is mocked (no real network); the record file is read back.

    On failure, first check:
        - a broken record path would strand maintainers unable to refresh replays, or leak the key into a committed artifact (AIS-8).
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-XXXX")
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    content = "<html><h1 id='productTitle'>Rec</h1></html>"
    response = json.dumps({"BOOK_TITLE": {"selector": "#productTitle", "rationale": "id"}})
    record_path = tmp_path / "rec.json"

    page = FakePage(locators={"#productTitle": FakeLocator("Rec")}, content=content)
    browser = FakeBrowser(page_factory=lambda *a, **k: page)

    with (
        patch.object(sd, "async_playwright", build_playwright_factory(browser)),
        patch.object(sd, "call_anthropic", MagicMock(return_value=(response, _fake_usage()))),
    ):
        await sd.detect_schema("amazon", urls=["https://x/1"], record=str(record_path))

    data = json.loads(record_path.read_text(encoding="utf-8"))
    expected_key = next(iter(_detail_replay_for(content, response)))
    assert data[expected_key] == response
    assert "sk-ant-test-XXXX" not in record_path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# detect_schema - JSON format + check-current-only + drift exit codes (subtask 08)
# --------------------------------------------------------------------------- #
def _tiny_site(selector="#t") -> dict[str, dict[str, str]]:
    """A one-field site_constants entry so a FakePage can make every current selector match/miss."""
    return {"tiny": {"BASE_URL": "https://x", "BOOK_TITLE": selector}}


async def test_check_current_only_makes_no_api_call(monkeypatch, tmp_path) -> None:
    """
    [Local] schema detection: verifies check current only makes no api call.

    Scenario:
        --check-current-only constructs no client, needs no key, makes no AI call (AIS-9).

    Boundaries:
        - the key is deleted; sd.anthropic.Anthropic and call_anthropic both raise if ever reached; an --html-file sample drives the fully offline path.

    On failure, first check:
        - a keyless drift check would silently require the key or spend on a live call.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    content = "<html><h1 id='productTitle'>Book</h1></html>"
    html_path = tmp_path / "b.html"
    html_path.write_text(content, encoding="utf-8")
    page = FakePage(locators={"#productTitle": FakeLocator("Book")}, content=content)
    browser = FakeBrowser(page_factory=lambda *a, **k: page)

    with (
        patch.object(sd, "async_playwright", build_playwright_factory(browser)),
        patch.object(sd, "anthropic") as mock_anthropic,
        patch.object(
            sd, "call_anthropic", MagicMock(side_effect=AssertionError("no AI call in check-current-only"))
        ) as call_ai,
    ):
        mock_anthropic.Anthropic.side_effect = AssertionError("no client in check-current-only mode")
        report, usage_line, exit_code = await sd.detect_schema(
            "amazon", html_files=[str(html_path)], check_current_only=True
        )

    mock_anthropic.Anthropic.assert_not_called()
    call_ai.assert_not_called()
    assert "no AI call" in usage_line
    assert "Schema-detection proposals" in report
    assert exit_code in (0, 4)
    browser.close.assert_awaited_once()


async def test_exit_code_zero_when_all_current_selectors_match(monkeypatch) -> None:
    """
    [Local] schema detection: verifies exit code zero when all current selectors match.

    Scenario:
        a run where every current selector matches a sample returns exit 0.

    Boundaries:
        - a one-field site whose selector the FakePage carries; check-current-only (no AI).

    On failure, first check:
        - a healthy schema would wrongly trip the drift exit code.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(sd, "site_constants", _tiny_site("#t"))
    page = FakePage(locators={"#t": FakeLocator("Title")}, content="<h1 id='t'>Title</h1>")
    browser = FakeBrowser(page_factory=lambda *a, **k: page)

    with patch.object(sd, "async_playwright", build_playwright_factory(browser)):
        _report, usage_line, exit_code = await sd.detect_schema("tiny", urls=["u0"], check_current_only=True)

    assert exit_code == 0
    assert "no AI call" in usage_line
    browser.close.assert_awaited_once()


async def test_exit_code_four_on_broken_current_selector(monkeypatch) -> None:
    """
    [Local] schema detection: verifies exit code four on broken current selector.

    Scenario:
        a current selector matching on no sample yields exit 4 (drift).

    Boundaries:
        - a one-field site whose selector the FakePage does NOT carry; check-current-only.

    On failure, first check:
        - drift would go unnoticed and a scheduled CI job could not gate on it.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(sd, "site_constants", _tiny_site("#gone"))
    page = FakePage(locators={}, content="<html></html>")
    browser = FakeBrowser(page_factory=lambda *a, **k: page)

    with patch.object(sd, "async_playwright", build_playwright_factory(browser)):
        _report, _usage, exit_code = await sd.detect_schema("tiny", urls=["u0"], check_current_only=True)

    assert exit_code == 4
    browser.close.assert_awaited_once()


async def test_detect_schema_json_format_offline_replay(monkeypatch, tmp_path) -> None:
    """
    [Local] schema detection: verifies detect schema json format offline replay.

    Scenario:
        --format json on an offline replay run emits the documented JSON schema + exit code.

    Boundaries:
        - FakePage + hand-built replay; no key, no client.

    On failure, first check:
        - a CI drift artifact would be malformed, or the exit code would be dropped.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    content = "<html><h1 id='productTitle'>Book One</h1></html>"
    response = json.dumps({"BOOK_TITLE": {"selector": "#productTitle", "rationale": "stable id"}})
    replay_path = tmp_path / "amazon.replay.json"
    replay_path.write_text(json.dumps(_detail_replay_for(content, response)), encoding="utf-8")
    page = FakePage(locators={"#productTitle": FakeLocator("Book One")}, content=content)
    browser = FakeBrowser(page_factory=lambda *a, **k: page)

    with patch.object(sd, "async_playwright", build_playwright_factory(browser)):
        report, usage_line, exit_code = await sd.detect_schema(
            "amazon", urls=["https://x/1"], replay=str(replay_path), report_format="json"
        )

    data = json.loads(report)
    assert data["site"] == "amazon"
    assert data["page_type"] == "detail"
    assert data["model"] == sd.DEFAULT_MODEL
    title = next(f for f in data["fields"] if f["name"] == "BOOK_TITLE")
    assert title["proposed"] == "#productTitle"
    assert title["matches"] == {"https://x/1": 1}
    assert "current_broken" in data["summary"]
    assert exit_code in (0, 4)
    assert "replayed=1" in usage_line
    browser.close.assert_awaited_once()
