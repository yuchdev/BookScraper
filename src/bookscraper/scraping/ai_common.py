"""Shared AI-prompt infrastructure reused by every Claude-backed surface in ``scraping/``.

This module is the single hardened AI stack extracted from :mod:`bookscraper.scraping.schema_detection`
(task 04.0) so the schema-detection tool and the LLM extraction fallback (task 04.1) cannot grow two
copies of the same prompt-injection and cost defenses that silently drift apart (threat-model EXT-5).
It holds the pieces that are not specific to proposing CSS selectors:

* HTML pruning for an LLM-sized snapshot (:func:`prune_html`) and the pre-flight token budget /
  re-prune loop (:func:`fit_snapshot_to_budget`);
* the ``<page_html>`` fence and its un-spoofable escaping (:func:`build_snapshot_block`, AIS-1 / PI-2);
* control-character sanitizing of untrusted text (:func:`sanitize_untrusted`, re-exported from
  :mod:`bookscraper.scraping.safe_text`, AIS-13 / PI-3);
* model and API-key resolution (:func:`resolve_model`, :func:`resolve_api_key`);
* the token pre-flight, stop-reason checks, text extraction and body-free SDK-error summary
  (:func:`fit_snapshot_to_budget`, :func:`check_stop_reason`, :func:`extract_text`,
  :func:`sdk_error_summary`);
* the usage-line formatters (:func:`format_usage_line`, :func:`format_replay_usage_line`);
* the ``sha256(prompt)`` replay/record I/O (:func:`prompt_hash`, :func:`load_replay_file`,
  :func:`replay_lookup`, :func:`write_record_file`).

Every function that fails loudly raises :class:`AIRequestError` by default, but accepts an
``error_cls`` override so a caller can keep its own more specific exception type and messages:
:class:`bookscraper.scraping.schema_detection.SchemaDetectionError` is a subclass of
:class:`AIRequestError` and is injected by ``schema_detection`` so its 04.0 "fail loudly" contract is
unchanged.

Import direction (EXT-5 invariant, guarded by ``test_ai_common_imports_only_scraping_and_book_utils``):
this lives under ``scraping/`` and imports only from ``scraping/`` and ``book_utils`` - never from
``commands/`` or ``backends/``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Optional

import anthropic

from .safe_text import sanitize_untrusted

# ``sanitize_untrusted`` lives in the stdlib-only :mod:`bookscraper.scraping.safe_text` module so
# lightweight callers can reuse it without importing this heavy (anthropic) module; re-exported here
# as part of the shared AI stack so AI callers reach it through one import.
__all__ = ["sanitize_untrusted"]

# Default model; overridable via ANTHROPIC_MODEL (or a ``--model`` flag) so a caller can pin/upgrade
# without a code change. The exact value is irrelevant to the test suite, which always mocks the
# anthropic client (no real API call is ever made from tests). ``claude-sonnet-5`` runs ADAPTIVE
# THINKING by default and thinking tokens count toward ``max_tokens``; it also rejects sampling params
# (temperature/top_p/top_k) and ``budget_tokens`` with a 400, so none of those are sent.
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
# still does not fit, the request is abandoned (``error_cls``) rather than sent.
MAX_INPUT_TOKENS = 40_000
# The floor the re-prune loop will not shrink the snapshot below.
MIN_HTML_BUDGET = 10_000

# At most one retry on a transient SDK error. The anthropic client's own ``max_retries`` is set to
# this same value so there is exactly one retry layer, never stacked unbounded retries.
MAX_API_RETRIES = 1

# AIS-1: the pruned page snapshot is spliced into the prompt fenced in ``<page_html>...</page_html>``
# and the instruction block (built by the caller) declares everything inside that fence to be
# untrusted DATA whose own instructions must be ignored. A hostile page could try to CLOSE the fence
# early with a literal ``</page_html>`` (or open a decoy ``<page_html>``) and smuggle text into
# instruction context, so every literal fence tag in the snapshot - opening or closing, any case, any
# inner whitespace, and including an *attributed* form like ``</page_html foo="x">`` (PI-2) - has its
# angle brackets HTML-escaped before embedding. Same breakout class, and same fix, as ``sanitize.py``'s
# V-3 ``</script>`` escaping (CWE-116).

_FENCE_TAG_RE = re.compile(r"<\s*/?\s*page_html\b[^>]*>", re.IGNORECASE)


class AIRequestError(Exception):
    """Raised on any unrecoverable failure in the shared AI stack.

    The base type for every fail-loud error in this module (missing API key, empty model ID,
    page-load/prompt-budget failure, malformed replay file, truncated/refused/malformed response, a
    redacted SDK/transport failure). A caller may inject a more specific subclass via each function's
    ``error_cls`` parameter - :class:`bookscraper.scraping.schema_detection.SchemaDetectionError`
    subclasses this - so the 04.0 "fail loudly" contract and messages stay unchanged while the two AI
    surfaces share one defense.
    """


# --------------------------------------------------------------------------- #
# HTML pruning + snapshot fence (pure).
# --------------------------------------------------------------------------- #
def prune_html(html: str, max_chars: int = DEFAULT_HTML_BUDGET) -> str:
    """Strip an HTML document down to a compact, LLM-friendly snapshot.

    Removes comments and the entire contents of ``<script>``/``<style>``/``<svg>``/
    ``<noscript>`` elements (noise that carries no selector signal), collapses runs of
    whitespace, and truncates the result to ``max_chars`` characters. Pure string
    transformation - no parsing library, no I/O.

    :param html: The raw page HTML.
    :param max_chars: The hard character cap on the returned snapshot.
    :returns: The pruned, whitespace-collapsed, truncated snapshot.
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


