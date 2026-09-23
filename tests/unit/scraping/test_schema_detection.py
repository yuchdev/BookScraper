"""Unit tests for bookscraper.scraping.schema_detection's pure logic.

No mocks, no network, no browser: only the functions that transform strings/dicts -
HTML pruning, code-fence stripping, AI-response shape validation, detail-field
selection, and diff-report formatting. The Playwright/anthropic-driven pieces are
covered in tests/mock/scraping/test_schema_detection.py.
"""

import pytest

from bookscraper.scraping import parameters
from bookscraper.scraping.schema_detection import (
    SchemaDetectionError,
    _strip_code_fences,
    build_prompt,
    detail_selector_fields,
    format_diff_report,
    prune_html,
    validate_ai_response,
)


# --------------------------------------------------------------------------- #
# detail_selector_fields
# --------------------------------------------------------------------------- #
def test_detail_selector_fields_excludes_search_url_api_and_sentinels() -> None:
    entry = {
        "BASE_URL": "https://x",
        "SEARCH_BASE_URL": "https://x/s",
        "SEARCH_BASE_API": "https://x/api",
        "SINGLE_BOOK_API": "https://x/b",
        "SEARCH_TITLE": ".s",
        "404_PAGE_TITLE": "page not found",
        "BOOK_TITLE": "#t",
        "AUTHORS": ".a",
        "AUTHORS_META": "meta[name=author]",
        "ISBN10": None,
    }
    fields = detail_selector_fields(entry)
    assert set(fields) == {"BOOK_TITLE", "AUTHORS", "AUTHORS_META", "ISBN10"}


def test_detail_selector_fields_on_real_leanpub_entry() -> None:
    fields = detail_selector_fields(parameters.site_constants["leanpub"])
    assert "BOOK_TITLE" in fields
    assert "DESCRIPTION" in fields
    # None-valued fields are kept (we may want to discover a selector for them).
    assert "ISBN10" in fields
    # Search selectors and endpoint constants are excluded.
    assert not any(f.startswith("SEARCH_") for f in fields)
    assert "SINGLE_BOOK_API" not in fields
    assert "BASE_URL" not in fields


# --------------------------------------------------------------------------- #
# prune_html
# --------------------------------------------------------------------------- #
def test_prune_html_removes_script_style_svg_and_comments() -> None:
    html = (
        "<html><head><style>.x{color:red}</style>"
        "<script>var a = 1;</script></head>"
        "<body><!-- a comment --><h1 id='t'>Title</h1>"
        "<svg><path d='M0 0'/></svg><p>Body</p></body></html>"
    )
    pruned = prune_html(html)
    assert "color:red" not in pruned
    assert "var a" not in pruned
    assert "a comment" not in pruned
    assert "M0 0" not in pruned
    assert "Title" in pruned
    assert "Body" in pruned


def test_prune_html_collapses_whitespace() -> None:
    pruned = prune_html("<p>a</p>\n\n   \t<p>b</p>")
    assert "  " not in pruned
    assert "\n" not in pruned


def test_prune_html_truncates_to_budget() -> None:
    html = "<p>" + ("x" * 1000) + "</p>"
    pruned = prune_html(html, max_chars=50)
    assert len(pruned) == 50


def test_prune_html_empty_input() -> None:
    assert prune_html("") == ""


