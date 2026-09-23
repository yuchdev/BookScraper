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


def _fake_message(text: str) -> SimpleNamespace:
    """Build a stand-in for an anthropic Message: `.content[i].text`."""
    return SimpleNamespace(content=[SimpleNamespace(text=text)])


# --------------------------------------------------------------------------- #
# call_anthropic (anthropic SDK mocked)
# --------------------------------------------------------------------------- #
def test_call_anthropic_returns_reply_text() -> None:
    client = MagicMock()
    client.messages.create.return_value = _fake_message('{"BOOK_TITLE": {"selector": "#t"}}')

    with patch.object(sd, "anthropic") as mock_anthropic:
        mock_anthropic.Anthropic.return_value = client
        result = sd.call_anthropic("prompt", "test-key", "model-x")

    assert result == '{"BOOK_TITLE": {"selector": "#t"}}'
    mock_anthropic.Anthropic.assert_called_once_with(api_key="test-key")
    _, kwargs = client.messages.create.call_args
    assert kwargs["model"] == "model-x"


def test_call_anthropic_wraps_sdk_error() -> None:
    with patch.object(sd, "anthropic") as mock_anthropic:
        mock_anthropic.Anthropic.side_effect = RuntimeError("network down")
        with pytest.raises(sd.SchemaDetectionError, match="Anthropic API request failed"):
            sd.call_anthropic("prompt", "test-key")


def test_call_anthropic_raises_on_empty_content() -> None:
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(content=[])
    with patch.object(sd, "anthropic") as mock_anthropic:
        mock_anthropic.Anthropic.return_value = client
        with pytest.raises(sd.SchemaDetectionError, match="no text content"):
            sd.call_anthropic("prompt", "test-key")


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
        patch.object(sd, "call_anthropic", MagicMock(return_value=proposals_json)) as call_ai,
    ):
        report = await sd.detect_schema("amazon", ["https://x/1", "https://x/2"])

    assert "#productTitle" in report
    assert "[MATCH]" in report
    # TAGS matched page1 but not page2 -> flagged, not silently accepted.
    assert "INCONSISTENT" in report
    assert "proposal only" in report.lower()
    call_ai.assert_called_once()
    browser.close.assert_awaited_once()


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
        patch.object(sd, "call_anthropic", MagicMock(return_value="not json at all")),
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
