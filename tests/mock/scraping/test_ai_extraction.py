"""Mock-tier tests for bookscraper.scraping.ai_extraction's live-client behaviour (task 04.1/03).

The anthropic client is always a ``MagicMock`` - this suite MUST NEVER make a real network call or
spend real credits. It covers the pieces the pure unit tests cannot: that exactly one request is made
per page for all missing fields (EXT-4), that the ``ANTHROPIC_API_KEY`` and page HTML never reach a
log line (EXT-6 caplog sweep), that an SDK error or a ``max_tokens`` stop degrades to ``{}`` instead of
aborting the run (EXT-7), that replay mode never touches the client, and that usage/record bookkeeping
is wired through the shared ``ai_common`` accumulator.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from bookscraper.scraping import ai_common, ai_extraction
from bookscraper.scraping.ai_common import UsageAccumulator
from bookscraper.scraping.ai_extraction import extract_missing_fields

SITE = "amazon"


def _fake_usage(input_tokens: int = 42, output_tokens: int = 7) -> SimpleNamespace:
    """Build a stand-in for an anthropic ``Message.usage`` object."""
    return SimpleNamespace(
        input_tokens=input_tokens,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
        output_tokens=output_tokens,
    )


def _fake_message(text: str, stop_reason: str = "end_turn", usage: object = None) -> SimpleNamespace:
    """Build a stand-in for an anthropic ``Message`` with one text block and a usage object."""
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        stop_details=None,
        usage=usage if usage is not None else _fake_usage(),
    )


def _fake_client(message: object, input_tokens: int = 100) -> MagicMock:
    """A mocked anthropic client whose ``count_tokens`` and ``create`` are pre-wired."""
    client = MagicMock()
    client.messages.count_tokens.return_value = SimpleNamespace(input_tokens=input_tokens)
    client.messages.create.return_value = message
    return client


def _response_json(**fields: dict) -> str:
    """Serialize a ``{"fields": {...}}`` reply body as the raw model text."""
    return json.dumps({"fields": fields})


# --------------------------------------------------------------------------- #
# EXT-4: one request per page for all missing eligible fields
# --------------------------------------------------------------------------- #
async def test_one_request_for_multiple_missing_fields():
    """
    [Local] ai_extraction: several missing fields are recovered with a single API request (EXT-4).

    Scenario:
        Given ISBN13 and DESCRIPTION both missing and both present in the page text
        And a mocked client returning both in one reply
        When extract_missing_fields runs live
        Then client.messages.create is called exactly once and both fields are kept

    Boundaries:
        - count_tokens is under budget, so no re-prune loop interferes with the single create call

    On failure, first check:
        - The engine started issuing one request per field instead of batching them
    """
    description = "A practical guide to concurrent Python."
    html = f"<html><body><p>ISBN-13: 978-1098131029</p><div>{description}</div></body></html>"
    message = _fake_message(
        _response_json(
            ISBN13={"value": "9781098131029", "evidence": "ISBN-13: 978-1098131029"},
            DESCRIPTION={"value": description, "evidence": description},
        )
    )
    client = _fake_client(message)
    result = await extract_missing_fields(
        html,
        SITE,
        ["ISBN13", "DESCRIPTION"],
        client=client,
        replay=None,
        record=None,
        usage=UsageAccumulator(),
    )
    assert client.messages.create.call_count == 1
    assert set(result) == {"ISBN13", "DESCRIPTION"}


# --------------------------------------------------------------------------- #
# EXT-6: the key and page HTML never reach a log line
# --------------------------------------------------------------------------- #
async def test_api_key_and_html_never_logged(monkeypatch, caplog):
    """
    [Local] ai_extraction: neither the API key nor page HTML appears in any log record (EXT-6 sweep).

    Scenario:
        Given ANTHROPIC_API_KEY set to a sentinel and page HTML carrying a sentinel marker
        And a reply that keeps one field and drops another (exercising the debug drop log)
        When extract_missing_fields runs live with logging captured at DEBUG
        Then no captured log message contains either sentinel

    Boundaries:
        - Captures the "bookscraper.scraping.ai_extraction" logger at DEBUG so the per-field drop line is included

    On failure, first check:
        - A warning/debug line started echoing page content, a raw prompt, or the key
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-SECRET-KEY-should-never-log")
    html_marker = "HTMLSENTINEL-should-never-log"
    html = f"<html><body><p>ISBN-13: 978-1098131029</p><!-- {html_marker} --><p>About</p></body></html>"
    message = _fake_message(
        _response_json(
            ISBN13={"value": "9781098131029", "evidence": "ISBN-13: 978-1098131029"},
            DESCRIPTION={"value": "not on the page at all", "evidence": "About"},
        )
    )
    client = _fake_client(message)
    with caplog.at_level("DEBUG", logger="bookscraper.scraping.ai_extraction"):
        result = await extract_missing_fields(
            html,
            SITE,
            ["ISBN13", "DESCRIPTION"],
            client=client,
            replay=None,
            record=None,
            usage=UsageAccumulator(),
        )
    assert set(result) == {"ISBN13"}
    blob = "\n".join(record.getMessage() for record in caplog.records)
    assert "sk-SECRET-KEY-should-never-log" not in blob
    assert html_marker not in blob


