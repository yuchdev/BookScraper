#!/usr/bin/env python3
"""Measure HTML-pruned vs Markdown snapshot size and ground-truth preservation (task 04.1/04).

A time-boxed *measurement*, not a feature. It compares the two prompt-snapshot formats the
LLM value-extraction fallback (:mod:`bookscraper.scraping.ai_extraction`) could feed to Claude,
on every non-edge-case **detail** fixture in ``tests/fixtures/``:

* the ``ai_common`` pruned HTML (:func:`bookscraper.scraping.ai_common.prune_html`) - the status quo;
* a **stdlib-only** Markdown rendering (:class:`_MarkdownConverter`, built on ``html.parser`` the
  same way :mod:`bookscraper.scraping.sanitize` is - no third-party dependency is added, and none
  may be added to ``pyproject.toml`` by this spike).

For each format and fixture it reports:

* character count and a token estimate. Tokens are a ``chars / 4`` **proxy by default** (printed as
  ``~N``); the real SDK ``count_tokens`` endpoint is used only with ``--count-tokens`` (which needs
  ``ANTHROPIC_API_KEY`` and makes a network call - off by default so the spike runs fully offline);
* whether each eligible field's ground-truth value (from the ``.expected.json`` golden) is still
  present in that format's **visible text**. This mirrors the subtask-03 evidence-check precondition:
  the AI fallback can only recover a field whose value both appears in the snapshot the model reads
  *and* survives as a verbatim quote in the page's visible text. The per-field match reuses the exact
  gates :mod:`bookscraper.scraping.ai_extraction` uses (ISBN digit-clean, ``normalize_date`` for the
  publication date, whitespace-normalized substring for authors/description).

The decision rule (status.md *Notes & decisions*): adopt Markdown for ``ai_extraction.py`` only if
token reduction is **>= 30% on the median fixture** AND **no** ground-truth value present in the HTML
baseline is lost by Markdown on any fixture. Otherwise the spike closes as "measured, not adopted".

``detect-schema`` is deliberately out of scope: it proposes CSS selectors and needs the ``id`` /
``class`` / attribute structure Markdown discards, so it stays on HTML whatever this spike finds.

This script lives under ``scripts/`` (out of the coverage scope, like the other maintenance scripts)
and makes **no** network or API call unless ``--count-tokens`` is passed.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

# The script sits in ``scripts/`` beside the package source tree; make ``src`` importable when run
# directly (``python scripts/measure_snapshot_formats.py``) without requiring an editable install.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bookscraper import isbn  # noqa: E402 - sys.path is adjusted just above for direct execution
from bookscraper.book_utils import normalize_date  # noqa: E402
from bookscraper.scraping.ai_common import prune_html  # noqa: E402
from bookscraper.scraping.text import html_to_text  # noqa: E402

#: The record fields the fallback may fill, as ``(golden_key, field_kind)``; mirrors
#: :data:`bookscraper.scraping.ai_extraction.ELIGIBLE_FIELDS` (``title`` is never eligible).
_ELIGIBLE_GOLDEN_FIELDS: tuple[tuple[str, str], ...] = (
    ("description", "description"),
    ("publication_date", "date"),
    ("isbn13", "isbn"),
    ("isbn10", "isbn"),
    ("authors", "authors"),
)

#: Characters-per-token proxy used when ``--count-tokens`` is not passed. A coarse, well-known
#: English-text approximation; the absolute numbers are proxies but the *ratio* between the two
#: formats (which drives the decision) is stable under a constant divisor.
_CHARS_PER_TOKEN = 4

#: Block-level tags that force a line break in the Markdown rendering.
_BLOCK_TAGS = frozenset(
    {"p", "div", "section", "article", "header", "footer", "ul", "ol", "table", "tr", "hr", "blockquote", "pre"}
)
#: Tags whole subtrees of which carry no reader-visible text and are dropped entirely.
_SKIP_TAGS = frozenset({"script", "style", "svg", "noscript", "head", "template", "iframe"})
#: Heading tags mapped to their Markdown ``#`` depth.
_HEADING_LEVELS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}


class _MarkdownConverter(HTMLParser):
    """Convert an HTML document to a compact Markdown-ish text rendering, stdlib only.

    Mirrors the ``html.parser`` approach of :mod:`bookscraper.scraping.sanitize`: a single streaming
    parse, no third-party converter. The output keeps the reader-visible text and light block
    structure (headings as ``#``, list items as ``-``, table cells pipe-separated, paragraphs and
    ``<br>`` as newlines) while discarding every tag, attribute, ``script`` / ``style`` / ``svg`` /
    ``noscript`` / ``head`` subtree, and comment - which is where the size win over pruned HTML comes
    from. It is intentionally lossy of structure (that is the point of the spike) but never of the
    visible text a field value would be quoted from.

    :ivar _parts: The accumulated output fragments, joined on close.
    :ivar _skip_depth: Nesting depth inside a skipped (non-visible) subtree; text is dropped while > 0.
    """

    def __init__(self) -> None:
        """Initialize an empty converter not currently inside a skipped subtree."""
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        """Open a tag: enter skip mode for non-visible subtrees, else emit the block/heading marker."""
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in _HEADING_LEVELS:
            self._parts.append("\n\n" + "#" * _HEADING_LEVELS[tag] + " ")
        elif tag == "li":
            self._parts.append("\n- ")
        elif tag in ("td", "th"):
            self._parts.append(" | ")
        elif tag == "br":
            self._parts.append("\n")
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        """Close a tag: leave skip mode or emit a trailing break for block/heading elements."""
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag in _HEADING_LEVELS or tag in _BLOCK_TAGS:
            self._parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        """Emit character data verbatim unless inside a skipped (non-visible) subtree."""
        if self._skip_depth:
            return
        self._parts.append(data)

    def to_markdown(self) -> str:
        """Return the collapsed Markdown rendering: runs of blank lines and spaces normalized."""
        text = "".join(self._parts)
        lines = [" ".join(line.split()) for line in text.splitlines()]
        out: list[str] = []
        blank = False
        for line in lines:
            if not line:
                blank = True
                continue
            if out and blank:
                out.append("")
            blank = False
            out.append(line)
        return "\n".join(out).strip()


def html_to_markdown(html: str) -> str:
    """Render ``html`` to a compact stdlib-only Markdown snapshot.

    :param html: The raw page HTML.
    :returns: The Markdown-ish rendering (empty string for empty input).
    """
    if not html:
        return ""
    converter = _MarkdownConverter()
    converter.feed(html)
    converter.close()
    return converter.to_markdown()


def _normalize_ws(text: str) -> str:
    """Collapse all whitespace runs to single spaces and strip the ends."""
    return " ".join(text.split())


def _date_present(golden_iso: str, text: str) -> bool:
    """Report whether a date normalizing to ``golden_iso`` appears in ``text``.

    Slides a 1-to-4-token window over the visible text and runs
    :func:`bookscraper.scraping.book_utils.normalize_date` on each - the same normalizer the subtask-03
    date gate uses - so a human rendering such as ``September 24, 2022`` is matched to the golden ISO
    without depending on a single global parse picking the right date first.

    :param golden_iso: The golden publication date, ISO form.
    :param text: The format's whitespace-normalized visible text.
    :returns: ``True`` if any window normalizes to ``golden_iso``.
    """
    tokens = text.split()
    for size in (4, 3, 2, 1):
        for i in range(len(tokens) - size + 1):
            iso, _ = normalize_date(" ".join(tokens[i : i + size]))
            if iso == golden_iso:
                return True
    return False


def _value_present(kind: str, golden_value: object, visible_text: str) -> bool:
    """Report whether one eligible golden field value is recoverable from a format's visible text.

    Applies the same per-field gate :mod:`bookscraper.scraping.ai_extraction` uses:
    ISBNs are matched after :func:`bookscraper.isbn.clean` (digit-only), the publication date through
    :func:`_date_present`, and authors/description as whitespace-normalized substrings.

    :param kind: The field kind (``"isbn"`` / ``"date"`` / ``"authors"`` / ``"description"``).
    :param golden_value: The golden value (may be ``None`` / a list).
    :param visible_text: The format's whitespace-normalized visible text.
    :returns: ``True`` if the value is present/recoverable in that text.
    """
    if golden_value is None:
        return True  # Nothing to preserve; not counted as a ground-truth value below.
    if kind == "isbn":
        cleaned = isbn.clean(str(golden_value))
        return bool(cleaned) and cleaned in isbn.clean(visible_text)
    if kind == "date":
        return _date_present(str(golden_value), visible_text)
    if kind == "authors":
        names = [n for n in (golden_value or []) if n]
        return bool(names) and all(_normalize_ws(str(n)) in visible_text for n in names)
    # description
    normalized = _normalize_ws(str(golden_value))
    return bool(normalized) and normalized in visible_text


def _count_tokens_proxy(text: str) -> int:
    """Return the ``chars / 4`` token proxy for ``text``."""
    return (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN


def _count_tokens_api(client: object, model: str, text: str) -> int:
    """Return the SDK ``count_tokens`` count for ``text`` as a single user message (``--count-tokens``)."""
    return client.messages.count_tokens(  # type: ignore[attr-defined]
        model=model, messages=[{"role": "user", "content": text}]
    ).input_tokens


def _discover_detail_fixtures(fixtures_root: Path) -> list[Path]:
    """Return every non-edge-case ``kind == "detail"`` HTML fixture path, sorted by site then name.

    Edge-case fixtures (basename starting with ``_``) and search fixtures are excluded. The Leanpub
    detail path is a JSON API, not HTML, so it carries no HTML snapshot and is out of this spike.

    :param fixtures_root: The ``tests/fixtures`` directory.
    :returns: The HTML detail fixture paths.
    """
    found: list[Path] = []
    for meta_path in sorted(fixtures_root.glob("html/*/*.meta.json")):
        html_path = meta_path.with_suffix("").with_suffix(".html")
        if html_path.name.startswith("_") or not html_path.is_file():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("kind") != "detail":
            continue
        if not html_path.with_suffix("").with_suffix(".expected.json").is_file():
            continue
        found.append(html_path)
    return found


class _FormatResult:
    """Per-fixture, per-format measurement: size and which ground-truth values were preserved.

    :ivar chars: Snapshot character count.
    :ivar tokens: Snapshot token count (proxy or SDK).
    :ivar preserved: Ordered ``(golden_key, present)`` pairs for every eligible field with a non-null
        golden value (fields whose golden value is ``None`` are omitted - there is nothing to preserve).
    """

    __slots__ = ("chars", "tokens", "preserved")

    def __init__(self, chars: int, tokens: int, preserved: list[tuple[str, bool]]) -> None:
        self.chars = chars
        self.tokens = tokens
        self.preserved = preserved

    @property
    def all_preserved(self) -> bool:
        """``True`` when every present-in-golden eligible value survived in this format."""
        return all(present for _, present in self.preserved)


def _measure_fixture(html_path: Path, token_counter) -> tuple[str, str, _FormatResult, _FormatResult]:
    """Measure one fixture; return ``(site, name, html_result, markdown_result)``.

    :param html_path: The fixture ``.html`` path.
    :param token_counter: A callable ``str -> int`` for token counting (proxy or SDK).
    :returns: The site, fixture stem, and the two per-format results.
    """
    site = html_path.parent.name
    name = html_path.stem
    raw = html_path.read_text(encoding="utf-8")
    golden = json.loads(html_path.with_suffix("").with_suffix(".expected.json").read_text(encoding="utf-8"))

    html_snapshot = prune_html(raw)
    md_snapshot = html_to_markdown(raw)

    # The evidence precondition is evaluated on each format's *visible text* (subtask 03's check runs
    # against page text, never the Markdown): for the HTML snapshot that is html_to_text of the
    # pruned HTML; the Markdown snapshot is already text.
    html_text = _normalize_ws(html_to_text(html_snapshot))
    md_text = _normalize_ws(md_snapshot)

    def preserved_for(visible_text: str) -> list[tuple[str, bool]]:
        rows: list[tuple[str, bool]] = []
        for golden_key, kind in _ELIGIBLE_GOLDEN_FIELDS:
            value = golden.get(golden_key)
            if value is None:
                continue
            rows.append((golden_key, _value_present(kind, value, visible_text)))
        return rows

    html_result = _FormatResult(len(html_snapshot), token_counter(html_snapshot), preserved_for(html_text))
    md_result = _FormatResult(len(md_snapshot), token_counter(md_snapshot), preserved_for(md_text))
    return site, name, html_result, md_result


def main(argv: Optional[list[str]] = None) -> int:
    """Run the measurement and print the table and decision.

    :param argv: Command-line arguments (defaults to ``sys.argv[1:]``).
    :returns: Process exit code (``0`` on a clean measurement, ``2`` when no fixtures were found).
    """
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--fixtures-root",
        type=Path,
        default=_REPO_ROOT / "tests" / "fixtures",
        help="Path to tests/fixtures (default: repo tests/fixtures).",
    )
    parser.add_argument(
        "--count-tokens",
        action="store_true",
        help="Count real tokens via the Anthropic SDK (needs ANTHROPIC_API_KEY, makes a network call). "
        "Off by default: a chars/4 proxy is used and labelled '~'.",
    )
    parser.add_argument("--model", default=None, help="Model ID for --count-tokens (default: ai_common default).")
    args = parser.parse_args(argv)

    if args.count_tokens:
        import anthropic

        from bookscraper.scraping import ai_common

        model = ai_common.resolve_model(args.model)
        client = anthropic.Anthropic(api_key=ai_common.resolve_api_key())

        def token_counter(text: str) -> int:
            return _count_tokens_api(client, model, text)

        token_label = ""
    else:
        token_counter = _count_tokens_proxy
        token_label = "~"

    fixtures = _discover_detail_fixtures(args.fixtures_root)
    if not fixtures:
        print(f"No non-edge-case detail fixtures found under {args.fixtures_root}", file=sys.stderr)
        return 2

    header = (
        f"{'site':<9} {'fixture':<40} {'html_ch':>8} {'md_ch':>8} "
        f"{'html_tok':>9} {'md_tok':>9} {'reduct%':>8} {'preserved':<24}"
    )
    print(f"Token column: {'real SDK count_tokens' if args.count_tokens else 'chars/4 PROXY (~)'}")
    print(header)
    print("-" * len(header))

    reductions: list[float] = []
    any_value_lost = False
    per_site: dict[str, list[float]] = {}

    for html_path in fixtures:
        site, name, html_r, md_r = _measure_fixture(html_path, token_counter)
        reduction = 100.0 * (html_r.tokens - md_r.tokens) / html_r.tokens if html_r.tokens else 0.0
        reductions.append(reduction)
        per_site.setdefault(site, []).append(reduction)

        # A value is "lost" only if the HTML baseline preserved it but Markdown did not.
        html_map = dict(html_r.preserved)
        lost = [k for k, present in md_r.preserved if html_map.get(k) and not present]
        if lost:
            any_value_lost = True
        preserved_cells = []
        for key, present in md_r.preserved:
            mark = "ok" if present else ("LOST" if html_map.get(key) else "n/a")
            preserved_cells.append(f"{key}:{mark}")
        preserved_summary = ",".join(preserved_cells) or "(none)"

        print(
            f"{site:<9} {name:<40} {html_r.chars:>8} {md_r.chars:>8} "
            f"{token_label}{html_r.tokens:>8} {token_label}{md_r.tokens:>8} "
            f"{reduction:>7.1f}% {preserved_summary:<24}"
        )

    median_reduction = statistics.median(reductions)
    print("-" * len(header))
    for site in sorted(per_site):
        vals = per_site[site]
        print(f"{site}: median token reduction {statistics.median(vals):.1f}% over {len(vals)} fixture(s)")
    print(f"\nOverall median token reduction: {median_reduction:.1f}% over {len(reductions)} fixtures")
    print(f"Any ground-truth value lost by Markdown (preserved in HTML, not in Markdown): {any_value_lost}")

    meets_reduction = median_reduction >= 30.0
    adopt = meets_reduction and not any_value_lost
    print(
        f"\nDecision rule: median token reduction >= 30% ({meets_reduction}) "
        f"AND no ground-truth value lost ({not any_value_lost})"
    )
    print(f"=> {'ADOPT Markdown' if adopt else 'MEASURED, NOT ADOPTED'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
