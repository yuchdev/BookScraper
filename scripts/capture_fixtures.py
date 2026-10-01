#!/usr/bin/env python3
"""Capture and sanitize a real page into the versioned test corpus (task 06.0).

This is a *dev tool*, not product code: it is outside the coverage scope, but every
sanitizer it calls lives in ``bookscraper.scraping.sanitize`` and is unit-tested to
100%. It is the single guarded path by which untrusted third-party HTML/JSON crosses
into git history, so it is deliberately fail-closed.

Usage::

    uv run python scripts/capture_fixtures.py --site amazon --kind detail \\
        --url https://www.amazon.com/dp/1098131029 --name python_crash_course_3e
    uv run python scripts/capture_fixtures.py --site leanpub --kind api \\
        --url "https://leanpub.com/api/v1/cache/books/<slug>.json?include=accepted_authors" \\
        --name <slug>

Security properties (threat model docs/security/2026-09-30-real-html-test-corpus.md):

* FIX-1  - ``detail``/``search`` run in a FRESH ``browser.new_context()`` with no
  ``storage_state`` / ``user_data_dir`` / persistent profile; cookies are asserted
  empty (cleared and re-checked) immediately before ``page.content()``, else abort.
* FIX-2  - ``assert_clean`` runs on the sanitized bytes AND the ``.meta.json``; any
  hit or sanitizer exception aborts with no file written; ``--force`` only relaxes the
  refuse-to-overwrite rule, never the clean gate.
* FIX-5  - signed-URL / session params are stripped by the sanitizer.
* FIX-7  - ``api``/``robots`` httpx fetches send no auth headers or cookies.
* FIX-11 - per-file 400 KB budget and a bounded body read guard against hostile input.
* FIX-14 - ``.meta.json`` records ``logged_out: true`` and ``sanitizer_version``.

Corpus policy (status.md, user-ratified 2026-09-30): this one-off capture does NOT gate
on robots.txt; it is still logged out, cookie-less and rate-limited.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any, Optional

import httpx
from playwright.async_api import async_playwright

# Import direction stays scraping-only: this script reuses the scraper's route handler
# and selectors, plus the pure sanitizers.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bookscraper.scraping import sanitize  # noqa: E402
from bookscraper.scraping.parameters import HEADLESS_BROWSER, USER_AGENTS, site_constants  # noqa: E402
from bookscraper.scraping.scrape_details import route_handler  # noqa: E402

# Repo root -> tests/fixtures/{html,json,robots}/<site>/
REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures"

# FIX-11: hard per-file size budget, whole-corpus budget, and body-read cap.
MAX_FILE_BYTES = 400 * 1024
MAX_CORPUS_BYTES = 15 * 1024 * 1024
MAX_BODY_BYTES = 8 * 1024 * 1024

# Politeness: a fixed sleep between network requests (the capture is not robots-gated,
# so this is the only rate control).
REQUEST_DELAY_SECONDS = 2.0

# Bounded wait for the site's title/anchor selector after domcontentloaded.
SELECTOR_TIMEOUT_MS = 15_000

# A real search-results page carries dozens of near-identical result cards; the parser's
# control flow is fully exercised by a handful, and the full set blows the 400 KB per-file
# budget (FIX-11). The search pipeline trims to the first N SEARCH_BOOK_CARD matches before
# sanitizing, keeping the page chrome (pagination/no-results markers live outside the cards)
# while dropping the redundant tail.
MAX_SEARCH_CARDS = 8

_KIND_TO_SUBDIR = {"detail": "html", "search": "html", "api": "json", "robots": "robots"}
_KIND_TO_EXT = {"detail": "html", "search": "html", "api": "json", "robots": "txt"}


class CaptureError(Exception):
    """A capture that must abort with no file written."""


_DESCRIPTION_KEYS = ("DESCRIPTION", "DESCRIPTION_ALT")


def keep_selectors_for(site: str) -> list[str]:
    """keep-TEXT selectors: factual metadata that must survive verbatim.

    Everything the site's ``site_constants`` detail entry extracts EXCEPT the
    description (and non-selector endpoint/URL keys). ``neutralize_prose`` protects these
    subtrees so title/authors/ISBN/date/tags keep their exact text.
    """
    entry = site_constants.get(site, {})
    skip_exact = {*_DESCRIPTION_KEYS, "BASE_URL", "404_PAGE_TITLE"}
    selectors: list[str] = []
    for key, value in entry.items():
        if key in skip_exact or key.startswith("SEARCH_") or key.endswith("_API"):
            continue
        if isinstance(value, str) and value:
            selectors.append(value)
    return selectors


def description_selectors_for(site: str) -> list[str]:
    """keep-STRUCTURE selectors: the description element must survive ``shrink`` (so its
    selector still matches) even though its prose text is neutralized to the placeholder.
    """
    entry = site_constants.get(site, {})
    return [entry[key] for key in _DESCRIPTION_KEYS if isinstance(entry.get(key), str) and entry[key]]


# SEARCH_* keys that are NOT CSS selectors (a base URL/API, a page-count int, or an
# attribute NAME) and so must never be fed to the selector-aware sanitizer.
_NON_SELECTOR_SEARCH_KEYS = frozenset(
    {"SEARCH_BASE_URL", "SEARCH_BASE_API", "SEARCH_MAXIMUM_PAGES", "SEARCH_ASIN_ATTRIBUTE"}
)


def search_selectors_for(site: str) -> list[str]:
    """keep-selectors for a SEARCH-results page (``--kind search``).

    The detail ``keep_selectors_for`` deliberately skips ``SEARCH_*`` keys, so feeding it a
    search page leaves ``shrink`` with no matches and it drops the whole page. A search
    fixture instead keeps the ``SEARCH_*`` CSS selectors the search parser reads - the book
    card, title, authors, publication date, detail link, next-page button and no-results
    marker - so every card survives verbatim and the selectors still resolve offline. The
    non-selector ``SEARCH_*`` values (base URL/API, page count, the ``data-asin`` attribute
    NAME) are skipped, since the bounded sanitizer selector parser is fail-closed on them.
    """
    entry = site_constants.get(site, {})
    selectors: list[str] = []
    for key, value in entry.items():
        if not key.startswith("SEARCH_") or key in _NON_SELECTOR_SEARCH_KEYS:
            continue
        if isinstance(value, str) and value:
            selectors.append(value)
    return selectors


async def _assert_logged_out(context: Any) -> None:
    """FIX-1: abort unless the context is cookie-less and has no stored origins."""
    cookies = await context.cookies()
    if cookies:
        await context.clear_cookies()
        cookies = await context.cookies()
    if cookies:
        raise CaptureError("Context is not cookie-less; refusing to capture (FIX-1).")
    state = await context.storage_state()
    if state.get("origins"):
        raise CaptureError("Context storage_state has origins; refusing to capture (FIX-1).")


async def capture_document(browser: Any, url: str, site: str) -> str:
    """Capture ``page.content()`` from a fresh, logged-out ephemeral context.

    A new ``browser.new_context()`` is created with NO ``storage_state`` /
    ``user_data_dir`` (FIX-1). Cookies are asserted empty right before the snapshot.
    """
    context = await browser.new_context()
    try:
        await _assert_logged_out(context)
        page = await context.new_page()
        await page.set_extra_http_headers({"User-Agent": USER_AGENTS[0]})
        await page.route("**/*", route_handler)
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        title_selector = site_constants.get(site, {}).get("BOOK_TITLE")
        if title_selector:
            try:
                await page.wait_for_selector(title_selector, timeout=SELECTOR_TIMEOUT_MS)
            except Exception:
                # Title selector may legitimately not match (bot wall / drift); the
                # captured DOM is still what production sees, so continue.
                pass
        await _assert_logged_out(context)
        content = await page.content()
        # FIX-11: cap the browser DOM snapshot before sanitizing, like the httpx body.
        if len(content.encode("utf-8")) > MAX_BODY_BYTES:
            raise CaptureError("page.content() exceeds the read cap (FIX-11).")
        return content
    finally:
        await context.close()


async def _capture_html(url: str, site: str) -> str:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=HEADLESS_BROWSER)
        try:
            return await capture_document(browser, url, site)
        finally:
            await browser.close()


def _fetch_body(url: str) -> str:
    """FIX-7: unauthenticated httpx GET with no cookies and a bounded body read."""
    with httpx.Client(follow_redirects=True, timeout=30.0, trust_env=False) as client:
        with client.stream("GET", url, headers={"User-Agent": USER_AGENTS[0]}) as response:
            response.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > MAX_BODY_BYTES:
                    raise CaptureError("Response body exceeds the read cap (FIX-11).")
                chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", errors="replace")


async def _chromium_version() -> str:
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                return browser.version
            finally:
                await browser.close()
    except Exception:
        return "unknown"


def _bookscraper_version() -> str:
    try:
        return metadata.version("bookscraper")
    except metadata.PackageNotFoundError:
        return "unknown"


def _trim_repeated_blocks(html: str, selector: str, limit: int) -> str:
    """Keep only the first ``limit`` elements matching ``selector``; drop the rest.

    A pre-sanitize trim step for ``--kind search`` (see :data:`MAX_SEARCH_CARDS`): it runs
    on the raw DOM so the card selector still matches verbatim markup, and drops whole
    later-card subtrees via the sanitizer's own bounded parser. Page chrome outside the
    cards (pagination/no-results markers) is untouched. A page with ``<= limit`` matches is
    returned unchanged.
    """
    root = sanitize._parse(html)
    matches = sanitize._select(root, selector)
    if len(matches) <= limit:
        return html
    drop = {id(el) for el in matches[limit:]}
    sanitize._filter_tree(root, lambda el: id(el) in drop)
    return sanitize._serialize(root)


_SUBRESOURCE_ELEMENTS = frozenset({"img", "source", "picture", "video", "audio", "track", "object", "embed", "link"})
_SUBRESOURCE_ATTRS = frozenset({"src", "srcset", "poster", "data"})


def _strip_subresources(html: str) -> str:
    """Remove every element/attribute that would trigger a network fetch when served.

    The offline golden harness (subtask 03) is default-deny: any request a served fixture
    makes to an unmapped URL (an ``<img>``, a stylesheet ``<link>``, a ``srcset``) is
    recorded and fails the test. A search-results snapshot the parser only reads for its
    card/title/author/date selectors has no need of those resources, so they are dropped,
    leaving a self-contained page that fetches nothing. Anchors (``<a href>``) are kept -
    they load nothing and the detail-link selector reads them.
    """
    root = sanitize._parse(html)
    sanitize._filter_tree(root, lambda el: sanitize._local_name(el.tag) in _SUBRESOURCE_ELEMENTS)
    for el in sanitize._iter_elements(root):
        kept = []
        for name, value in el.attrs:
            lowered = name.lower()
            if lowered in _SUBRESOURCE_ATTRS:
                continue
            if lowered == "style" and value is not None and "url(" in value.lower():
                continue
            kept.append([name, value])
        el.attrs = kept
    return sanitize._serialize(root)


def _sanitize_payload(raw: str, site: str, kind: str) -> str:
    """Route raw bytes through the pure sanitizers for its kind."""
    if kind in ("detail", "search"):
        if kind == "search":
            # A search page keeps the SEARCH_* card/title/author/date selectors; there is
            # no long-prose description element to preserve structurally. Trim the redundant
            # tail of result cards first (budget; the parser needs only a handful).
            card_selector = site_constants.get(site, {}).get("SEARCH_BOOK_CARD")
            if isinstance(card_selector, str) and card_selector:
                raw = _trim_repeated_blocks(raw, card_selector, MAX_SEARCH_CARDS)
            keep_text = search_selectors_for(site)
            keep_structure: list[str] = []
        else:
            keep_text = keep_selectors_for(site)
            keep_structure = description_selectors_for(site)
        # Neutralize prose (keep-text is protected verbatim; the description's prose
        # collapses to the placeholder but its elements are preserved), then shrink the
        # page to the size budget while keeping BOTH the factual elements and the
        # description element (keep-structure) so every selector still resolves.
        cleaned = sanitize.sanitize_html(raw, keep_text)
        shrunk = sanitize.shrink(cleaned, keep_text + keep_structure)
        # A search snapshot is served to the offline (default-deny) harness, so it must
        # fetch nothing: drop image/stylesheet subresources the selectors never read.
        return _strip_subresources(shrunk) if kind == "search" else shrunk
    if kind == "api":
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            raise CaptureError(f"API response is not valid JSON: {exc}") from exc
        return json.dumps(sanitize.sanitize_json(data), ensure_ascii=False, indent=2)
    return sanitize.sanitize_text(raw)


def _build_meta(url: str, site: str, kind: str, raw: str, sanitized: str, chromium_version: str) -> dict[str, Any]:
    return {
        "url": url,
        "site": site,
        "kind": kind,
        "captured_at": datetime.now(UTC).isoformat(),
        "chromium_version": chromium_version,
        "bookscraper_version": _bookscraper_version(),
        "sha256": hashlib.sha256(sanitized.encode("utf-8")).hexdigest(),
        "bytes_raw": len(raw.encode("utf-8")),
        "bytes_sanitized": len(sanitized.encode("utf-8")),
        "logged_out": True,
        "sanitizer_version": sanitize.SANITIZER_VERSION,
    }


def _corpus_size_bytes(exclude: set[Path]) -> int:
    """Total on-disk size of the committed fixture corpus, excluding given paths."""
    total = 0
    if FIXTURE_ROOT.exists():
        for path in FIXTURE_ROOT.rglob("*"):
            if path.is_file() and path not in exclude:
                total += path.stat().st_size
    return total


def _write_fixture(site: str, kind: str, name: str, sanitized: str, meta: dict[str, Any], force: bool) -> Path:
    subdir = FIXTURE_ROOT / _KIND_TO_SUBDIR[kind] / site
    fixture_path = subdir / f"{name}.{_KIND_TO_EXT[kind]}"
    meta_path = subdir / f"{name}.meta.json"
    if fixture_path.exists() and not force:
        raise CaptureError(f"{fixture_path} exists; pass --force to overwrite.")

    sanitized_bytes = sanitized.encode("utf-8")
    if len(sanitized_bytes) > MAX_FILE_BYTES:
        raise CaptureError(
            f"Sanitized fixture is {len(sanitized_bytes)} bytes, over the {MAX_FILE_BYTES} budget (FIX-11)."
        )

    meta_text = json.dumps(meta, indent=2, sort_keys=True)
    # FIX-11: enforce the whole-corpus budget at capture time. Exclude the files being
    # (over)written so a re-capture is measured against its replacement, not itself.
    existing = _corpus_size_bytes(exclude={fixture_path, meta_path})
    projected = existing + len(sanitized_bytes) + len((meta_text + "\n").encode("utf-8"))
    if projected > MAX_CORPUS_BYTES:
        raise CaptureError(f"Corpus would be {projected} bytes, over the {MAX_CORPUS_BYTES} budget (FIX-11).")
    # FIX-2: the clean gate runs on the payload AND the meta, and cannot be bypassed.
    sanitize.assert_clean(sanitized)
    sanitize.assert_clean(meta_text)

    subdir.mkdir(parents=True, exist_ok=True)
    fixture_path.write_text(sanitized, encoding="utf-8")
    meta_path.write_text(meta_text + "\n", encoding="utf-8")
    return fixture_path


async def run_capture(args: argparse.Namespace) -> Path:
    if args.kind in ("detail", "search"):
        time.sleep(REQUEST_DELAY_SECONDS)
        raw = await _capture_html(args.url, args.site)
        chromium_version = await _chromium_version()
    else:
        time.sleep(REQUEST_DELAY_SECONDS)
        raw = _fetch_body(args.url)
        chromium_version = "n/a"

    sanitized = _sanitize_payload(raw, args.site, args.kind)
    meta = _build_meta(args.url, args.site, args.kind, raw, sanitized, chromium_version)
    return _write_fixture(args.site, args.kind, args.name, sanitized, meta, args.force)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture and sanitize a page into the test corpus.")
    parser.add_argument("--site", required=True, choices=["amazon", "packtpub", "leanpub", "oreilly"])
    parser.add_argument("--kind", required=True, choices=["detail", "search", "api", "robots"])
    parser.add_argument("--url", required=True, help="The URL to capture.")
    parser.add_argument("--name", required=True, help="Fixture base name (no extension).")
    parser.add_argument(
        "--force", action="store_true", help="Overwrite an existing fixture (never bypasses assert_clean)."
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        path = asyncio.run(run_capture(args))
    except CaptureError as exc:
        print(f"capture aborted: {exc}", file=sys.stderr)
        return 1
    except sanitize.SanitizationError as exc:
        print(f"capture aborted (unclean): {exc}", file=sys.stderr)
        return 1
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