# --------------------------------------------------------------------------- #
# Replay mode never constructs or touches a client
# --------------------------------------------------------------------------- #
async def test_replay_mode_constructs_no_client():
    """
    [Local] ai_extraction: in replay mode the injected client is never called (offline guarantee).

    Scenario:
        Given a valid replay map and a MagicMock client passed alongside it
        When extract_missing_fields runs in replay mode
        Then neither count_tokens nor create is ever called and the field is still recovered

    Boundaries:
        - Passing a live-looking client proves replay takes precedence and makes no API call

    On failure, first check:
        - Replay mode fell through to the live client path
    """
    html = "<html><body><p>ISBN-13: 978-1098131029</p></body></html>"
    fields = ["ISBN13"]
    instruction_block = ai_extraction.build_extraction_instruction_block(SITE, fields)
    snapshot_block = ai_common.build_snapshot_block(ai_common.prune_html(html))
    replay = {
        ai_common.prompt_hash(instruction_block, snapshot_block): _response_json(
            ISBN13={"value": "9781098131029", "evidence": "ISBN-13: 978-1098131029"}
        )
    }
    client = _fake_client(_fake_message("{}"))
    usage = UsageAccumulator()
    result = await extract_missing_fields(
        html,
        SITE,
        fields,
        client=client,
        replay=replay,
        record=None,
        usage=usage,
    )
    assert set(result) == {"ISBN13"}
    client.messages.create.assert_not_called()
    client.messages.count_tokens.assert_not_called()
    assert usage.replayed == 1


# --------------------------------------------------------------------------- #
# EXT-7: SDK error / max_tokens stop degrade to {} softly
# --------------------------------------------------------------------------- #
async def test_sdk_error_returns_empty_and_logs_no_body(caplog):
    """
    [Local] ai_extraction: an SDK error on create returns {} and logs only a body-free summary (EXT-7/EXT-6).

    Scenario:
        Given a client whose create raises an HTTP-style error carrying a secret response body
        When extract_missing_fields runs live
        Then it returns {} without raising, and no log record contains the secret body

    Boundaries:
        - count_tokens is under budget, so the failure is isolated to the create call

    On failure, first check:
        - The SDK error propagated out of a scrape, or its body leaked into a log line
    """

    class _FakeStatusError(Exception):
        """An SDK-style error carrying an HTTP status and a secret response body in its text."""

        status_code = 429

        def __str__(self) -> str:
            """Return a sentinel body that must never appear in a log line."""
            return "SECRET-RESPONSE-BODY-should-never-log"

    html = "<html><body><p>ISBN-13: 978-1098131029</p></body></html>"
    client = _fake_client(_fake_message("{}"))
    client.messages.create.side_effect = _FakeStatusError()
    with caplog.at_level("WARNING", logger="bookscraper.scraping.ai_extraction"):
        result = await extract_missing_fields(
            html,
            SITE,
            ["ISBN13"],
            client=client,
            replay=None,
            record=None,
            usage=UsageAccumulator(),
        )
    assert result == {}
    blob = "\n".join(record.getMessage() for record in caplog.records)
    assert "SECRET-RESPONSE-BODY-should-never-log" not in blob
    assert "_FakeStatusError" in blob and "429" in blob