def _neutralize_fence(pruned_html: str) -> str:
    """Escape any literal ``<page_html>`` / ``</page_html>`` tag in the snapshot (AIS-1).

    A page cannot be allowed to close (or decoy-open) the ``<page_html>`` fence and escape into
    instruction context, so every fence tag - opening or closing, any case, any inner whitespace -
    has its angle brackets HTML-escaped (``<`` -> ``&lt;``, ``>`` -> ``&gt;``) before embedding.
    Pure string transform; all other content is preserved verbatim.
    """
    return _FENCE_TAG_RE.sub(lambda m: m.group(0).replace("<", "&lt;").replace(">", "&gt;"), pruned_html)


def build_snapshot_block(pruned_html: str) -> str:
    """Build the per-page snapshot block (the user message), which varies per sample page
    and is therefore *not* part of a cacheable prefix.

    The pruned HTML is fenced in ``<page_html>...</page_html>`` (AIS-1) and any literal fence tag
    inside it is neutralized (:func:`_neutralize_fence`) so a hostile page cannot break out of the
    fence into instruction context. Pure string assembly.

    :param pruned_html: The pruned page snapshot (untrusted).
    :returns: The fenced snapshot block.
    """
    return f"Pruned HTML snapshot (untrusted page data, fenced below):\n<page_html>\n{_neutralize_fence(pruned_html)}\n</page_html>\n"


# --------------------------------------------------------------------------- #
# Model / key resolution.
# --------------------------------------------------------------------------- #
def resolve_model(cli_model: Optional[str], *, error_cls: type[Exception] = AIRequestError) -> str:
    """Resolve the model ID with precedence ``--model`` flag > ``ANTHROPIC_MODEL`` env > default.

    ``cli_model`` is the parsed ``--model`` value (``None`` when the flag was omitted). An empty
    or whitespace-only ID - whether from the flag or the env var - is rejected with ``error_cls``;
    any other value is passed through unchanged for the API to accept or reject.

    :param cli_model: The ``--model`` flag value, or ``None``.
    :param error_cls: The exception type to raise on an empty ID.
    :returns: The resolved model ID.
    """
    model = cli_model if cli_model is not None else os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)
    if not model.strip():
        raise error_cls("Model ID must not be empty. Set --model or ANTHROPIC_MODEL to a valid model ID.")
    return model