# --------------------------------------------------------------------------- #
# _strip_code_fences
# --------------------------------------------------------------------------- #
def test_strip_code_fences_json_fence() -> None:
    assert _strip_code_fences('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_strip_code_fences_bare_fence() -> None:
    assert _strip_code_fences('```\n{"a": 1}\n```') == '{"a": 1}'


def test_strip_code_fences_no_fence_passthrough() -> None:
    assert _strip_code_fences('  {"a": 1}  ') == '{"a": 1}'


# --------------------------------------------------------------------------- #
# validate_ai_response
# --------------------------------------------------------------------------- #
FIELDS = ["BOOK_TITLE", "AUTHORS", "ISBN10"]


def test_validate_ai_response_happy_path() -> None:
    raw = (
        '{"BOOK_TITLE": {"selector": "#t", "rationale": "stable id"}, '
        '"AUTHORS": {"selector": ".a", "rationale": "author link"}}'
    )
    proposals = validate_ai_response(raw, FIELDS)
    assert proposals["BOOK_TITLE"] == {"selector": "#t", "rationale": "stable id"}
    assert proposals["AUTHORS"]["selector"] == ".a"


def test_validate_ai_response_accepts_null_selector() -> None:
    raw = '{"ISBN10": {"selector": null, "rationale": "not present on this page"}}'
    proposals = validate_ai_response(raw, FIELDS)
    assert proposals["ISBN10"]["selector"] is None


def test_validate_ai_response_strips_fence() -> None:
    raw = '```json\n{"BOOK_TITLE": {"selector": "#t"}}\n```'
    proposals = validate_ai_response(raw, FIELDS)
    assert proposals["BOOK_TITLE"]["selector"] == "#t"
    assert proposals["BOOK_TITLE"]["rationale"] == ""


def test_validate_ai_response_ignores_unknown_keys() -> None:
    raw = '{"BOOK_TITLE": {"selector": "#t"}, "BOGUS": {"selector": ".x"}}'
    proposals = validate_ai_response(raw, FIELDS)
    assert "BOGUS" not in proposals
    assert "BOOK_TITLE" in proposals


def test_validate_ai_response_rejects_non_json() -> None:
    with pytest.raises(SchemaDetectionError, match="not valid JSON"):
        validate_ai_response("this is not json", FIELDS)


def test_validate_ai_response_rejects_non_object_top_level() -> None:
    with pytest.raises(SchemaDetectionError, match="must be an object"):
        validate_ai_response('["BOOK_TITLE", "#t"]', FIELDS)


def test_validate_ai_response_rejects_proposal_without_selector_key() -> None:
    with pytest.raises(SchemaDetectionError, match="must be an object with a 'selector'"):
        validate_ai_response('{"BOOK_TITLE": {"rationale": "oops"}}', FIELDS)


def test_validate_ai_response_rejects_non_string_selector() -> None:
    with pytest.raises(SchemaDetectionError, match="non-string, non-null"):
        validate_ai_response('{"BOOK_TITLE": {"selector": 42}}', FIELDS)


def test_validate_ai_response_rejects_empty_proposal_set() -> None:
    with pytest.raises(SchemaDetectionError, match="no usable proposals"):
        validate_ai_response('{"UNKNOWN_KEY": {"selector": "#t"}}', FIELDS)


# --------------------------------------------------------------------------- #
# build_prompt
# --------------------------------------------------------------------------- #
def test_build_prompt_includes_site_fields_and_html() -> None:
    prompt = build_prompt("amazon", ["BOOK_TITLE", "AUTHORS"], "<h1>Snapshot</h1>")
    assert "amazon" in prompt
    assert "BOOK_TITLE" in prompt
    assert "AUTHORS" in prompt
    assert "<h1>Snapshot</h1>" in prompt
    # Instructs a JSON-only response keyed by the given fields.
    assert "JSON" in prompt


# --------------------------------------------------------------------------- #
# format_diff_report
# --------------------------------------------------------------------------- #
def _validation(consistent: bool, matched_flags: list[bool]) -> dict:
    per_url = [
        {"url": f"https://x/{i}", "matched": m, "count": 1 if m else 0, "snippet": "T" if m else ""}
        for i, m in enumerate(matched_flags)
    ]
    return {"per_url": per_url, "consistent": consistent}


def test_format_diff_report_marks_changed_field() -> None:
    report = format_diff_report(
        site="amazon",
        urls=["https://x/0"],
        proposals={"BOOK_TITLE": {"selector": "#newTitle", "rationale": "stable id"}},
        current={"BOOK_TITLE": "#productTitle"},
        validation={"BOOK_TITLE": _validation(True, [True])},
    )
    assert "- current : '#productTitle'" in report
    assert "+ proposed: '#newTitle'" in report
    assert "rationale: stable id" in report
    assert "[MATCH]" in report
    assert "proposal only" in report.lower()


def test_format_diff_report_marks_unchanged_field() -> None:
    report = format_diff_report(
        site="amazon",
        urls=["https://x/0"],
        proposals={"BOOK_TITLE": {"selector": "#productTitle", "rationale": ""}},
        current={"BOOK_TITLE": "#productTitle"},
        validation={"BOOK_TITLE": _validation(True, [True])},
    )
    assert "(unchanged)" in report


def test_format_diff_report_flags_inconsistent_field() -> None:
    report = format_diff_report(
        site="amazon",
        urls=["https://x/0", "https://x/1"],
        proposals={"TAGS": {"selector": ".tag", "rationale": "class"}},
        current={"TAGS": ".old-tag"},
        validation={"TAGS": _validation(False, [True, False])},
    )
    assert "INCONSISTENT" in report
    assert "WARNING:" in report
    assert "TAGS" in report


def test_format_diff_report_all_consistent_summary() -> None:
    report = format_diff_report(
        site="amazon",
        urls=["https://x/0"],
        proposals={"BOOK_TITLE": {"selector": "#t", "rationale": ""}},
        current={"BOOK_TITLE": "#t"},
        validation={"BOOK_TITLE": _validation(True, [True])},
    )
    assert "validated consistently" in report


def test_format_diff_report_handles_field_not_currently_present() -> None:
    report = format_diff_report(
        site="amazon",
        urls=["https://x/0"],
        proposals={"NEW_FIELD": {"selector": ".n", "rationale": "new"}},
        current={},
        validation={"NEW_FIELD": _validation(True, [True])},
    )
    assert "field not currently present" in report
