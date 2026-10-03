"""Unit tests for bookscraper.scraping.schema_detection's pure logic.

No mocks, no network, no browser: only the functions that transform strings/dicts -
HTML pruning, code-fence stripping, AI-response shape validation, detail-field
selection, and diff-report formatting. The Playwright/anthropic-driven pieces are
covered in tests/mock/scraping/test_schema_detection.py.
"""

import json as _json
from types import SimpleNamespace

import pytest

from bookscraper.scraping import parameters
from bookscraper.scraping.schema_detection import (
    DEFAULT_MODEL,
    RATIONALE_MAX_CHARS,
    SchemaDetectionError,
    _build_current_proposals,
    _merge_proposals,
    _primary_is_broken,
    _strip_code_fences,
    build_instruction_block,
    build_prompt,
    compute_field_statuses,
    count_current_broken,
    detail_selector_fields,
    format_check_only_usage_line,
    format_diff_report,
    format_json_report,
    format_usage_line,
    prune_html,
    resolve_model,
    sanitize_untrusted,
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


# --------------------------------------------------------------------------- #
# sanitize_untrusted / _is_current_broken / count_current_broken (subtask 08)
# --------------------------------------------------------------------------- #
def test_sanitize_untrusted_strips_ansi_and_control_chars() -> None:
    """Scenario: ESC/ANSI and C0/C1/DEL control chars are stripped; printable bytes kept (AIS-13).

    Boundaries: pure string transform; no I/O.
    On failure: a hostile page/model string could emit terminal escapes into stdout/a JSON artifact.
    """
    payload = "safe\x1b[31mRED\r\x07\x00end\x7f\x9f"
    cleaned = sanitize_untrusted(payload)
    for bad in ("\x1b", "\r", "\x07", "\x00", "\x7f", "\x9f"):
        assert bad not in cleaned
    # The ESC byte is gone but its printable tail "[31m" survives verbatim (neutered, not hidden).
    assert "[31mRED" in cleaned
    assert cleaned.endswith("end")
    assert sanitize_untrusted(None) is None


def test_compute_field_statuses_four_cases() -> None:
    """Scenario: the drift rule classifies ok / broken / fallback_unused / absent_optional.

    Boundaries: pure over proposals + validation; exercises every branch of the rule.
    On failure: an unused fallback or optional selector would gate CI, or a real break would hide.
    """
    fields = ["BOOK_TITLE", "AUTHORS", "AUTHORS_ALT", "ISBN10", "DETAILS_BUTTON", "DESCRIPTION", "DESCRIPTION_ALT"]
    current = {
        "BOOK_TITLE": "#t",  # matches -> ok
        "AUTHORS": "#a",  # matches -> ok (primary healthy)
        "AUTHORS_ALT": "#a2",  # misses, primary AUTHORS healthy -> fallback_unused
        "ISBN10": None,  # unset -> absent_optional
        "DETAILS_BUTTON": "#btn",  # misses, OPTIONAL -> absent_optional
        "DESCRIPTION": "#d",  # misses, needed -> broken
        "DESCRIPTION_ALT": "#d2",  # misses, primary DESCRIPTION also broken -> broken
    }
    cur_prop = _build_current_proposals(fields, current)
    cur_val = {
        "BOOK_TITLE": {"per_url": [{"url": "u0", "count": 1}]},
        "AUTHORS": {"per_url": [{"url": "u0", "count": 2}]},
        "AUTHORS_ALT": {"per_url": [{"url": "u0", "count": 0}]},
        "ISBN10": {"per_url": [{"url": "u0", "count": 0}]},
        "DETAILS_BUTTON": {"per_url": [{"url": "u0", "count": 0}]},
        "DESCRIPTION": {"per_url": [{"url": "u0", "count": 0}]},
        "DESCRIPTION_ALT": {"per_url": [{"url": "u0", "count": 0}]},
    }
    statuses = compute_field_statuses(fields, cur_prop, cur_val)
    assert statuses == {
        "BOOK_TITLE": "ok",
        "AUTHORS": "ok",
        "AUTHORS_ALT": "fallback_unused",
        "ISBN10": "absent_optional",
        "DETAILS_BUTTON": "absent_optional",
        "DESCRIPTION": "broken",
        "DESCRIPTION_ALT": "broken",
    }
    # Only the two genuinely-broken needed fields gate.
    assert count_current_broken(fields, cur_prop, cur_val) == 2


def test_primary_is_broken_guard_branches() -> None:
    """Scenario: a fallback does not gate when its primary is absent / unset / optional.

    Boundaries: pure over _primary_is_broken; exercises each early-return guard.
    On failure: an unused fallback whose primary is healthy/optional/unset would wrongly gate CI.
    """
    miss = {"X": {"per_url": [{"url": "u0", "count": 0}]}}
    # Primary not in the field set.
    assert _primary_is_broken("X", [], {"X": {"selector": "#x"}}, miss) is False
    # Primary has no selector set.
    assert _primary_is_broken("X", ["X"], {"X": {"selector": None}}, miss) is False
    # Primary is itself optional.
    optional = next(iter(parameters.OPTIONAL_SELECTOR_KEYS))
    assert (
        _primary_is_broken(
            optional, [optional], {optional: {"selector": "#o"}}, {optional: {"per_url": [{"url": "u0", "count": 0}]}}
        )
        is False
    )
    # Primary is a real, non-optional selector matching nothing -> broken.
    assert _primary_is_broken("X", ["X"], {"X": {"selector": "#x"}}, miss) is True


def test_alt_without_primary_is_a_normal_gating_field() -> None:
    """Scenario: an ``*_ALT`` key with no primary in the field set is treated as a normal primary.

    Boundaries: pure; the ``FOO`` primary is absent, so ``FOO_ALT`` gates on its own miss.
    On failure: a stray fallback with no primary would silently never gate.
    """
    fields = ["FOO_ALT"]
    cur_prop = {"FOO_ALT": {"selector": "#x"}}
    cur_val = {"FOO_ALT": {"per_url": [{"url": "u0", "count": 0}]}}
    assert compute_field_statuses(fields, cur_prop, cur_val) == {"FOO_ALT": "broken"}


def test_card_relative_zero_cards_is_broken() -> None:
    """Scenario: a needed card-relative selector matching zero cards everywhere is broken.

    Boundaries: pure; ``match_count`` carries the per-sample card hits.
    On failure: a search card-relative drift would not gate CI.
    """
    fields = ["SEARCH_TITLE"]
    cur_prop = {"SEARCH_TITLE": {"selector": ".t"}}
    cur_val = {"SEARCH_TITLE": {"per_url": [{"url": "u0", "match_count": 0, "card_count": 8}]}}
    assert compute_field_statuses(fields, cur_prop, cur_val) == {"SEARCH_TITLE": "broken"}


def test_format_check_only_usage_line_names_no_ai_no_key() -> None:
    line = format_check_only_usage_line(3)
    assert "no AI call" in line
    assert "no API key required" in line
    assert "3 sample(s)" in line
    assert "$" not in line


# --------------------------------------------------------------------------- #
# format_json_report (subtask 08) - schema, current_matches, AIS-12, AIS-13
# --------------------------------------------------------------------------- #
def _json_inputs():
    """Shared normal-mode inputs: ISBN13 changed+inconsistent+current-broken, BOOK_TITLE unchanged."""
    site = "amazon"
    labels = ["u0", "u1"]
    fields = ["ISBN13", "BOOK_TITLE"]
    proposals = {
        "ISBN13": {"selector": "#new", "rationale": "stable id"},
        "BOOK_TITLE": {"selector": "#t", "rationale": "unchanged"},
    }
    current = {"ISBN13": "#old", "BOOK_TITLE": "#t"}
    validation = {
        "ISBN13": {
            "per_url": [
                {"url": "u0", "matched": True, "count": 1, "snippet": "x"},
                {"url": "u1", "matched": False, "count": 0, "snippet": ""},
            ],
            "consistent": False,
        },
        "BOOK_TITLE": {
            "per_url": [{"url": "u0", "count": 1}, {"url": "u1", "count": 1}],
            "consistent": True,
        },
    }
    current_proposals = _build_current_proposals(fields, current)
    current_validation = {
        "ISBN13": {"per_url": [{"url": "u0", "count": 0}, {"url": "u1", "count": 0}], "consistent": True},
        "BOOK_TITLE": {"per_url": [{"url": "u0", "count": 1}, {"url": "u1", "count": 1}], "consistent": True},
    }
    return site, labels, fields, proposals, current, validation, current_proposals, current_validation


def test_json_report_matches_schema() -> None:
    """Scenario: the JSON report carries the documented schema and summary (subtask 08).

    Boundaries: pure formatting over crafted validation dicts.
    On failure: a CI consumer parsing the drift artifact would read the wrong keys/counts.
    """
    site, labels, fields, proposals, current, validation, cur_prop, cur_val = _json_inputs()
    data = _json.loads(
        format_json_report(
            site,
            "detail",
            "claude-sonnet-5",
            labels,
            fields,
            proposals,
            current,
            validation,
            cur_prop,
            cur_val,
            check_current_only=False,
        )
    )
    assert data["site"] == "amazon"
    assert data["page_type"] == "detail"
    assert data["model"] == "claude-sonnet-5"
    assert data["samples"] == ["u0", "u1"]
    isbn = next(f for f in data["fields"] if f["name"] == "ISBN13")
    assert isbn["current"] == "#old"
    assert isbn["proposed"] == "#new"
    assert isbn["changed"] is True
    assert isbn["rationale"] == "stable id"
    assert isbn["matches"] == {"u0": 1, "u1": 0}
    assert isbn["consistent"] is False
    assert isbn["current_matches"] == {"u0": 0, "u1": 0}
    assert isbn["status"] == "broken"
    title = next(f for f in data["fields"] if f["name"] == "BOOK_TITLE")
    assert title["changed"] is False
    assert title["status"] == "ok"
    assert data["summary"] == {"changed": 1, "inconsistent": 1, "current_broken": 1}


def test_current_matches_reported_for_each_sample() -> None:
    """Scenario: every field's current_matches has one entry per sample (subtask 08).

    Boundaries: pure formatting; two samples.
    On failure: a per-sample drift signal would be dropped, hiding which page a selector broke on.
    """
    site, labels, fields, proposals, current, validation, cur_prop, cur_val = _json_inputs()
    data = _json.loads(
        format_json_report(
            site,
            "detail",
            "m",
            labels,
            fields,
            proposals,
            current,
            validation,
            cur_prop,
            cur_val,
            check_current_only=False,
        )
    )
    for field in data["fields"]:
        assert set(field["current_matches"].keys()) == {"u0", "u1"}


def test_json_report_check_current_only_nulls_proposed_fields() -> None:
    """Scenario: check-current-only JSON nulls proposed/rationale/changed/model and summary.changed.

    Boundaries: pure formatting; matches/consistent reflect the current selector only.
    On failure: a drift job would read phantom AI proposals that were never made.
    """
    site, labels, fields, _proposals, current, _validation, cur_prop, cur_val = _json_inputs()
    data = _json.loads(
        format_json_report(
            site,
            "detail",
            None,
            labels,
            fields,
            {},
            current,
            {},
            cur_prop,
            cur_val,
            check_current_only=True,
        )
    )
    assert data["model"] is None
    assert data["summary"]["changed"] is None
    for field in data["fields"]:
        assert field["proposed"] is None
        assert field["rationale"] is None
        assert field["changed"] is None
        assert field["matches"] == {}
        assert set(field["current_matches"].keys()) == {"u0", "u1"}
    # ISBN13's current selector matched no sample -> counted broken even with no AI step.
    assert data["summary"]["current_broken"] == 1


def test_json_report_field_absent_from_proposals_is_null() -> None:
    """Scenario: a field in the schema but absent from the merged proposals serializes as null.

    Boundaries: pure formatting; the merge dropped the field (no page proposed it).
    On failure: a KeyError would abort serialization, or a phantom proposal would appear.
    """
    fields = ["ISBN13", "TAGS"]
    current = {"ISBN13": "#old", "TAGS": "#tags"}
    cur_prop = _build_current_proposals(fields, current)
    cur_val = {
        "ISBN13": {"per_url": [{"url": "u0", "count": 1}]},
        "TAGS": {"per_url": [{"url": "u0", "count": 1}]},
    }
    data = _json.loads(
        format_json_report(
            "amazon",
            "detail",
            "m",
            ["u0"],
            fields,
            {"ISBN13": {"selector": "#new", "rationale": "r"}},  # TAGS not proposed
            current,
            {"ISBN13": {"per_url": [{"url": "u0", "count": 1}], "consistent": True}},
            cur_prop,
            cur_val,
            check_current_only=False,
        )
    )
    tags = next(f for f in data["fields"] if f["name"] == "TAGS")
    assert tags["proposed"] is None
    assert tags["changed"] is None
    assert tags["matches"] == {}


def test_json_report_null_current_selector_serializes_as_null() -> None:
    """Scenario: a field the site leaves unset (current is None) serializes current as JSON null.

    Boundaries: pure formatting; the current value is None (e.g. leanpub ISBN10).
    On failure: a non-string current value would crash serialization or be miscounted as drift.
    """
    fields = ["ISBN10"]
    current = {"ISBN10": None}
    cur_prop = _build_current_proposals(fields, current)
    cur_val = {"ISBN10": {"per_url": [{"url": "u0", "count": 0}], "consistent": True}}
    data = _json.loads(
        format_json_report(
            "leanpub",
            "detail",
            "m",
            ["u0"],
            fields,
            {"ISBN10": {"selector": None, "rationale": ""}},
            current,
            {"ISBN10": {"per_url": [{"url": "u0", "count": 0}], "consistent": True}},
            cur_prop,
            cur_val,
            check_current_only=False,
        )
    )
    field = data["fields"][0]
    assert field["current"] is None
    # A null current selector is never counted as drift.
    assert data["summary"]["current_broken"] == 0


def test_json_report_omits_key_and_html_bounds_rationale() -> None:
    """Scenario: the JSON artifact carries no key, no HTML, and a rationale bounded to 200 chars (AIS-12).

    Boundaries: pure formatting; the rationale is overlong and a fake key is embedded in it.
    On failure: a committed CI artifact could leak the credential or page PII / full markup.
    """
    long_rationale = "R" * 500 + " sk-ant-SECRET <html><body>page</body></html>"
    report = format_json_report(
        "amazon",
        "detail",
        "m",
        ["u0"],
        ["ISBN13"],
        {"ISBN13": {"selector": "#new", "rationale": long_rationale}},
        {"ISBN13": "#old"},
        {"ISBN13": {"per_url": [{"url": "u0", "count": 1}], "consistent": True}},
        _build_current_proposals(["ISBN13"], {"ISBN13": "#old"}),
        {"ISBN13": {"per_url": [{"url": "u0", "count": 0}], "consistent": True}},
        check_current_only=False,
    )
    data = _json.loads(report)
    rationale = data["fields"][0]["rationale"]
    assert len(rationale) == RATIONALE_MAX_CHARS
    # Truncation occurs before the embedded secret/markup, so neither reaches the artifact.
    assert "sk-ant-SECRET" not in report
    assert "<html>" not in report
    assert "<body>" not in report


def test_reports_strip_ansi_and_control_chars_from_rationale_and_selector() -> None:
    """Scenario: AIS-13 stripping applies to rationale AND proposed selector in BOTH reports.

    Boundaries: a payload with \\x1b[31m + \\r + \\x07 in both the rationale and the selector.
    On failure: a hostile string could inject terminal escapes into stdout/--output or a JSON file.
    """
    hostile_sel = "#id\x1b[31m\r\x07x"
    hostile_rat = "why\x1b[0m\r\x07because"
    proposals = {"ISBN13": {"selector": hostile_sel, "rationale": hostile_rat}}
    current = {"ISBN13": "#old"}
    validation = {
        "ISBN13": {"per_url": [{"url": "u0", "matched": True, "count": 1, "snippet": "ok"}], "consistent": True}
    }
    cur_prop = _build_current_proposals(["ISBN13"], current)
    cur_val = {"ISBN13": {"per_url": [{"url": "u0", "count": 0}], "consistent": True}}

    text = format_diff_report("amazon", ["u0"], proposals, current, validation)
    json_report = format_json_report(
        "amazon",
        "detail",
        "m",
        ["u0"],
        ["ISBN13"],
        proposals,
        current,
        validation,
        cur_prop,
        cur_val,
        check_current_only=False,
    )
    for bad in ("\x1b", "\r", "\x07"):
        assert bad not in text
        assert bad not in json_report
    # The neutered printable tail still shows (so the reviewer sees the real content).
    assert "because" in text
    parsed = _json.loads(json_report)
    assert parsed["fields"][0]["proposed"].startswith("#id")
    assert "because" in parsed["fields"][0]["rationale"]