def resolve_api_key(*, error_cls: type[Exception] = AIRequestError) -> str:
    """Read ``ANTHROPIC_API_KEY`` from the environment, failing loudly when it is unset.

    The key is read only into the returned value (passed to the SDK ``api_key=`` kwarg by the
    caller) and never logged. Raises ``error_cls`` with the same message 04.0 used when the key is
    missing or empty.

    :param error_cls: The exception type to raise when the key is unset.
    :returns: The API key.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise error_cls("ANTHROPIC_API_KEY is not set. Export it (or add it to .env) before running detect-schema.")
    return api_key


# --------------------------------------------------------------------------- #
# SDK response handling + token pre-flight.
# --------------------------------------------------------------------------- #
def extract_text(message: anthropic.types.Message, *, error_cls: type[Exception] = AIRequestError) -> str:
    """Concatenate the ``text`` blocks of an anthropic ``Message`` response.

    Only ``type == "text"`` blocks are collected; ``thinking`` blocks (present on an
    adaptive-thinking model) are skipped so a thinking summary never leaks into the JSON parse.

    :param message: The anthropic ``Message`` response.
    :param error_cls: The exception type to raise when the response has no text content.
    :returns: The concatenated text blocks.
    """
    parts: list[str] = []
    for block in getattr(message, "content", []) or []:
        if getattr(block, "type", None) != "text":
            continue
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    if not parts:
        raise error_cls("AI response contained no text content.")
    return "\n".join(parts)


def sdk_error_summary(exc: Exception) -> str:
    """Summarize an SDK exception as its class name (plus HTTP status if present), never its body.

    AIS-8/AIS-16: the Anthropic error body can echo request content and the account's error
    detail; the API key is never in it but is never risked either. Mirrors ``rotate-cert``'s
    "status code, never response body" rule.

    :param exc: The SDK/transport exception.
    :returns: A body-free one-line summary.
    """
    status = getattr(exc, "status_code", None)
    summary = f"Anthropic API request failed: {type(exc).__name__}"
    if status is not None:
        summary = f"{summary} (HTTP {status})"
    return summary


def check_stop_reason(message: anthropic.types.Message, *, error_cls: type[Exception] = AIRequestError):
    """Raise ``error_cls`` for a truncated or refused response, so neither is parsed.

    ``max_tokens`` means the JSON answer was cut off (incomplete, must not be parsed); ``refusal``
    means the model declined - the ``stop_details.category`` is named when present. Any other stop
    reason (``end_turn`` etc.) returns normally.

    :param message: The anthropic ``Message`` response.
    :param error_cls: The exception type to raise on a truncated/refused response.
    """
    reason = getattr(message, "stop_reason", None)
    if reason == "max_tokens":
        raise error_cls(
            "AI response was truncated: it hit max_tokens before completing the JSON answer. "
            "The output is incomplete and will not be parsed."
        )
    if reason == "refusal":
        details = getattr(message, "stop_details", None)
        category = getattr(details, "category", None) if details else None
        suffix = f" (category: {category})" if category else ""
        raise error_cls(f"AI refused to produce a response{suffix}; not parsing it.")
    if reason == "model_context_window_exceeded":
        raise error_cls(
            "AI response was truncated: the request exceeded the model's context window. "
            "The output is incomplete and will not be parsed; lower --max-input-tokens."
        )


def fit_snapshot_to_budget(
    client: anthropic.Anthropic,
    model: str,
    instruction_block: str,
    snapshot: str,
    max_input_tokens: int,
    *,
    error_cls: type[Exception] = AIRequestError,
) -> str:
    """Return a snapshot small enough that the full prompt fits ``max_input_tokens``.

    Counts tokens with the SDK's ``count_tokens`` endpoint; while over budget the snapshot is
    halved toward the ``MIN_HTML_BUDGET`` character floor. If the prompt still exceeds the budget
    at the floor, raises ``error_cls`` **without sending** the request.

    :param client: The anthropic client whose ``count_tokens`` endpoint is used.
    :param model: The model ID to count against.
    :param instruction_block: The static (system) instruction block.
    :param snapshot: The per-page snapshot (user message) to fit.
    :param max_input_tokens: The hard per-request input-token budget.
    :param error_cls: The exception type to raise when the prompt will not fit.
    :returns: A snapshot that fits the budget.
    """
    current = snapshot
    while True:
        try:
            tokens = client.messages.count_tokens(
                model=model,
                system=[{"type": "text", "text": instruction_block}],
                messages=[{"role": "user", "content": current}],
            ).input_tokens
        except Exception as exc:  # noqa: X002 - deliberately uniform; body-free SDK failure surface (redacts to class name + HTTP status; never the response body)
            raise error_cls(sdk_error_summary(exc)) from exc
        if tokens <= max_input_tokens:
            return current
        if len(current) <= MIN_HTML_BUDGET:
            raise error_cls(
                f"Prompt still exceeds the {max_input_tokens}-token budget ({tokens} tokens) "
                f"even after pruning the HTML snapshot to the {MIN_HTML_BUDGET}-character floor. "
                "Not sending the request."
            )
        current = current[: max(MIN_HTML_BUDGET, len(current) // 2)]


# --------------------------------------------------------------------------- #
# Usage-line formatters (pure).
# --------------------------------------------------------------------------- #
class UsageAccumulator:
    """Collects per-response anthropic ``usage`` objects across one run's AI calls.

    The single shared usage sink for every Claude-backed surface in ``scraping/`` (the
    schema-detection tool keeps its own inline ``usages`` list, but the 04.1 extraction
    fallback and its 05 integration accumulate here): each live response's ``usage`` object
    is recorded with :meth:`record`, each offline replay with :meth:`record_replayed`, and
    the run's one-line summary is produced by :meth:`usage_line`. Appends happen from
    ``asyncio`` tasks that never ``await`` between the read and the append, so the GIL makes
    a lock unnecessary. It carries **no** API key and **no** prompt/page content - only token
    counts and a request tally (threat-model EXT-6).

    :ivar _usages: The per-response ``usage`` objects recorded so far.
    :ivar _replayed: The number of offline replay responses served.
    """

    def __init__(self):
        """Start an empty accumulator with no recorded usages and a zero replay tally."""
        self._usages: list[Any] = []
        self._replayed = 0

    def record(self, usage: Optional[Any]):
        """Record one live response's ``usage`` object (a ``None`` usage is ignored).

        :param usage: The anthropic ``Message.usage`` object, or ``None``.
        """
        if usage is not None:
            self._usages.append(usage)

    def record_replayed(self, count: int = 1):
        """Record that ``count`` responses were served from an offline replay file.

        :param count: The number of replayed responses to add to the tally.
        """
        self._replayed += count

    @property
    def request_count(self) -> int:
        """The number of live (non-replay) responses recorded so far."""
        return len(self._usages)

    @property
    def replayed(self) -> int:
        """The number of offline replay responses served so far."""
        return self._replayed

    def usage_line(self, model: str) -> str:
        """Format the one-line usage summary for this run (no prices, no key).

        A run that made only replay calls reports the offline shape (zero tokens, ``N``
        replayed); otherwise the live token totals are summed across every recorded response.

        :param model: The resolved model ID.
        :returns: The usage summary line.
        """
        if self._replayed and not self._usages:
            return format_replay_usage_line(model, self._replayed)
        return format_usage_line(model, self._usages, len(self._usages))

    def totals(self) -> dict[str, int]:
        """Return the summed token counts and request/replay tallies for this run.

        The machine-readable counterpart of :meth:`usage_line`, consumed by the run report
        (task 07.0/03). It carries **no** prices and **no** API key - only aggregate token
        counts and the live/replay request tallies (threat-model EXT-6).

        :returns: A dict with ``requests``, ``replayed``, ``input_tokens``,
            ``cache_read_input_tokens``, ``cache_creation_input_tokens`` and ``output_tokens``.
        """
        return {
            "requests": len(self._usages),
            "replayed": self._replayed,
            "input_tokens": sum(int(getattr(usage, "input_tokens", 0) or 0) for usage in self._usages),
            "cache_read_input_tokens": sum(
                int(getattr(usage, "cache_read_input_tokens", 0) or 0) for usage in self._usages
            ),
            "cache_creation_input_tokens": sum(
                int(getattr(usage, "cache_creation_input_tokens", 0) or 0) for usage in self._usages
            ),
            "output_tokens": sum(int(getattr(usage, "output_tokens", 0) or 0) for usage in self._usages),
        }


def format_usage_line(model: str, usages: list[Any], request_count: int) -> str:
    """Format the one-line run usage summary from each response's ``usage`` object.

    Sums input / cache-read / cache-creation / output tokens across every response and reports
    them with the model and request count. Carries **no** prices (they change, and a stale figure
    would mislead) and **no** API key. Pure formatting.

    :param model: The resolved model ID.
    :param usages: The per-response ``usage`` objects.
    :param request_count: The number of requests made.
    :returns: The usage summary line.
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


