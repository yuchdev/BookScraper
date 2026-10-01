"""Mock-tier tests for bookscraper.scraping.schema_detection.

Playwright is faked via tests/mock/scraping/playwright_fakes.py and the anthropic client
is always mocked - this suite MUST NEVER make a real network call to Anthropic's API or
spend real credits. Covers: the happy path, a missing ANTHROPIC_API_KEY, malformed AI
JSON, a selector that matches some sample URLs but not others (flagged, not silently
accepted), and a Playwright page-load failure.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from bookscraper.scraping import schema_detection as sd
from tests.mock.scraping.playwright_fakes import (
    FakeBrowser,
    FakeLocator,
    FakePage,
    build_playwright_factory,
)

MODULE = "bookscraper.scraping.schema_detection"


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
    """Scenario: a well-formed reply is returned with its usage object.

    Boundaries: the anthropic client (count_tokens + create) is fully mocked; no real call.
    On failure: the model kwarg is not threaded through, or usage is dropped.
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
    """Scenario: the client is built with a single retry, matching the module's own bound.

    Boundaries: only the client constructor kwargs are asserted; no network.
    On failure: stacked/unbounded retries would multiply spend on a transient error.
    """
    client = _fake_client(_fake_message('{"BOOK_TITLE": {"selector": "#t"}}'))
    with patch.object(sd, "anthropic") as mock_anthropic:
        mock_anthropic.Anthropic.return_value = client
        sd.call_anthropic("instructions", "snapshot", "test-key", "model-x")

    _, kwargs = mock_anthropic.Anthropic.call_args
    assert kwargs["max_retries"] == sd.MAX_API_RETRIES == 1


def test_instruction_block_marked_cacheable_and_snapshot_not() -> None:
    """Scenario: cache_control sits on the static instruction block, never the per-page snapshot.

    Boundaries: asserts only the create() kwargs shape; no network.
    On failure: caching the volatile snapshot would write a fresh entry every request.
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
    """Scenario: an SDK error surfaces only the exception class + status, never its body/key.

    Boundaries: the client constructor raises; the fake key must appear nowhere in the error.
    On failure: a leaked body/key is a credential/PII disclosure (AIS-8/AIS-16).
    """

    class _FakeStatusError(Exception):
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
    """Scenario: a max_tokens stop reason is treated as truncation and never parsed.

    Boundaries: client mocked; create returns stop_reason=max_tokens.
    On failure: a half-written JSON answer would be parsed as if complete.
    """
    client = _fake_client(_fake_message('{"BOOK_TITLE":', stop_reason="max_tokens"))
    with patch.object(sd, "anthropic") as mock_anthropic:
        mock_anthropic.Anthropic.return_value = client
        with pytest.raises(sd.SchemaDetectionError, match="truncated"):
            sd.call_anthropic("instructions", "snapshot", "test-key")


def test_call_anthropic_raises_on_refusal_naming_category() -> None:
    """Scenario: a refusal stop reason raises and names stop_details.category when present.

    Boundaries: client mocked; create returns stop_reason=refusal + a category.
    On failure: a refusal would be parsed as content, or the category would be dropped.
    """
    details = SimpleNamespace(category="cyber", explanation="nope")
    client = _fake_client(_fake_message("", stop_reason="refusal", stop_details=details))
    with patch.object(sd, "anthropic") as mock_anthropic:
        mock_anthropic.Anthropic.return_value = client
        with pytest.raises(sd.SchemaDetectionError, match="refused.*category: cyber"):
            sd.call_anthropic("instructions", "snapshot", "test-key")


def test_call_anthropic_skips_thinking_blocks() -> None:
    """Scenario: _extract_text concatenates only text blocks, skipping thinking blocks.

    Boundaries: client mocked; content carries a thinking block before the text block.
    On failure: a thinking summary would leak into the JSON parse.
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
    """Scenario: a response with no text blocks fails loudly.

    Boundaries: client mocked; content is empty.
    On failure: an empty reply would be treated as a valid (empty) proposal set.
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
    """Scenario: an over-budget prompt is re-pruned until it fits, then sent once.

    Boundaries: count_tokens returns over-budget first, then under-budget; create mocked.
    On failure: the oversized prompt would be sent, or the snapshot never shrunk.
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
    """Scenario: a prompt still over budget at the 10k-char floor raises without any send.

    Boundaries: count_tokens always returns over-budget; create must never be called.
    On failure: unbounded spend on a prompt that can never fit.
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
    """Scenario: an SDK error during the token-count pre-flight is wrapped without its body.

    Boundaries: count_tokens raises; create must never run.
    On failure: a raw SDK body (possibly echoing the prompt) would leak.
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
    """Scenario: an SDK error from messages.create is wrapped without its body.

    Boundaries: count_tokens is fine; create raises.
    On failure: a raw SDK body would leak into the error surface.
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
    page = FakePage(content="<html><script>x()</script><h1>Title</h1></html>")
    result = await sd.fetch_pruned_html("https://x/book", page)
    assert "Title" in result
    assert "x()" not in result
    page.goto.assert_awaited_once()
    page.route.assert_awaited_once()


async def test_fetch_pruned_html_raises_on_timeout() -> None:
    page = FakePage()
    page.goto = AsyncMock(side_effect=PlaywrightTimeoutError("nav timeout"))
    with pytest.raises(sd.SchemaDetectionError, match="Timed out loading"):
        await sd.fetch_pruned_html("https://x/book", page)


async def test_fetch_pruned_html_raises_on_generic_load_error() -> None:
    page = FakePage()
    page.goto = AsyncMock(side_effect=RuntimeError("connection refused"))
    with pytest.raises(sd.SchemaDetectionError, match="Failed to load"):
        await sd.fetch_pruned_html("https://x/book", page)


# --------------------------------------------------------------------------- #
# validate_across_samples
# --------------------------------------------------------------------------- #
async def test_validate_across_samples_consistent_match() -> None:
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
    page1 = FakePage(locators={".tag": FakeLocator(["a", "b"])})
    page2 = FakePage(locators={})
    samples = [{"url": "u1", "page": page1}, {"url": "u2", "page": page2}]
    proposals = {"TAGS": {"selector": ".tag", "rationale": ""}}

    result = await sd.validate_across_samples(proposals, samples)

    assert result["TAGS"]["consistent"] is False
    matched = [entry["matched"] for entry in result["TAGS"]["per_url"]]
    assert matched == [True, False]


async def test_validate_across_samples_null_selector_is_miss() -> None:
    page = FakePage(locators={})
    samples = [{"url": "u1", "page": page}]
    proposals = {"ISBN10": {"selector": None, "rationale": "absent"}}

    result = await sd.validate_across_samples(proposals, samples)

    entry = result["ISBN10"]["per_url"][0]
    assert entry["matched"] is False
    assert entry["snippet"] == "null selector"


async def test_validate_across_samples_bad_selector_does_not_abort() -> None:
    page = FakePage(locators={"::bad::": FakeLocator([], raises=ValueError("bad selector"))})
    samples = [{"url": "u1", "page": page}]
    proposals = {"TAGS": {"selector": "::bad::", "rationale": ""}}

    result = await sd.validate_across_samples(proposals, samples)

    entry = result["TAGS"]["per_url"][0]
    assert entry["matched"] is False
    assert entry["snippet"] == "query error"


# --------------------------------------------------------------------------- #
# detect_schema (full pipeline)
# --------------------------------------------------------------------------- #
def _amazon_pages():
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
        report, usage_line = await sd.detect_schema("amazon", ["https://x/1", "https://x/2"])

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
    """Scenario: a 3-URL run makes 3 requests and sums each response's usage into one line.

    Boundaries: call_anthropic is mocked per URL; cache-read is non-zero on requests 2-3.
    On failure: the usage line would reflect one request, or drop cached-token totals.
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
        _, usage_line = await sd.detect_schema("amazon", ["https://x/1", "https://x/2", "https://x/3"])

    assert call_ai.call_count == 3
    assert "requests=3" in usage_line
    assert "input_tokens=1200" in usage_line
    assert "cache_read_input_tokens=1800" in usage_line
    assert "cache_creation_input_tokens=500" in usage_line
    assert "output_tokens=30" in usage_line
    assert "model=claude-sonnet-5" in usage_line


