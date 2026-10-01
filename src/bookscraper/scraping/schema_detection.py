"""AI-assisted CSS-selector schema detection for the four book sites.

This module automates the manual "figure out the selectors for a site's detail page"
loop documented in ``docs/scraping/schema-detection.md``. Given a site name and one or
more sample detail-page URLs it:

1. loads each URL with Playwright (reusing ``scrape_details.route_handler``'s
   resource-blocking so only the document is fetched) and extracts a pruned HTML
   snapshot small enough to hand to an LLM (``prune_html``);
2. asks Claude, via the official ``anthropic`` SDK, to propose a CSS selector for each
   detail-page field already present in ``parameters.site_constants[site]`` - one request
   per sample page, sharing a cacheable static instruction block, each fitted to a
   pre-flight input-token budget - and merges the per-page proposals
   (``build_instruction_block`` / ``call_anthropic`` / ``validate_ai_response``);
3. re-queries every proposed selector against each sample page's *live* DOM and reports
   whether it matched, how many elements, a text snippet, and whether the result is
   consistent across all sample URLs (``validate_across_samples``);
4. emits a diff-style report comparing the proposals to the current values
   (``format_diff_report``).

Hard boundary: this tool **never** writes to ``parameters.py``. It proposes; a human
applies. It also fails loudly - a missing ``ANTHROPIC_API_KEY``, a page-load failure,
or a malformed/non-JSON AI response each raise ``SchemaDetectionError`` rather than
producing partial or guessed output.

Offline modes (AIS-9, an acceptance gate): the sample pages can come from committed local
HTML files (``html_files=``) instead of live URLs - each is read with a size cap (AIS-11),
loaded into Chromium via ``set_content`` with **every** request aborted (no network at all),
and pruned deterministically from the file bytes. And the model step can be served from a
recorded ``replay=`` file (responses keyed by ``sha256`` of the exact prompt the code builds):
in replay mode no Anthropic client is ever constructed and no ``ANTHROPIC_API_KEY`` is
required, so the whole pipeline runs in CI with zero network and no key; a prompt missing from
the replay file fails loudly. ``record=`` writes the per-prompt responses of a live run back
out for a maintainer to curate into a replay file (never the API key).

Import direction: this lives under ``scraping/`` and imports only from ``scraping/`` and
``book_utils`` - never from ``commands/`` or ``backends/`` (no storage is involved).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import re
from pathlib import Path
from typing import Any, Optional

import anthropic
from playwright.async_api import Browser, Page, Route, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from ..book_utils import print_log
from .parameters import HEADLESS_BROWSER, NON_SELECTOR_SEARCH_KEYS, USER_AGENTS, site_constants
from .scrape_details import route_handler

module_logger = logging.getLogger("schema_detection")

# Default model; overridable via ANTHROPIC_MODEL (or the ``--model`` flag) so a caller can
# pin/upgrade without a code change. The exact value is irrelevant to the test suite, which
# always mocks the anthropic client (no real API call is ever made from tests). ``claude-sonnet-5``
# runs ADAPTIVE THINKING by default and thinking tokens count toward ``max_tokens``; it also
# rejects sampling params (temperature/top_p/top_k) and ``budget_tokens`` with a 400, so none of
# those are sent.
DEFAULT_MODEL = "claude-sonnet-5"
# Raised from 2048: on a thinking-by-default model the thinking tokens share the ``max_tokens``
# budget, so a smaller cap risks truncating the JSON answer. 16000 is the skill's non-streaming
# default.
DEFAULT_MAX_TOKENS = 16000

# Upper bound on the pruned-HTML payload handed to the LLM. Kept well under the model's
# context window while leaving room for the prompt scaffolding and the response.
DEFAULT_HTML_BUDGET = 60000

# Pre-flight input-token budget per request (overridable via ``--max-input-tokens``). If a built
# prompt exceeds it, the HTML snapshot is re-pruned toward ``MIN_HTML_BUDGET`` characters; if it
# still does not fit, the request is abandoned (``SchemaDetectionError``) rather than sent.
MAX_INPUT_TOKENS = 40_000
# The floor the re-prune loop will not shrink the snapshot below.
MIN_HTML_BUDGET = 10_000

# Hard per-run ceiling on sample URLs (and therefore on paid requests): a cost guard, enforced
# before any browser launch, network fetch, or API call.
MAX_SAMPLE_URLS = 5

# At most one retry on a transient SDK error. The anthropic client's own ``max_retries`` is set to
# this same value so there is exactly one retry layer, never stacked unbounded retries.
MAX_API_RETRIES = 1

# AIS-11: hard cap on a single ``--html-file`` payload, aligned with the 06.0 corpus's 400 KB
# per-file budget (``tests/fixtures/README.md``). A file over this - or missing/unreadable/not
# valid UTF-8 - fails loudly rather than being read into memory and ``set_content``'d.
HTML_FILE_MAX_BYTES = 400 * 1024

# Keys in a site_constants entry that are NOT detail-page CSS selectors and so should
# never be proposed/validated as such: the site base URL and the 404-title sentinel.
_NON_SELECTOR_KEYS = frozenset({"BASE_URL", "404_PAGE_TITLE"})

# Search-page selector classification (used only by ``--page-type search``).
#
# The card-container selector is PAGE-LEVEL: ``page.locator(...).all()`` finds the result
# cards. Only one of these two keys is present per site (Amazon/Leanpub use
# ``SEARCH_BOOK_CARD``; Packtpub/O'Reilly use ``SEARCH_RESULT_ITEM_SELECTOR``).
SEARCH_CARD_KEYS = frozenset({"SEARCH_BOOK_CARD", "SEARCH_RESULT_ITEM_SELECTOR"})
# SEARCH_* selectors evaluated against the whole page rather than inside a single card:
# the card container itself plus the pagination button and the "no results" markers
# (which live outside the cards, per search_utils.get_search_results_via_playwright).
SEARCH_PAGE_LEVEL_KEYS = SEARCH_CARD_KEYS | frozenset(
    {"SEARCH_NEXT_PAGE_SELECTOR", "SEARCH_NO_RESULTS_TEXT", "SEARCH_NO_BOOKS_FOUND"}
)
# A card-relative selector matching fewer than this fraction of the cards on a sample page
# is flagged *partial* (the drift this subtask exists to catch: a per-card selector that
# only resolves on some cards).
CARD_MATCH_THRESHOLD = 0.8


class SchemaDetectionError(Exception):
    """Raised on any unrecoverable schema-detection failure (missing API key, page-load
    failure, malformed AI response). Callers (``commands/detect_schema.py``) surface it
    as a ``print_log`` error and exit non-zero."""


# --------------------------------------------------------------------------- #
# Pure helpers (no I/O, no network) - unit-tested directly.
# --------------------------------------------------------------------------- #
def detail_selector_fields(entry: dict) -> list[str]:
    """Return the detail-page CSS-selector field names in a ``site_constants`` entry.

    Excludes search-result selectors (``SEARCH_*``), URL/API endpoint constants
    (keys ending ``_URL``/``_API``), and the non-selector sentinels in
    ``_NON_SELECTOR_KEYS``. Meta-tag selectors (e.g. ``AUTHORS_META``) are kept - they
    are legitimate detail-page selectors. Keys whose current value is ``None`` are kept
    too, since the whole point may be to discover a selector the site now exposes.
    """
    fields: list[str] = []
    for key in entry:
        if key.startswith("SEARCH_"):
            continue
        if key in _NON_SELECTOR_KEYS:
            continue
        if key.endswith(("_URL", "_API")):
            continue
        fields.append(key)
    return fields


def search_selector_fields(entry: dict) -> list[str]:
    """Return the search-results-page CSS-selector field names in a ``site_constants`` entry.

    Keeps every ``SEARCH_*`` key EXCEPT the non-selector constants in
    ``NON_SELECTOR_SEARCH_KEYS`` (the base search URL/API, the page-count integer, and the
    ``data-asin`` attribute NAME). The remaining keys are the card-container selector
    (``SEARCH_BOOK_CARD`` / ``SEARCH_RESULT_ITEM_SELECTOR``), the card-relative field
    selectors (title/authors/detail-link/publication-date/rating), and the page-level
    pagination / no-results selectors - all genuine CSS selectors, including
    ``SEARCH_NO_RESULTS_TEXT`` / ``SEARCH_NO_BOOKS_FOUND`` whose names belie that they
    select an element. This is the search-page counterpart of ``detail_selector_fields``
    and shares its non-selector classification with ``scripts/capture_fixtures.py`` via
    ``parameters.NON_SELECTOR_SEARCH_KEYS``.
    """
    fields: list[str] = []
    for key in entry:
        if not key.startswith("SEARCH_"):
            continue
        if key in NON_SELECTOR_SEARCH_KEYS:
            continue
        fields.append(key)
    return fields


def prune_html(html: str, max_chars: int = DEFAULT_HTML_BUDGET) -> str:
    """Strip an HTML document down to a compact, LLM-friendly snapshot.

    Removes comments and the entire contents of ``<script>``/``<style>``/``<svg>``/
    ``<noscript>`` elements (noise that carries no selector signal), collapses runs of
    whitespace, and truncates the result to ``max_chars`` characters. Pure string
    transformation - no parsing library, no I/O.
    """
    if not html:
        return ""

    text = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    for tag in ("script", "style", "svg", "noscript"):
        text = re.sub(rf"<{tag}\b.*?</{tag}>", "", text, flags=re.DOTALL | re.IGNORECASE)
        # Also drop self-closing / unclosed leftovers of the tag's opening.
        text = re.sub(rf"<{tag}\b[^>]*/?>", "", text, flags=re.IGNORECASE)

    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars]
    return text


def build_instruction_block(site: str, field_names: list[str], page_type: str = "detail") -> str:
    """Build the *static* instruction block sent to Claude as the cacheable prefix.

    Contains only the site name, the field list, and the output contract - no per-page
    content - so it is byte-identical across every sample URL in a run and can be served
    from Anthropic's prompt cache on the 2nd+ request (the snapshot, which varies per page,
    is sent separately as the user message). Asks for a JSON object keyed by the *existing*
    field names (so Claude proposes values for known keys, not new keys), each value an
    object with a ``selector`` and a short ``rationale``. Pure string assembly.

    ``page_type`` selects the wording: ``"detail"`` (the default) targets a single book
    detail page; ``"search"`` targets a search-results listing and additionally explains
    the card-relative semantics - the card-container field
    (``SEARCH_BOOK_CARD`` / ``SEARCH_RESULT_ITEM_SELECTOR``) and the pagination/no-results
    fields are page-level, while every other field is evaluated *inside a single card*.
    """
    fields_block = "\n".join(f"  - {name}" for name in field_names)
    if page_type == "search":
        intro = (
            f"You are helping maintain a web scraper's CSS-selector schema for the "
            f"'{site}' book SEARCH-RESULTS page.\n\n"
            "This is a listing page made of many repeated book 'cards'. The card-container "
            "field (SEARCH_BOOK_CARD or SEARCH_RESULT_ITEM_SELECTOR) is PAGE-LEVEL and must "
            "match each result card on the page; the pagination and no-results fields "
            "(SEARCH_NEXT_PAGE_SELECTOR / SEARCH_NO_RESULTS_TEXT / SEARCH_NO_BOOKS_FOUND) "
            "are page-level too. Every OTHER field (title, authors, detail link, "
            "publication date, rating) is evaluated INSIDE a single card, so its selector "
            "must be correct when queried relative to one card element, not the whole page.\n\n"
            "You will be given a pruned HTML snapshot of one sample search-results page. "
        )
    else:
        intro = (
            f"You are helping maintain a web scraper's CSS-selector schema for the "
            f"'{site}' book detail page.\n\n"
            "You will be given a pruned HTML snapshot of one sample detail page. "
        )
    return (
        intro + "For each of the following fields, propose the most robust CSS selector that "
        "Playwright's `page.locator(selector)` could use to extract that field's value. "
        "Prefer stable `id` and `data-*` attributes over fragile auto-generated class "
        "chains. If a field genuinely has no corresponding element on the page, use null "
        "for its selector.\n\n"
        "Fields (use exactly these keys, do not invent new ones):\n"
        f"{fields_block}\n\n"
        "Respond with ONLY a single JSON object (no prose, no markdown fences) mapping "
        "each field name to an object of the form "
        '{"selector": "<css or null>", "rationale": "<one short sentence>"}.'
    )


def build_snapshot_block(pruned_html: str) -> str:
    """Build the per-page snapshot block (the user message), which varies per sample URL
    and is therefore *not* part of the cacheable prefix. Pure string assembly."""
    return f"Pruned HTML snapshot:\n{pruned_html}\n"


def build_prompt(site: str, field_names: list[str], pruned_html: str) -> str:
    """Return the combined single-string view of the instruction block and the snapshot.

    Retained for debugging and the pure-logic unit tests; the live pipeline sends the two
    blocks separately (see ``build_instruction_block`` / ``build_snapshot_block``) so the
    instruction block can be marked cacheable.
    """
    return build_instruction_block(site, field_names) + "\n\n" + build_snapshot_block(pruned_html)


def _strip_code_fences(text: str) -> str:
    """Remove a leading/trailing ```json ... ``` (or ``` ... ```) fence if present.

    Claude is asked for raw JSON but may still wrap it; tolerate that one common shape
    rather than failing a perfectly good response over cosmetics.
    """
    stripped = text.strip()
    fence = re.match(r"^```[a-zA-Z0-9]*\n(.*)\n```$", stripped, flags=re.DOTALL)
    if fence:
        return fence.group(1).strip()
    return stripped


def validate_ai_response(raw_text: str, field_names: list[str]) -> dict[str, dict[str, Any]]:
    """Parse and shape-validate the model's JSON reply.

    Returns ``{field: {"selector": <str|None>, "rationale": <str>}}`` for every key the
    model returned that is one of ``field_names``. Raises ``SchemaDetectionError`` if the
    payload is not JSON, is not a top-level object, or any proposal is not an object
    carrying a ``selector`` that is a string or null. Keys the model returns that are not
    in ``field_names`` are ignored (it was asked not to invent keys, but a stray one is
    not worth aborting a whole run over).
    """
    text = _strip_code_fences(raw_text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SchemaDetectionError(f"AI response was not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise SchemaDetectionError("AI response JSON must be an object keyed by field name.")

    allowed = set(field_names)
    proposals: dict[str, dict[str, Any]] = {}
    for key, value in data.items():
        if key not in allowed:
            continue
        if not isinstance(value, dict) or "selector" not in value:
            raise SchemaDetectionError(f"AI proposal for '{key}' must be an object with a 'selector' field.")
        selector = value.get("selector")
        if selector is not None and not isinstance(selector, str):
            raise SchemaDetectionError(f"AI proposal for '{key}' has a non-string, non-null selector.")
        rationale = value.get("rationale", "")
        proposals[key] = {"selector": selector, "rationale": str(rationale) if rationale else ""}

    if not proposals:
        raise SchemaDetectionError("AI response contained no usable proposals for the requested fields.")
    return proposals


def _fmt_selector(value: Optional[str]) -> str:
    """Render a selector value (possibly None) for the diff report."""
    return "None" if value is None else repr(value)


def format_diff_report(
    site: str,
    urls: list[str],
    proposals: dict[str, dict[str, Any]],
    current: dict[str, Any],
    validation: dict[str, dict[str, Any]],
) -> str:
    """Build the human-facing report string.

    For each proposed field it shows the current vs. proposed selector (diff-style
    ``-``/``+`` when they differ, ``=`` when identical), the per-URL live-DOM validation
    result, and an explicit inconsistency warning where a selector matched on some sample
    URLs but not others. For a search-page run a card-relative field's per-URL result
    additionally carries the card count and the fraction of cards it matched; a field that
    matched fewer than ``CARD_MATCH_THRESHOLD`` of the cards on any sample page is flagged
    *partial*. Pure formatting - all live data is precomputed in ``validation``.
    """
    lines: list[str] = []
    lines.append(f"Schema-detection proposals for site '{site}'")
    lines.append(f"Sample URLs ({len(urls)}):")
    for url in urls:
        lines.append(f"  - {url}")
    lines.append("")
    lines.append("NOTE: This is a proposal only. Nothing has been written to parameters.py.")
    lines.append("Review each field below and apply desired changes to site_constants by hand.")
    lines.append("=" * 72)

    inconsistent_fields: list[str] = []
    partial_fields: list[str] = []

    for field in proposals:
        current_value = current.get(field, "<field not currently present>")
        proposed_value = proposals[field]["selector"]
        rationale = proposals[field]["rationale"]
        field_validation = validation.get(field, {})
        per_url = field_validation.get("per_url", [])
        consistent = field_validation.get("consistent", True)
        partial = field_validation.get("partial", False)

        lines.append("")
        lines.append(f"### {field}")
        if current_value == proposed_value:
            lines.append(f"  = (unchanged) {_fmt_selector(proposed_value)}")
        else:
            lines.append(
                f"  - current : {_fmt_selector(current_value) if current_value != '<field not currently present>' else current_value}"
            )
            lines.append(f"  + proposed: {_fmt_selector(proposed_value)}")
        if rationale:
            lines.append(f"    rationale: {rationale}")

        lines.append("    validation:")
        for result in per_url:
            lines.append("      " + _format_validation_result(result))

        if not consistent:
            inconsistent_fields.append(field)
            lines.append(
                "    !! INCONSISTENT across sample URLs - matched on some pages but not others. Review before applying."
            )
        if partial:
            partial_fields.append(field)
            lines.append(
                "    !! PARTIAL - matched fewer than 80% of the cards on at least one sample URL. "
                "Review before applying."
            )

    lines.append("")
    lines.append("=" * 72)
    summary: list[str] = []
    if inconsistent_fields:
        summary.append(
            f"WARNING: {len(inconsistent_fields)} field(s) validated inconsistently "
            f"across sample URLs: {', '.join(inconsistent_fields)}"
        )
    if partial_fields:
        summary.append(
            f"WARNING: {len(partial_fields)} card-relative selector(s) matched under 80% of the "
            f"cards on at least one sample URL: {', '.join(partial_fields)}"
        )
    if summary:
        lines.extend(summary)
    else:
        lines.append("All proposed selectors validated consistently across the sample URLs.")
    return "\n".join(lines)


def _format_validation_result(result: dict[str, Any]) -> str:
    """Render one per-URL validation line for the report.

    A card-relative result (carrying a ``fraction``) is shown as ``matched/total cards``
    plus a percentage; every other result keeps the detail-page ``N element(s)`` form so
    the detail report is byte-identical to before. Pure formatting.
    """
    url = result["url"]
    snippet = result.get("snippet", "")
    tag = "[MATCH]" if result.get("matched") else "[ no  ]"
    if "fraction" in result:
        card_count = result.get("card_count", 0)
        match_count = result.get("match_count", 0)
        pct = round(result["fraction"] * 100)
        snippet_note = f' text="{snippet}"' if snippet else ""
        return f"{tag} {url} ({match_count}/{card_count} cards matched, {pct}%){snippet_note}"
    if result.get("matched"):
        snippet_note = f' text="{snippet}"' if snippet else ""
        return f"{tag} {url} ({result.get('count', 0)} element(s)){snippet_note}"
    note_str = f" ({snippet})" if snippet else ""
    return f"{tag} {url} (0 elements){note_str}"


def resolve_model(cli_model: Optional[str]) -> str:
    """Resolve the model ID with precedence ``--model`` flag > ``ANTHROPIC_MODEL`` env > default.

    ``cli_model`` is the parsed ``--model`` value (``None`` when the flag was omitted). An empty
    or whitespace-only ID - whether from the flag or the env var - is rejected with
    ``SchemaDetectionError``; any other value is passed through unchanged for the API to accept
    or reject.
    """
    model = cli_model if cli_model is not None else os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)
    if not model.strip():
        raise SchemaDetectionError("Model ID must not be empty. Set --model or ANTHROPIC_MODEL to a valid model ID.")
    return model


def format_usage_line(model: str, usages: list[Any], request_count: int) -> str:
    """Format the one-line run usage summary from each response's ``usage`` object.

    Sums input / cache-read / cache-creation / output tokens across every response and reports
    them with the model and request count. Carries **no** prices (they change, and a stale figure
    would mislead) and **no** API key. Pure formatting.
    """
    input_tokens = sum(int(getattr(usage, "input_tokens", 0) or 0) for usage in usages)
    cache_read = sum(int(getattr(usage, "cache_read_input_tokens", 0) or 0) for usage in usages)
    cache_creation = sum(int(getattr(usage, "cache_creation_input_tokens", 0) or 0) for usage in usages)
    output_tokens = sum(int(getattr(usage, "output_tokens", 0) or 0) for usage in usages)
    return (
        f"Anthropic usage: model={model} requests={request_count} "
        f"input_tokens={input_tokens} cache_read_input_tokens={cache_read} "
        f"cache_creation_input_tokens={cache_creation} output_tokens={output_tokens}"
    )


def prompt_hash(instruction_block: str, snapshot_block: str) -> str:
    """Return the ``sha256`` key for a prompt, used to index the ``--replay``/``--record`` file.

    The key is the hex ``sha256`` of the exact prompt the live path would send - the static
    instruction block and the per-page snapshot block joined the same way ``build_prompt``
    joins them. Both the runtime (replay lookup / record write) and the maintenance script that
    generates a replay file compute the key through this one function and ``build_snapshot_block``
    so the two can never drift. Pure: no I/O, no network.
    """
    prompt = f"{instruction_block}\n\n{snapshot_block}"
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def format_replay_usage_line(model: str, replayed: int) -> str:
    """Format the usage line for an offline ``--replay`` run: zero tokens, ``N`` replayed.

    No Anthropic request is made in replay mode, so there is no real token usage to report;
    the line mirrors ``format_usage_line``'s shape (all token counts zero) and names how many
    recorded responses were served. Pure formatting; carries no key.
    """
    return (
        f"Anthropic usage: model={model} requests=0 replayed={replayed} "
        f"input_tokens=0 cache_read_input_tokens=0 cache_creation_input_tokens=0 "
        f"output_tokens=0 (offline replay; no API call, no tokens spent)"
    )


def _merge_proposals(
    per_url_proposals: list[dict[str, dict[str, Any]]],
    field_names: list[str],
) -> dict[str, dict[str, Any]]:
    """Merge the per-URL proposal sets into one set, field by field.

    One request is sent per sample page (so each contributes a proposal set); for each field the
    first page that proposed a non-null selector wins, otherwise the first page that mentioned the
    field at all (a null proposal). Later cross-sample DOM validation then re-checks the merged
    selector against every page, so a per-page false positive is still surfaced.
    """
    merged: dict[str, dict[str, Any]] = {}
    for field in field_names:
        fallback: Optional[dict[str, Any]] = None
        for proposals in per_url_proposals:
            if field not in proposals:
                continue
            if fallback is None:
                fallback = proposals[field]
            if proposals[field]["selector"] is not None:
                merged[field] = proposals[field]
                break
        else:
            if fallback is not None:
                merged[field] = fallback
    return merged


# --------------------------------------------------------------------------- #
# External-collaborator helpers (network / browser) - mock-tested.
# --------------------------------------------------------------------------- #
def _extract_text(message: anthropic.types.Message) -> str:
    """Concatenate the ``text`` blocks of an anthropic ``Message`` response.

    Only ``type == "text"`` blocks are collected; ``thinking`` blocks (present on an
    adaptive-thinking model) are skipped so a thinking summary never leaks into the JSON parse.
    """
    parts: list[str] = []
    for block in getattr(message, "content", []) or []:
        if getattr(block, "type", None) != "text":
            continue
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    if not parts:
        raise SchemaDetectionError("AI response contained no text content.")
    return "\n".join(parts)


def _sdk_error_summary(exc: Exception) -> str:
    """Summarize an SDK exception as its class name (plus HTTP status if present), never its body.

    AIS-8/AIS-16: the Anthropic error body can echo request content and the account's error
    detail; the API key is never in it but is never risked either. Mirrors ``rotate-cert``'s
    "status code, never response body" rule.
    """
    status = getattr(exc, "status_code", None)
    summary = f"Anthropic API request failed: {type(exc).__name__}"
    if status is not None:
        summary = f"{summary} (HTTP {status})"
    return summary


def _check_stop_reason(message: anthropic.types.Message) -> None:
    """Raise ``SchemaDetectionError`` for a truncated or refused response, so neither is parsed.

    ``max_tokens`` means the JSON answer was cut off (incomplete, must not be parsed); ``refusal``
    means the model declined - the ``stop_details.category`` is named when present. Any other stop
    reason (``end_turn`` etc.) returns normally.
    """
    reason = getattr(message, "stop_reason", None)
    if reason == "max_tokens":
        raise SchemaDetectionError(
            "AI response was truncated: it hit max_tokens before completing the JSON answer. "
            "The output is incomplete and will not be parsed."
        )
    if reason == "refusal":
        details = getattr(message, "stop_details", None)
        category = getattr(details, "category", None) if details else None
        suffix = f" (category: {category})" if category else ""
        raise SchemaDetectionError(f"AI refused to produce a response{suffix}; not parsing it.")


def _fit_snapshot_to_budget(
    client: anthropic.Anthropic,
    model: str,
    instruction_block: str,
    snapshot: str,
    max_input_tokens: int,
) -> str:
    """Return a snapshot small enough that the full prompt fits ``max_input_tokens``.

    Counts tokens with the SDK's ``count_tokens`` endpoint; while over budget the snapshot is
    halved toward the ``MIN_HTML_BUDGET`` character floor. If the prompt still exceeds the budget
    at the floor, raises ``SchemaDetectionError`` **without sending** the request.
    """
    current = snapshot
    while True:
        try:
            tokens = client.messages.count_tokens(
                model=model,
                system=[{"type": "text", "text": instruction_block}],
                messages=[{"role": "user", "content": current}],
            ).input_tokens
        except Exception as exc:  # noqa: BLE001 - deliberately uniform, body-free failure surface
            raise SchemaDetectionError(_sdk_error_summary(exc)) from exc
        if tokens <= max_input_tokens:
            return current
        if len(current) <= MIN_HTML_BUDGET:
            raise SchemaDetectionError(
                f"Prompt still exceeds the {max_input_tokens}-token budget ({tokens} tokens) "
                f"even after pruning the HTML snapshot to the {MIN_HTML_BUDGET}-character floor. "
                "Not sending the request."
            )
        current = current[: max(MIN_HTML_BUDGET, len(current) // 2)]


def call_anthropic(
    instruction_block: str,
    snapshot: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    max_input_tokens: int = MAX_INPUT_TOKENS,
) -> tuple[str, Any]:
    """Send the instruction block + snapshot to Claude and return ``(raw_text, usage)``.

    Synchronous (the anthropic SDK's ``messages.create`` is blocking); the async orchestrator runs
    it via ``asyncio.to_thread``. The client is built with ``max_retries=MAX_API_RETRIES`` (one
    retry, matched to the module's own bound, so transient errors never stack unbounded retries).
    The static ``instruction_block`` is sent as a cacheable ``system`` block and the per-page
    ``snapshot`` as the user message. The prompt is first fitted to ``max_input_tokens``
    (re-pruning the snapshot, or failing without sending). A truncated (``max_tokens``) or refused
    response raises rather than being parsed, and any SDK/transport error is wrapped as
    ``SchemaDetectionError`` carrying only the exception class (and HTTP status), never its body.
    """
    try:
        client = anthropic.Anthropic(api_key=api_key, max_retries=MAX_API_RETRIES)
    except Exception as exc:  # noqa: BLE001 - deliberately uniform, body-free failure surface
        raise SchemaDetectionError(_sdk_error_summary(exc)) from exc
    fitted = _fit_snapshot_to_budget(client, model, instruction_block, snapshot, max_input_tokens)
    try:
        message = client.messages.create(
            model=model,
            max_tokens=DEFAULT_MAX_TOKENS,
            system=[
                {
                    "type": "text",
                    "text": instruction_block,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": fitted}],
        )
    except Exception as exc:  # noqa: BLE001 - deliberately uniform, body-free failure surface
        raise SchemaDetectionError(_sdk_error_summary(exc)) from exc
    _check_stop_reason(message)
    return _extract_text(message), getattr(message, "usage", None)


async def fetch_pruned_html(url: str, page: Page, max_chars: int = DEFAULT_HTML_BUDGET) -> str:
    """Navigate ``page`` to ``url`` and return a pruned HTML snapshot.

    Applies a rotated User-Agent and the shared ``route_handler`` (document-only)
    resource blocking, exactly like ``scrape_details.scrape_book``. Raises
    ``SchemaDetectionError`` on any navigation/load failure.
    """
    try:
        await page.set_extra_http_headers({"User-Agent": random.choice(USER_AGENTS)})
        await page.route("**/*", route_handler)
        await page.goto(url, timeout=60000)
        html = await page.content()
    except PlaywrightTimeoutError as exc:
        raise SchemaDetectionError(f"Timed out loading sample URL '{url}': {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise SchemaDetectionError(f"Failed to load sample URL '{url}': {exc}") from exc
    return prune_html(html, max_chars=max_chars)


async def _block_all_requests(route: Route) -> None:
    """Abort every request (default-deny), used for ``--html-file`` pages.

    Unlike ``route_handler`` (which blocks only non-document subresources and lets the
    document continue to the network), a ``set_content`` page has no document fetch and must
    make **no** network call at all (AIS-9), so this aborts everything - document, subresource,
    anything a loaded fixture might try to pull.
    """
    await route.abort()


def _read_html_file(path: str) -> str:
    """Read a local ``--html-file`` as UTF-8 text, enforcing the AIS-11 size cap.

    Fails loudly (``SchemaDetectionError``) on a missing, oversized, or non-UTF-8/unreadable
    path rather than silently skipping it: the file is caller-supplied but its *content* is
    untrusted markup, so an oversized/binary file must never be read into memory and fed to
    ``set_content``.
    """
    file_path = Path(path)
    try:
        size = file_path.stat().st_size
    except OSError as exc:
        raise SchemaDetectionError(f"Cannot access --html-file '{path}': {exc}") from exc
    if size > HTML_FILE_MAX_BYTES:
        raise SchemaDetectionError(
            f"--html-file '{path}' is {size} bytes, over the {HTML_FILE_MAX_BYTES}-byte cap. "
            "Reduce it to the minimum markup the selectors need (see the 06.0 corpus budget)."
        )
    try:
        return file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SchemaDetectionError(f"Cannot read --html-file '{path}' as UTF-8 text: {exc}") from exc


async def load_html_file_page(
    browser: Browser,
    path: str,
    max_chars: int = DEFAULT_HTML_BUDGET,
) -> tuple[Page, str]:
    """Load a local ``--html-file`` into a new, fully offline Chromium page for validation.

    Reads the file (size-capped UTF-8, AIS-11), opens a page with **all** requests aborted
    (AIS-9: default-deny *before* ``set_content``, so any subresource a fixture references is
    blocked, not fetched), sets the markup, and returns ``(page, pruned_html)``. The pruned
    snapshot is derived from the file bytes (not from ``page.content()``) so it - and therefore
    the replay ``sha256`` key - is deterministic and independent of the Chromium build.
    """
    raw_html = _read_html_file(path)
    page = await browser.new_page()
    await page.route("**/*", _block_all_requests)
    await page.set_content(raw_html, wait_until="domcontentloaded")
    return page, prune_html(raw_html, max_chars=max_chars)


def load_replay_file(path: str) -> dict[str, str]:
    """Load a ``--replay`` file: a JSON object of ``{sha256(prompt): raw_model_response}``.

    Keys beginning with ``_`` (e.g. a ``_provenance`` note on a hand-authored file) are
    ignored, so a recording can carry documentation alongside its hash-keyed entries. Fails
    loudly on a missing/unreadable path or a payload that is not a JSON object.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise SchemaDetectionError(f"Cannot read --replay file '{path}': {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SchemaDetectionError(f"--replay file '{path}' is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SchemaDetectionError(f"--replay file '{path}' must be a JSON object keyed by prompt hash.")
    return {key: value for key, value in data.items() if not key.startswith("_")}


def replay_lookup(replay: dict[str, str], instruction_block: str, snapshot_block: str) -> str:
    """Return the recorded response for a prompt, or fail loudly if it is not recorded (AIS-9).

    A missing entry is a hard error ("no recorded response") - never a silent fall-through to a
    live API call - so an offline run that drifts from its recordings fails rather than spending.
    """
    key = prompt_hash(instruction_block, snapshot_block)
    if key not in replay:
        raise SchemaDetectionError(
            f"No recorded response in the --replay file for prompt hash {key}. "
            "The prompt changed (site fields, page content, or page_type) or the recording is "
            "stale; refresh it with --record against a live run."
        )
    return replay[key]


def write_record_file(path: str, recorded: dict[str, str]) -> None:
    """Write a ``--record`` file: ``{sha256(prompt): raw_model_response}`` from a live run.

    For maintainers refreshing the committed replay recordings. Carries a ``_provenance`` note
    and the hash-keyed responses only - never the ``ANTHROPIC_API_KEY`` (it is never part of a
    prompt or a response) and never any live token figure. Writes solely to ``path``.
    """
    payload: dict[str, str] = {
        "_provenance": "recorded from a live detect-schema run; review before committing as a replay fixture",
    }
    payload.update(recorded)
    try:
        Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as exc:
        raise SchemaDetectionError(f"Cannot write --record file '{path}': {exc}") from exc


async def _validate_page_level_field(
    selector: Optional[str],
    samples: list[dict[str, Any]],
) -> dict[str, Any]:
    """Re-query one page-level selector against every sample page's live DOM.

    Returns ``{"per_url": [{"url", "matched", "count", "snippet"}...], "consistent": bool}``.
    The field is *consistent* when every sample agrees on whether it matched (all hit or all
    miss). A null proposal records a miss; a selector Playwright rejects records a miss and
    is logged - a bad selector must never abort the run. The single sink for a proposed
    selector is ``page.locator(sel).count()`` / ``.first.text_content()`` (AIS-4).
    """
    per_url: list[dict[str, Any]] = []
    for sample in samples:
        url = sample["url"]
        page = sample["page"]
        if not selector:
            # A null proposal cannot match anything; record a miss with a note.
            per_url.append({"url": url, "matched": False, "count": 0, "snippet": "null selector"})
            continue
        try:
            locator = page.locator(selector)
            count = await locator.count()
            snippet = ""
            if count:
                text = await locator.first.text_content()
                snippet = (text or "").strip()[:80]
            per_url.append({"url": url, "matched": count > 0, "count": count, "snippet": snippet})
        except Exception as exc:  # noqa: BLE001 - a bad selector must not abort the run
            module_logger.warning("Selector %r failed on %s: %s", selector, url, exc)
            per_url.append({"url": url, "matched": False, "count": 0, "snippet": "query error"})

    matched_flags = {result["matched"] for result in per_url}
    return {"per_url": per_url, "consistent": len(matched_flags) <= 1}


async def validate_across_samples(
    proposals: dict[str, dict[str, Any]],
    samples: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Re-query every proposed detail-page selector against each sample page's live DOM.

    ``samples`` is a list of ``{"url": str, "page": <playwright Page>}``. Returns
    ``{field: {"per_url": [{"url", "matched", "count", "snippet"}...], "consistent": bool}}``.
    A field is *consistent* when every sample page agrees on whether it matched (all hit
    or all miss); a mixed result is flagged rather than silently resolved.
    """
    return {
        field: await _validate_page_level_field(proposal["selector"], samples) for field, proposal in proposals.items()
    }


async def _validate_card_relative_field(
    selector: Optional[str],
    samples: list[dict[str, Any]],
    cards_per_sample: list[list[Any]],
) -> dict[str, Any]:
    """Re-query one card-relative selector *inside each card* of every sample page.

    For each sample it records the card count, how many cards the selector matched, and the
    resulting fraction. ``matched`` is True only when there is at least one card and the
    fraction is at least ``CARD_MATCH_THRESHOLD``; any sample below that (including a page
    with no cards, or a null selector) sets the field's ``partial`` flag. The only sink for
    the selector is ``card.locator(sel).count()`` / ``.first.text_content()`` (AIS-4).
    """
    per_url: list[dict[str, Any]] = []
    partial = False
    for sample, cards in zip(samples, cards_per_sample, strict=True):
        card_count = len(cards)
        match_count = 0
        snippet = ""
        if selector:
            for card in cards:
                try:
                    sub = card.locator(selector)
                    count = await sub.count()
                    if count > 0 and not snippet:
                        text = await sub.first.text_content()
                        snippet = (text or "").strip()[:80]
                except Exception as exc:  # noqa: BLE001 - a bad selector must not abort the run
                    module_logger.warning("Card-relative selector %r failed on %s: %s", selector, sample["url"], exc)
                    count = 0
                if count > 0:
                    match_count += 1
        fraction = (match_count / card_count) if card_count else 0.0
        matched = card_count > 0 and fraction >= CARD_MATCH_THRESHOLD
        if not matched:
            partial = True
        per_url.append(
            {
                "url": sample["url"],
                "matched": matched,
                "card_count": card_count,
                "match_count": match_count,
                "fraction": fraction,
                "snippet": snippet,
            }
        )
    return {"per_url": per_url, "consistent": True, "partial": partial}


async def validate_search_across_samples(
    proposals: dict[str, dict[str, Any]],
    samples: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Re-query every proposed search-page selector against each sample page's live DOM.

    The card-container field (``SEARCH_CARD_KEYS``) and the page-level pagination/no-results
    fields (``SEARCH_PAGE_LEVEL_KEYS``) are validated against the whole page like the detail
    path. Every other field is *card-relative*: the cards are enumerated once per sample with
    the proposed card selector (``page.locator(card).all()``) and the selector is then
    queried inside each card, reporting the per-sample match fraction. Returns the same
    per-field shape as ``validate_across_samples``, with card-relative fields additionally
    carrying ``card_count`` / ``match_count`` / ``fraction`` per URL and a ``partial`` flag.
    """
    card_field = next((key for key in proposals if key in SEARCH_CARD_KEYS), None)
    card_selector = proposals[card_field]["selector"] if card_field else None

    cards_per_sample: list[list[Any]] = []
    for sample in samples:
        cards: list[Any] = []
        if card_selector:
            try:
                cards = await sample["page"].locator(card_selector).all()
            except Exception as exc:  # noqa: BLE001 - a bad card selector must not abort the run
                module_logger.warning("Card selector %r failed on %s: %s", card_selector, sample["url"], exc)
                cards = []
        cards_per_sample.append(cards)

    results: dict[str, dict[str, Any]] = {}
    for field, proposal in proposals.items():
        if field in SEARCH_PAGE_LEVEL_KEYS:
            results[field] = await _validate_page_level_field(proposal["selector"], samples)
        else:
            results[field] = await _validate_card_relative_field(proposal["selector"], samples, cards_per_sample)
    return results


def _resolve_sample_sources(
    urls: Optional[list[str]],
    html_files: Optional[list[str]],
) -> tuple[list[str], bool]:
    """Resolve the sample source list, enforcing "exactly one of urls / html_files".

    Returns ``(labels, from_files)`` - the list of URLs or file paths and whether they are local
    files. Raises ``SchemaDetectionError`` if neither or both are given, if the chosen list is
    empty, or if it exceeds ``MAX_SAMPLE_URLS`` (the cap applies to ``--html-file`` too, so an
    offline run cannot sidestep the per-run ceiling).
    """
    urls = urls or []
    html_files = html_files or []
    if urls and html_files:
        raise SchemaDetectionError("Pass either --url or --html-file sample pages, not both.")
    labels, from_files = (html_files, True) if html_files else (urls, False)
    if not labels:
        raise SchemaDetectionError("At least one sample --url or --html-file is required.")
    if len(labels) > MAX_SAMPLE_URLS:
        kind = "--html-file paths" if from_files else "sample URLs"
        raise SchemaDetectionError(
            f"Too many {kind} ({len(labels)}); the per-run cap is {MAX_SAMPLE_URLS}. Reduce the number of sample pages."
        )
    return labels, from_files


async def detect_schema(
    site: str,
    urls: Optional[list[str]] = None,
    html_files: Optional[list[str]] = None,
    model: Optional[str] = None,
    max_input_tokens: int = MAX_INPUT_TOKENS,
    page_type: str = "detail",
    replay: Optional[str] = None,
    record: Optional[str] = None,
) -> tuple[str, str]:
    """Run the full detect -> validate -> report pipeline, returning ``(report, usage_line)``.

    Sample pages come from either live ``urls`` or local ``html_files`` (exactly one, each capped
    at ``MAX_SAMPLE_URLS``). One response is obtained per sample - from a live Anthropic request,
    or, when ``replay`` names a recording, served offline by prompt ``sha256`` (AIS-9: no client
    is constructed and no ``ANTHROPIC_API_KEY`` is required; a missing entry fails loudly). The
    per-page proposal sets are merged and validated against every sample page's live DOM.
    ``page_type`` selects the field set/validation: ``"detail"`` (default) validates page-level;
    ``"search"`` validates each card-relative selector as a per-card match fraction. ``record``,
    on a live run, writes the per-prompt responses back out for a maintainer to curate.

    Offline note (AIS-9/AIS-10): in replay mode there is no client, so the ``count_tokens``
    pre-flight budget is skipped entirely (nothing is sent to bound); the usage line reports zero
    tokens and the replayed count. Raises ``SchemaDetectionError`` (never partial output) on: an
    unknown site, no/both/too-many sample sources, a missing ``ANTHROPIC_API_KEY`` (live only), an
    empty model ID, a missing/oversized/unreadable ``--html-file``, a prompt that will not fit the
    token budget (live only), a page-load failure, a missing replay entry, or a
    malformed/truncated/refused AI response.
    """
    if site not in site_constants:
        raise SchemaDetectionError(f"Unknown site '{site}'. Known sites: {', '.join(sorted(site_constants))}.")

    labels, from_files = _resolve_sample_sources(urls, html_files)

    # Resolve the model (and reject an empty ID) before any browser, network, or API call.
    resolved_model = resolve_model(model)

    offline = replay is not None
    replay_map: dict[str, str] = load_replay_file(replay) if offline else {}

    api_key: Optional[str] = None
    if not offline:
        # AIS-9: the key is only required - and only read - on the live path.
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise SchemaDetectionError(
                "ANTHROPIC_API_KEY is not set. Export it (or add it to .env) before running detect-schema."
            )

    current = site_constants[site]
    fields = search_selector_fields(current) if page_type == "search" else detail_selector_fields(current)
    if not fields:
        raise SchemaDetectionError(f"No {page_type}-page selector fields defined for site '{site}'.")

    instruction_block = build_instruction_block(site, fields, page_type=page_type)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=HEADLESS_BROWSER)
        samples: list[dict[str, Any]] = []
        recorded: dict[str, str] = {}
        try:
            for label in labels:
                if from_files:
                    # AIS-9/AIS-11: size-capped read, loaded with every request aborted.
                    page, pruned_html = await load_html_file_page(browser, label)
                else:
                    page = await browser.new_page()
                    pruned_html = await fetch_pruned_html(label, page)
                samples.append({"url": label, "page": page, "pruned_html": pruned_html})

            # One response per sample page, each sharing the cacheable instruction block; collect
            # every response's proposals (and live usage). The merged proposals are then validated
            # against ALL sample pages' live DOM (a selector matching one page's markup can be a
            # false positive - cross-URL validation is what catches that).
            per_url_proposals: list[dict[str, dict[str, Any]]] = []
            usages: list[Any] = []
            for sample in samples:
                snapshot_block = build_snapshot_block(sample["pruned_html"])
                if offline:
                    raw_reply = replay_lookup(replay_map, instruction_block, snapshot_block)
                else:
                    raw_reply, usage = await asyncio.to_thread(
                        call_anthropic,
                        instruction_block,
                        snapshot_block,
                        api_key,
                        resolved_model,
                        max_input_tokens,
                    )
                    usages.append(usage)
                    if record is not None:
                        recorded[prompt_hash(instruction_block, snapshot_block)] = raw_reply
                per_url_proposals.append(validate_ai_response(raw_reply, fields))

            proposals = _merge_proposals(per_url_proposals, fields)
            if page_type == "search":
                validation = await validate_search_across_samples(proposals, samples)
            else:
                validation = await validate_across_samples(proposals, samples)
        finally:
            await browser.close()

    if record is not None and not offline:
        write_record_file(record, recorded)

    print_log(f"Received {len(proposals)} selector proposal(s) for '{site}'.", "info")
    report = format_diff_report(site, labels, proposals, current, validation)
    usage_line = (
        format_replay_usage_line(resolved_model, len(samples))
        if offline
        else format_usage_line(resolved_model, usages, len(usages))
    )
    return report, usage_line
