"""AI-assisted CSS-selector schema detection for the four book sites.

This module automates the manual "figure out the selectors for a site's detail page"
loop documented in ``docs/scraping/schema-detection.md``. Given a site name and one or
more sample detail-page URLs it:

1. loads each URL with Playwright (reusing ``scrape_details.route_handler``'s
   resource-blocking so only the document is fetched) and extracts a pruned HTML
   snapshot small enough to hand to an LLM (``prune_html``);
2. asks Claude, via the official ``anthropic`` SDK, to propose a CSS selector for each
   detail-page field already present in ``parameters.site_constants[site]``
   (``build_prompt`` / ``call_anthropic`` / ``validate_ai_response``);
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
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from ..book_utils import print_log
from .parameters import HEADLESS_BROWSER, USER_AGENTS, site_constants
from .scrape_details import route_handler

module_logger = logging.getLogger("schema_detection")

# Default model; overridable via ANTHROPIC_MODEL so a caller can pin/upgrade without a
# code change. The exact value is irrelevant to the test suite, which always mocks the
# anthropic client (no real API call is ever made from tests).
DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_MAX_TOKENS = 2048

# Upper bound on the pruned-HTML payload handed to the LLM. Kept well under the model's
# context window while leaving room for the prompt scaffolding and the response.
DEFAULT_HTML_BUDGET = 60000

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


def build_prompt(site: str, field_names: list[str], pruned_html: str) -> str:
    """Build the instruction prompt sent to Claude for one sample page.

    Asks for a JSON object keyed by the *existing* field names (so Claude proposes
    values for known keys, not new keys), each value an object with a ``selector`` and a
    short ``rationale``. Pure string assembly.
    """
    fields_block = "\n".join(f"  - {name}" for name in field_names)
    return (
        f"You are helping maintain a web scraper's CSS-selector schema for the "
        f"'{site}' book detail page.\n\n"
        "Below is a pruned HTML snapshot of one sample detail page. For each of the "
        "following fields, propose the most robust CSS selector that Playwright's "
        "`page.locator(selector)` could use to extract that field's value from this "
        "page. Prefer stable `id` and `data-*` attributes over fragile auto-generated "
        "class chains. If a field genuinely has no corresponding element on this page, "
        "use null for its selector.\n\n"
        "Fields (use exactly these keys, do not invent new ones):\n"
        f"{fields_block}\n\n"
        "Respond with ONLY a single JSON object (no prose, no markdown fences) mapping "
        "each field name to an object of the form "
        '{"selector": "<css or null>", "rationale": "<one short sentence>"}.\n\n'
        "Pruned HTML snapshot:\n"
        f"{pruned_html}\n"
    )


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


# --------------------------------------------------------------------------- #
# External-collaborator helpers (network / browser) - mock-tested.
# --------------------------------------------------------------------------- #
def _extract_text(message: Any) -> str:
    """Concatenate the text blocks of an anthropic ``Message`` response."""
    parts: list[str] = []
    for block in getattr(message, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    if not parts:
        raise SchemaDetectionError("AI response contained no text content.")
    return "\n".join(parts)


def call_anthropic(prompt: str, api_key: str, model: str = DEFAULT_MODEL) -> str:
    """Send ``prompt`` to Claude and return the raw text reply.

    Synchronous (the anthropic SDK's ``messages.create`` is blocking); the async
    orchestrator runs it via ``asyncio.to_thread``. Any SDK/transport error is wrapped as
    ``SchemaDetectionError`` so the caller fails loudly rather than leaking a raw
    exception.
    """
    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=model,
            max_tokens=DEFAULT_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:  # noqa: BLE001 - deliberately uniform failure surface
        raise SchemaDetectionError(f"Anthropic API request failed: {exc}") from exc
    return _extract_text(message)


async def fetch_pruned_html(url: str, page: Any, max_chars: int = DEFAULT_HTML_BUDGET) -> str:
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


async def detect_schema(site: str, urls: list[str], model: str = DEFAULT_MODEL) -> str:
    """Run the full detect -> validate -> report pipeline and return the report string.

    Raises ``SchemaDetectionError`` (never returns partial output) on: an unknown site, a
    missing ``ANTHROPIC_API_KEY``, a page-load failure, or a malformed AI response.
    """
    if site not in site_constants:
        raise SchemaDetectionError(f"Unknown site '{site}'. Known sites: {', '.join(sorted(site_constants))}.")
    if not urls:
        raise SchemaDetectionError("At least one sample --url is required.")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SchemaDetectionError(
            "ANTHROPIC_API_KEY is not set. Export it (or add it to .env) before running detect-schema."
        )

    current = site_constants[site]
    fields = detail_selector_fields(current)
    if not fields:
        raise SchemaDetectionError(f"No detail-page selector fields defined for site '{site}'.")

    resolved_model = os.environ.get("ANTHROPIC_MODEL", model)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=HEADLESS_BROWSER)
        samples: list[dict[str, Any]] = []
        try:
            for url in urls:
                page = await browser.new_page()
                pruned_html = await fetch_pruned_html(url, page)
                samples.append({"url": url, "page": page, "pruned_html": pruned_html})

            # Derive proposals from the first sample page, then validate them against ALL
            # sample pages' live DOM (a selector that matches one page's markup can be a
            # false positive - cross-URL validation is what catches that).
            prompt = build_prompt(site, fields, samples[0]["pruned_html"])
            raw_reply = await asyncio.to_thread(call_anthropic, prompt, api_key, resolved_model)
            proposals = validate_ai_response(raw_reply, fields)

            validation = await validate_across_samples(proposals, samples)
        finally:
            await browser.close()

    print_log(f"Received {len(proposals)} selector proposal(s) for '{site}'.", "info")
    return format_diff_report(site, urls, proposals, current, validation)