async def test_detect_schema_rejects_too_many_urls(monkeypatch) -> None:
    """Scenario: more than MAX_SAMPLE_URLS sample URLs is rejected before any work.

    Boundaries: no API key set, no browser/anthropic patched - the cap must trip first.
    On failure: an unbounded sample set runs up unbounded Anthropic spend.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    too_many = [f"https://x/{i}" for i in range(sd.MAX_SAMPLE_URLS + 1)]
    with pytest.raises(sd.SchemaDetectionError, match="per-run cap is 5"):
        await sd.detect_schema("amazon", too_many)


async def test_detect_schema_no_detail_fields_fails_loudly(monkeypatch) -> None:
    """Scenario: a site whose entry defines no detail-page selectors fails loudly.

    Boundaries: site_constants is monkeypatched to an endpoint-only entry; no browser launches.
    On failure: a run with nothing to propose would proceed to a pointless API call.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(sd, "site_constants", {"emptysite": {"BASE_URL": "https://x"}})
    with pytest.raises(sd.SchemaDetectionError, match="No detail-page selector fields"):
        await sd.detect_schema("emptysite", ["https://x/1"])


async def test_detect_schema_missing_api_key(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(sd.SchemaDetectionError, match="ANTHROPIC_API_KEY is not set"):
        await sd.detect_schema("amazon", ["https://x/1"])


async def test_detect_schema_malformed_ai_json_fails_loudly(monkeypatch) -> None:
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
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    bad_page = FakePage()
    bad_page.goto = AsyncMock(side_effect=RuntimeError("connection refused"))
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
    with pytest.raises(sd.SchemaDetectionError, match="Unknown site"):
        await sd.detect_schema("bogus", ["https://x/1"])


async def test_detect_schema_no_urls() -> None:
    with pytest.raises(sd.SchemaDetectionError, match="At least one sample"):
        await sd.detect_schema("amazon", [])