def format_replay_usage_line(model: str, replayed: int) -> str:
    """Format the usage line for an offline ``--replay`` run: zero tokens, ``N`` replayed.

    No Anthropic request is made in replay mode, so there is no real token usage to report;
    the line mirrors :func:`format_usage_line`'s shape (all token counts zero) and names how many
    recorded responses were served. Pure formatting; carries no key.

    :param model: The resolved model ID.
    :param replayed: The number of recorded responses served.
    :returns: The offline usage summary line.
    """
    return (
        f"Anthropic usage: model={model} requests=0 replayed={replayed} "
        f"input_tokens=0 cache_read_input_tokens=0 cache_creation_input_tokens=0 "
        f"output_tokens=0 (offline replay; no API call, no tokens spent)"
    )


# --------------------------------------------------------------------------- #
# Prompt hashing + replay/record I/O.
# --------------------------------------------------------------------------- #
def prompt_hash(instruction_block: str, snapshot_block: str) -> str:
    """Return the ``sha256`` key for a prompt, used to index the ``--replay``/``--record`` file.

    The key is the hex ``sha256`` of the exact prompt the live path would send - the static
    instruction block and the per-page snapshot block joined by a blank line. Both the runtime
    (replay lookup / record write) and the maintenance script that generates a replay file compute
    the key through this one function and :func:`build_snapshot_block` so the two can never drift.
    Pure: no I/O, no network.

    :param instruction_block: The static instruction block.
    :param snapshot_block: The per-page snapshot block.
    :returns: The hex ``sha256`` of the joined prompt.
    """
    prompt = f"{instruction_block}\n\n{snapshot_block}"
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def load_replay_file(path: str, *, error_cls: type[Exception] = AIRequestError) -> dict[str, str]:
    """Load a ``--replay`` file: a JSON object of ``{sha256(prompt): raw_model_response}``.

    Keys beginning with ``_`` (e.g. a ``_provenance`` note on a hand-authored file) are
    ignored, so a recording can carry documentation alongside its hash-keyed entries. Fails
    loudly on a missing/unreadable path or a payload that is not a JSON object.

    :param path: The replay file path.
    :param error_cls: The exception type to raise on a read/parse/shape failure.
    :returns: The hash-keyed response map (``_``-prefixed keys dropped).
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise error_cls(f"Cannot read --replay file '{path}': {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise error_cls(f"--replay file '{path}' is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise error_cls(f"--replay file '{path}' must be a JSON object keyed by prompt hash.")
    return {key: value for key, value in data.items() if not key.startswith("_")}


def replay_lookup(
    replay: dict[str, str],
    instruction_block: str,
    snapshot_block: str,
    *,
    error_cls: type[Exception] = AIRequestError,
) -> str:
    """Return the recorded response for a prompt, or fail loudly if it is not recorded (AIS-9).

    A missing entry is a hard error ("no recorded response") - never a silent fall-through to a
    live API call - so an offline run that drifts from its recordings fails rather than spending.

    :param replay: The hash-keyed response map.
    :param instruction_block: The static instruction block.
    :param snapshot_block: The per-page snapshot block.
    :param error_cls: The exception type to raise when the prompt is not recorded.
    :returns: The recorded response for the prompt.
    """
    key = prompt_hash(instruction_block, snapshot_block)
    if key not in replay:
        raise error_cls(
            f"No recorded response in the --replay file for prompt hash {key}. "
            "The prompt changed (site fields, page content, or page_type) or the recording is "
            "stale; refresh it with --record against a live run (for a committed test fixture, "
            "re-run scripts/generate_schema_replays.py instead)."
        )
    return replay[key]


def write_record_file(path: str, recorded: dict[str, str], *, error_cls: type[Exception] = AIRequestError):
    """Write a ``--record`` file: ``{sha256(prompt): raw_model_response}`` from a live run.

    For maintainers refreshing the committed replay recordings. Carries a ``_provenance`` note
    and the hash-keyed responses only - never the ``ANTHROPIC_API_KEY`` (it is never part of a
    prompt or a response) and never any live token figure. Writes solely to ``path``.

    :param path: The record file path.
    :param recorded: The hash-keyed response map to persist.
    :param error_cls: The exception type to raise on a write failure.
    """
    payload: dict[str, str] = {
        "_provenance": "recorded from a live detect-schema run; review before committing as a replay fixture",
    }
    payload.update(recorded)
    try:
        Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as exc:
        raise error_cls(f"Cannot write --record file '{path}': {exc}") from exc
