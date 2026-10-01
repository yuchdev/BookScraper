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

Import direction: this lives under ``scraping/`` and imports only from ``scraping/`` and
``book_utils`` - never from ``commands/`` or ``backends/`` (no storage is involved).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
from typing import Any, Optional

import anthropic
from playwright.async_api import Page, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from ..book_utils import print_log
from .parameters import HEADLESS_BROWSER, USER_AGENTS, site_constants
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

# Keys in a site_constants entry that are NOT detail-page CSS selectors and so should
# never be proposed/validated as such: the site base URL and the 404-title sentinel.
_NON_SELECTOR_KEYS = frozenset({"BASE_URL", "404_PAGE_TITLE"})


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


def build_instruction_block(site: str, field_names: list[str]) -> str:
    """Build the *static* instruction block sent to Claude as the cacheable prefix.

    Contains only the site name, the field list, and the output contract - no per-page
    content - so it is byte-identical across every sample URL in a run and can be served
    from Anthropic's prompt cache on the 2nd+ request (the snapshot, which varies per page,
    is sent separately as the user message). Asks for a JSON object keyed by the *existing*
    field names (so Claude proposes values for known keys, not new keys), each value an
    object with a ``selector`` and a short ``rationale``. Pure string assembly.
    """
    fields_block = "\n".join(f"  - {name}" for name in field_names)
    return (
        f"You are helping maintain a web scraper's CSS-selector schema for the "
        f"'{site}' book detail page.\n\n"
        "You will be given a pruned HTML snapshot of one sample detail page. For each of "
        "the following fields, propose the most robust CSS selector that Playwright's "
        "`page.locator(selector)` could use to extract that field's value from the page. "
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
    URLs but not others. Pure formatting - all live data is precomputed in ``validation``.
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

    for field in proposals:
        current_value = current.get(field, "<field not currently present>")
        proposed_value = proposals[field]["selector"]
        rationale = proposals[field]["rationale"]
        field_validation = validation.get(field, {})
        per_url = field_validation.get("per_url", [])
        consistent = field_validation.get("consistent", True)

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
            if result.get("matched"):
                snippet = result.get("snippet", "")
                snippet_note = f' text="{snippet}"' if snippet else ""
                lines.append(f"      [MATCH] {result['url']} ({result.get('count', 0)} element(s)){snippet_note}")
            else:
                note = result.get("snippet", "")
                note_str = f" ({note})" if note else ""
                lines.append(f"      [ no  ] {result['url']} (0 elements){note_str}")

        if not consistent:
            inconsistent_fields.append(field)
            lines.append(
                "    !! INCONSISTENT across sample URLs - matched on some pages but not others. Review before applying."
            )

    lines.append("")
    lines.append("=" * 72)
    if inconsistent_fields:
        lines.append(
            f"WARNING: {len(inconsistent_fields)} field(s) validated inconsistently "
            f"across sample URLs: {', '.join(inconsistent_fields)}"
        )
    else:
        lines.append("All proposed selectors validated consistently across the sample URLs.")
    return "\n".join(lines)


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


async def validate_across_samples(
    proposals: dict[str, dict[str, Any]],
    samples: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Re-query every proposed selector against each sample page's live DOM.

    ``samples`` is a list of ``{"url": str, "page": <playwright Page>}``. Returns
    ``{field: {"per_url": [{"url", "matched", "count", "snippet"}...], "consistent": bool}}``.
    A field is *consistent* when every sample page agrees on whether it matched (all hit
    or all miss); a mixed result is flagged rather than silently resolved.
    """
    results: dict[str, dict[str, Any]] = {}
    for field, proposal in proposals.items():
        selector = proposal["selector"]
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
        consistent = len(matched_flags) <= 1
        results[field] = {"per_url": per_url, "consistent": consistent}
    return results


async def detect_schema(
    site: str,
    urls: list[str],
    model: Optional[str] = None,
    max_input_tokens: int = MAX_INPUT_TOKENS,
) -> tuple[str, str]:
    """Run the full detect -> validate -> report pipeline, returning ``(report, usage_line)``.

    One request is sent per sample URL (capped at ``MAX_SAMPLE_URLS``), sharing a cacheable
    instruction block; the per-page proposal sets are merged and validated against every sample
    page's live DOM. Raises ``SchemaDetectionError`` (never returns partial output) on: too many
    sample URLs, an unknown site, a missing ``ANTHROPIC_API_KEY``, an empty model ID, a prompt
    that will not fit the token budget, a page-load failure, or a malformed/truncated/refused AI
    response.
    """
    if site not in site_constants:
        raise SchemaDetectionError(f"Unknown site '{site}'. Known sites: {', '.join(sorted(site_constants))}.")
    if not urls:
        raise SchemaDetectionError("At least one sample --url is required.")
    if len(urls) > MAX_SAMPLE_URLS:
        raise SchemaDetectionError(
            f"Too many sample URLs ({len(urls)}); the per-run cap is {MAX_SAMPLE_URLS}. "
            "Reduce the number of --url arguments."
        )

    # Resolve the model (and reject an empty ID) before any network or API call.
    resolved_model = resolve_model(model)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SchemaDetectionError(
            "ANTHROPIC_API_KEY is not set. Export it (or add it to .env) before running detect-schema."
        )

    current = site_constants[site]
    fields = detail_selector_fields(current)
    if not fields:
        raise SchemaDetectionError(f"No detail-page selector fields defined for site '{site}'.")

    instruction_block = build_instruction_block(site, fields)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=HEADLESS_BROWSER)
        samples: list[dict[str, Any]] = []
        try:
            for url in urls:
                page = await browser.new_page()
                pruned_html = await fetch_pruned_html(url, page)
                samples.append({"url": url, "page": page, "pruned_html": pruned_html})

            # One request per sample page, each sharing the cacheable instruction block; collect
            # every response's proposals and usage. The merged proposals are then validated against
            # ALL sample pages' live DOM (a selector that matches one page's markup can be a false
            # positive - cross-URL validation is what catches that).
            per_url_proposals: list[dict[str, dict[str, Any]]] = []
            usages: list[Any] = []
            for sample in samples:
                raw_reply, usage = await asyncio.to_thread(
                    call_anthropic,
                    instruction_block,
                    build_snapshot_block(sample["pruned_html"]),
                    api_key,
                    resolved_model,
                    max_input_tokens,
                )
                per_url_proposals.append(validate_ai_response(raw_reply, fields))
                usages.append(usage)

            proposals = _merge_proposals(per_url_proposals, fields)
            validation = await validate_across_samples(proposals, samples)
        finally:
            await browser.close()

    print_log(f"Received {len(proposals)} selector proposal(s) for '{site}'.", "info")
    report = format_diff_report(site, urls, proposals, current, validation)
    usage_line = format_usage_line(resolved_model, usages, len(usages))
    return report, usage_line
