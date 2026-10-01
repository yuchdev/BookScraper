"""Unit tests for bookscraper.scraping.schema_detection's pure logic.

No mocks, no network, no browser: only the functions that transform strings/dicts -
HTML pruning, code-fence stripping, AI-response shape validation, detail-field
selection, and diff-report formatting. The Playwright/anthropic-driven pieces are
covered in tests/mock/scraping/test_schema_detection.py.
"""

from types import SimpleNamespace

import pytest

from bookscraper.scraping import parameters
from bookscraper.scraping.schema_detection import (
    DEFAULT_MODEL,
    SchemaDetectionError,
    _merge_proposals,
    _strip_code_fences,
    build_instruction_block,
    build_prompt,
    detail_selector_fields,
    format_diff_report,
    format_usage_line,
    prune_html,
    resolve_model,
    search_selector_fields,
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
# search_selector_fields
# --------------------------------------------------------------------------- #
def test_search_selector_fields_selects_card_and_card_relative_keys() -> None:
    """Scenario: search_selector_fields keeps the card + card-relative + page-level SEARCH_*
    selectors but drops the URL/API/numeric/attribute-name constants.

    Boundaries: pure classification over a dict; no network.
    On failure: a non-selector SEARCH_* value (a base URL or the data-asin attribute name)
    would be fed to the model/validator as if it were a selector.
    """
    fields = search_selector_fields(parameters.site_constants["amazon"])
    # Card container (page-level) and card-relative field selectors are kept.
    assert "SEARCH_BOOK_CARD" in fields
    assert "SEARCH_TITLE" in fields
    assert "SEARCH_AUTHORS" in fields
    assert "SEARCH_BOOK_DETAIL_LINK" in fields
    # Page-level pagination / no-results selectors are kept (they ARE selectors).
    assert "SEARCH_NEXT_PAGE_SELECTOR" in fields
    assert "SEARCH_NO_RESULTS_TEXT" in fields
    # Non-selector constants are excluded.
    assert "SEARCH_BASE_URL" not in fields
    assert "SEARCH_ASIN_ATTRIBUTE" not in fields
    assert "SEARCH_MAXIMUM_PAGES" not in fields
    # Detail-page selectors never leak into the search set.
    assert "BOOK_TITLE" not in fields


def test_search_selector_fields_result_item_sites() -> None:
    """Scenario: the SEARCH_RESULT_ITEM_SELECTOR sites (packtpub/oreilly) keep their card key.

    Boundaries: pure classification over the real packtpub entry.
    On failure: a site using SEARCH_RESULT_ITEM_SELECTOR would have no card key to enumerate.
    """
    fields = search_selector_fields(parameters.site_constants["packtpub"])
    assert "SEARCH_RESULT_ITEM_SELECTOR" in fields
    assert "SEARCH_RATING_SELECTOR" in fields
    assert "SEARCH_BASE_URL" not in fields


# --------------------------------------------------------------------------- #
# build_instruction_block - search variant
# --------------------------------------------------------------------------- #
def test_search_prompt_explains_card_relative_semantics() -> None:
    """Scenario: the search prompt variant explains card-relative vs page-level semantics.

    Boundaries: pure string assembly; no network.
    On failure: the model would propose page-level selectors for per-card fields.
    """
    block = build_instruction_block("amazon", ["SEARCH_BOOK_CARD", "SEARCH_TITLE"], page_type="search")
    assert "SEARCH-RESULTS" in block
    assert "SEARCH_BOOK_CARD" in block
    assert "SEARCH_RESULT_ITEM_SELECTOR" in block
    assert "PAGE-LEVEL" in block
    assert "INSIDE a single card" in block
    # The detail variant carries none of the card-relative semantics.
    detail = build_instruction_block("amazon", ["BOOK_TITLE"])
    assert "INSIDE a single card" not in detail
    assert "SEARCH-RESULTS" not in detail


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


def test_format_diff_report_renders_card_fraction_and_flags_partial() -> None:
    """Scenario: a search report renders per-card fractions and flags a sub-80% field partial.

    Boundaries: pure formatting over a search-shaped validation dict (fraction/card_count).
    On failure: a card-relative selector matching only some cards would look fully valid.
    """
    validation = {
        "SEARCH_TITLE": {
            "per_url": [
                {"url": "u0", "matched": True, "card_count": 8, "match_count": 8, "fraction": 1.0, "snippet": "T"}
            ],
            "consistent": True,
            "partial": False,
        },
        "SEARCH_AUTHORS": {
            "per_url": [
                {"url": "u0", "matched": False, "card_count": 8, "match_count": 4, "fraction": 0.5, "snippet": ""}
            ],
            "consistent": True,
            "partial": True,
        },
    }
    report = format_diff_report(
        site="amazon",
        urls=["u0"],
        proposals={
            "SEARCH_TITLE": {"selector": ".t", "rationale": "card title"},
            "SEARCH_AUTHORS": {"selector": ".a", "rationale": "card authors"},
        },
        current={"SEARCH_TITLE": ".t", "SEARCH_AUTHORS": ".a"},
        validation=validation,
    )
    assert "8/8 cards matched, 100%" in report
    assert "4/8 cards matched, 50%" in report
    assert "!! PARTIAL" in report
    assert "WARNING: 1 card-relative selector(s) matched under 80%" in report
    assert "SEARCH_AUTHORS" in report


def test_format_diff_report_handles_field_not_currently_present() -> None:
    report = format_diff_report(
        site="amazon",
        urls=["https://x/0"],
        proposals={"NEW_FIELD": {"selector": ".n", "rationale": "new"}},
        current={},
        validation={"NEW_FIELD": _validation(True, [True])},
    )
    assert "field not currently present" in report


# --------------------------------------------------------------------------- #
# build_instruction_block / build_prompt - caching split
# --------------------------------------------------------------------------- #
def test_build_instruction_block_has_no_per_page_content() -> None:
    """Scenario: the cacheable static block carries the contract but no HTML snapshot.

    Boundaries: pure string assembly; no network.
    On failure: per-page content in the cached prefix would defeat cross-URL caching.
    """
    block = build_instruction_block("amazon", ["BOOK_TITLE", "AUTHORS"])
    assert "amazon" in block
    assert "BOOK_TITLE" in block
    assert "AUTHORS" in block
    assert "JSON" in block
    assert "Pruned HTML snapshot" not in block


def test_build_prompt_combines_instruction_and_snapshot() -> None:
    """Scenario: build_prompt is the instruction block followed by the snapshot block.

    Boundaries: pure string assembly.
    On failure: the combined debug view would drop one of the two halves.
    """
    prompt = build_prompt("amazon", ["BOOK_TITLE"], "<h1>Snapshot</h1>")
    assert build_instruction_block("amazon", ["BOOK_TITLE"]) in prompt
    assert "<h1>Snapshot</h1>" in prompt


# --------------------------------------------------------------------------- #
# resolve_model - precedence flag > env > default, empty rejected
# --------------------------------------------------------------------------- #
def test_model_precedence_flag_env_default(monkeypatch) -> None:
    """Scenario: --model beats ANTHROPIC_MODEL beats the compiled-in default.

    Boundaries: only os.environ is touched (via monkeypatch); no network.
    On failure: a pinned model could be silently ignored.
    """
    monkeypatch.setenv("ANTHROPIC_MODEL", "env-model")
    assert resolve_model("flag-model") == "flag-model"
    assert resolve_model(None) == "env-model"
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    assert resolve_model(None) == DEFAULT_MODEL
    assert DEFAULT_MODEL == "claude-sonnet-5"


def test_empty_model_id_rejected(monkeypatch) -> None:
    """Scenario: an empty/whitespace model ID (flag or env) is rejected.

    Boundaries: only os.environ is touched; no network.
    On failure: an empty model would reach the API as a confusing 400.
    """
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    with pytest.raises(SchemaDetectionError, match="must not be empty"):
        resolve_model("")
    with pytest.raises(SchemaDetectionError, match="must not be empty"):
        resolve_model("   ")
    monkeypatch.setenv("ANTHROPIC_MODEL", "")
    with pytest.raises(SchemaDetectionError, match="must not be empty"):
        resolve_model(None)


# --------------------------------------------------------------------------- #
# format_usage_line - sums across responses, no prices, no key
# --------------------------------------------------------------------------- #
class _Usage:
    """Minimal stand-in for an anthropic usage object."""

    def __init__(self, inp: int, cread: int, ccreate: int, out: int) -> None:
        self.input_tokens = inp
        self.cache_read_input_tokens = cread
        self.cache_creation_input_tokens = ccreate
        self.output_tokens = out


def test_usage_line_sums_tokens_across_requests() -> None:
    """Scenario: the usage line sums every field across all responses with no price.

    Boundaries: pure formatting over plain usage objects.
    On failure: a per-request figure or a stale price would mislead the operator.
    """
    usages = [_Usage(1000, 0, 500, 10), _Usage(100, 900, 0, 12), _Usage(100, 900, 0, 8)]
    line = format_usage_line("claude-sonnet-5", usages, len(usages))
    assert "model=claude-sonnet-5" in line
    assert "requests=3" in line
    assert "input_tokens=1200" in line
    assert "cache_read_input_tokens=1800" in line
    assert "cache_creation_input_tokens=500" in line
    assert "output_tokens=30" in line
    assert "$" not in line


def test_usage_line_tolerates_missing_usage_fields() -> None:
    """Scenario: a usage object missing cache fields is treated as zero, never crashing.

    Boundaries: pure formatting; usage lacks the cache attributes entirely.
    On failure: an older/partial usage shape would raise mid-report.
    """
    line = format_usage_line("m", [SimpleNamespace(input_tokens=5, output_tokens=2)], 1)
    assert "input_tokens=5" in line
    assert "cache_read_input_tokens=0" in line
    assert "output_tokens=2" in line


# --------------------------------------------------------------------------- #
# _merge_proposals - first non-null wins; null fallback; absent field dropped
# --------------------------------------------------------------------------- #
def test_merge_proposals_first_non_null_selector_wins() -> None:
    """Scenario: the first page proposing a non-null selector for a field wins.

    Boundaries: pure dict merge; no network.
    On failure: a later page's weaker selector could override a good earlier one.
    """
    per_url = [
        {"BOOK_TITLE": {"selector": None, "rationale": "absent here"}},
        {"BOOK_TITLE": {"selector": "#t", "rationale": "found here"}},
    ]
    merged = _merge_proposals(per_url, ["BOOK_TITLE"])
    assert merged["BOOK_TITLE"]["selector"] == "#t"


def test_merge_proposals_falls_back_to_null_when_no_page_matches() -> None:
    """Scenario: a field null on every page keeps a null proposal (the fallback branch).

    Boundaries: pure dict merge.
    On failure: a field no page could locate would silently vanish from the report.
    """
    per_url = [
        {"ISBN10": {"selector": None, "rationale": "not present"}},
        {"ISBN10": {"selector": None, "rationale": "still not present"}},
    ]
    merged = _merge_proposals(per_url, ["ISBN10"])
    assert merged["ISBN10"]["selector"] is None


def test_merge_proposals_drops_field_absent_from_every_page() -> None:
    """Scenario: a field no page returned at all is omitted from the merged set.

    Boundaries: pure dict merge.
    On failure: a phantom field with no proposal could reach the report.
    """
    merged = _merge_proposals([{"BOOK_TITLE": {"selector": "#t", "rationale": ""}}], ["BOOK_TITLE", "TAGS"])
    assert "TAGS" not in merged
    assert merged["BOOK_TITLE"]["selector"] == "#t"