async def test_max_tokens_stop_returns_empty():
    """
    [Local] ai_extraction: a max_tokens stop reason returns {} rather than parsing a truncated reply (EXT-7).

    Scenario:
        Given a reply whose stop_reason is "max_tokens"
        When extract_missing_fields runs live
        Then it returns {} without raising

    Boundaries:
        - The text content would parse, but the truncation check short-circuits before parsing

    On failure, first check:
        - check_stop_reason is no longer consulted, or its error is no longer caught softly
    """
    html = "<html><body><p>ISBN-13: 978-1098131029</p></body></html>"
    message = _fake_message(
        _response_json(ISBN13={"value": "9781098131029", "evidence": "ISBN-13: 978-1098131029"}),
        stop_reason="max_tokens",
    )
    result = await extract_missing_fields(
        html,
        SITE,
        ["ISBN13"],
        client=_fake_client(message),
        replay=None,
        record=None,
        usage=UsageAccumulator(),
    )
    assert result == {}


# --------------------------------------------------------------------------- #
# Usage + record bookkeeping
# --------------------------------------------------------------------------- #
async def test_usage_is_recorded_from_live_call():
    """
    [Local] ai_extraction: a live call records its response usage into the shared accumulator.

    Scenario:
        Given a successful live call whose response reports token usage
        When extract_missing_fields runs
        Then the accumulator counts one request and its usage line carries the input-token total

    Boundaries:
        - usage_line carries no prices and no key (shared ai_common contract)

    On failure, first check:
        - usage.record stopped being called with the response usage object
    """
    html = "<html><body><p>ISBN-13: 978-1098131029</p></body></html>"
    message = _fake_message(
        _response_json(ISBN13={"value": "9781098131029", "evidence": "ISBN-13: 978-1098131029"}),
        usage=_fake_usage(input_tokens=1234, output_tokens=56),
    )
    usage = UsageAccumulator()
    await extract_missing_fields(
        html,
        SITE,
        ["ISBN13"],
        client=_fake_client(message),
        replay=None,
        record=None,
        usage=usage,
    )
    assert usage.request_count == 1
    line = usage.usage_line("claude-sonnet-5")
    assert "input_tokens=1234" in line
    assert "sk-" not in line


async def test_record_mode_captures_raw_response():
    """
    [Local] ai_extraction: record mode stores the raw live response keyed by its prompt hash.

    Scenario:
        Given a record dict and a successful live call
        When extract_missing_fields runs
        Then the record holds one entry whose key is the prompt hash and whose value is the raw reply

    Boundaries:
        - The recorded key matches build_* + prompt_hash exactly (no drift from the replay path)

    On failure, first check:
        - record capture was dropped, or keyed on the fitted rather than the canonical snapshot
    """
    html = "<html><body><p>ISBN-13: 978-1098131029</p></body></html>"
    raw = _response_json(ISBN13={"value": "9781098131029", "evidence": "ISBN-13: 978-1098131029"})
    record: dict[str, str] = {}
    await extract_missing_fields(
        html,
        SITE,
        ["ISBN13"],
        client=_fake_client(_fake_message(raw)),
        replay=None,
        record=record,
        usage=UsageAccumulator(),
    )
    instruction_block = ai_extraction.build_extraction_instruction_block(SITE, ["ISBN13"])
    snapshot_block = ai_common.build_snapshot_block(ai_common.prune_html(html))
    expected_key = ai_common.prompt_hash(instruction_block, snapshot_block)
    assert record == {expected_key: raw}
