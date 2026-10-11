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
import tempfile
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
from bookscraper.scraping.browser import new_scrape_context  # noqa: E402
from bookscraper.scraping.parameters import (  # noqa: E402
    HEADLESS_BROWSER,
    NON_SELECTOR_SEARCH_KEYS,
    site_constants,
)
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
# attribute NAME) and so must never be fed to the selector-aware sanitizer. Aliased from
# the single source of truth in ``parameters`` so this script and ``schema_detection``
# share one classification and can never disagree.
_NON_SELECTOR_SEARCH_KEYS = NON_SELECTOR_SEARCH_KEYS


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
    context = await new_scrape_context(browser)
    try:
        await _assert_logged_out(context)
        page = await context.new_page()
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
        # No spoofed browser User-Agent header: the production Leanpub httpx path sends httpx's
        # own default UA too, so the capture matches what the scraper actually presents.
        with client.stream("GET", url) as response:
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


# --------------------------------------------------------------------------- #
# Refresh / drift workflow (subtask 07)
# --------------------------------------------------------------------------- #

# Politeness for the refresh re-capture: >= 10 s between requests to the same site
# (the production headed-capture policy, tests/fixtures/README.md). This is the only
# rate control on the refresh path; FIX-7 (logged out, no proxy/creds) is inherited
# from the capture code paths it reuses (_capture_html / _fetch_body).
REFRESH_REQUEST_DELAY_SECONDS = 10.0

# Known bot-wall / anti-bot markers seen across the four sites (lower-cased substrings):
# Amazon CAPTCHA ("Robot Check", /errors/validateCaptcha, "Enter the characters you see
# below"), Cloudflare interstitials ("Just a moment...", "Attention Required", the
# cf-chl challenge chunk), and PerimeterX. Used by --apply to refuse overwriting a
# hand-built fixture with a walled page (threat model FIX-6/FIX-10).
_WALL_MARKERS = (
    "validatecaptcha",
    "robot check",
    "enter the characters you see below",
    "type the characters you see in this image",
    "just a moment",
    "attention required",
    "cf-chl",
    "px-captcha",
    "/errors/validatecaptcha",
)


def drift_selectors_for(site: str, kind: str) -> dict[str, str]:
    """Map ``{field_key: css_selector}`` of the selectors to drift-check for ``kind``.

    For a ``detail`` snapshot this is every ``site_constants[site]`` detail selector the
    parser reads (title/authors/ISBN/date/tags AND the description), minus the
    non-selector keys (``BASE_URL``, the ``404_PAGE_TITLE`` title string, the ``*_API``
    endpoints) and the ``SEARCH_*`` family. For a ``search`` snapshot it is the
    ``SEARCH_*`` CSS selectors (the non-selector ``SEARCH_*`` values - base URL/API, page
    count, the ``data-asin`` attribute NAME - are excluded). ``api``/``robots`` kinds have
    no CSS selectors and yield an empty map.
    """
    entry = site_constants.get(site, {})
    result: dict[str, str] = {}
    if kind == "search":
        for key, value in entry.items():
            if not key.startswith("SEARCH_") or key in _NON_SELECTOR_SEARCH_KEYS:
                continue
            if isinstance(value, str) and value:
                result[key] = value
    elif kind == "detail":
        skip = {"BASE_URL", "404_PAGE_TITLE"}
        for key, value in entry.items():
            if key in skip or key.startswith("SEARCH_") or key.endswith("_API"):
                continue
            if isinstance(value, str) and value:
                result[key] = value
    return result


def count_selector_matches(html: str, selectors: dict[str, str]) -> dict[str, int]:
    """Count how many elements each selector matches in ``html`` (offline, no browser).

    Uses the sanitizer's own bounded ``_select`` - the same parser the capture pipeline
    already counts cards with (:func:`_trim_repeated_blocks`) - so drift counting stays
    offline, deterministic and Chromium-free: the fixtures are already-sanitized static
    DOM snapshots and the selectors are plain CSS ``_select`` fully supports.
    """
    root = sanitize._parse(html)
    return {field: len(sanitize._select(root, sel)) for field, sel in selectors.items()}


def compare_selector_matches(old_counts: dict[str, int], new_counts: dict[str, int]) -> list[str]:
    """Return the field names whose selector drifted from ``>=1`` matches to ``0`` (DRIFT).

    Pure helper (unit-tested in ``tests/unit/scraping/test_fixture_drift.py``): a selector
    that matched something in the old snapshot and nothing in the new one is exactly the
    markup drift this corpus exists to catch. A field that stays ``0``, stays matched, or is
    new to ``new_counts`` is not a drift. Result is sorted for a stable report.
    """
    drifted = [field for field, old in old_counts.items() if old >= 1 and new_counts.get(field, 0) == 0]
    return sorted(drifted)


def detect_wall(html: str, title_selector: Optional[str]) -> Optional[str]:
    """Return a reason string if ``html`` looks like a bot-wall/CAPTCHA, else ``None``.

    A wall is either a known marker substring (``_WALL_MARKERS``) or the site's title
    selector being absent from the DOM (a real detail page always carries it). ``--apply``
    uses this to refuse overwriting a hand-built fixture with a walled page (FIX-6/FIX-10).
    """
    lowered = html.lower()
    for marker in _WALL_MARKERS:
        if marker in lowered:
            return f"wall marker {marker!r} present"
    if title_selector:
        root = sanitize._parse(html)
        if not sanitize._select(root, title_selector):
            return f"title selector {title_selector!r} absent"
    return None


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


# --------------------------------------------------------------------------- #
# Refresh orchestration (subtask 07)
# --------------------------------------------------------------------------- #


class FixtureRef:
    """One committed fixture of a site, resolved from its ``.meta.json``.

    A plain class (not a ``@dataclass``) so the script still imports cleanly under the
    importlib-by-path loader the tests use, which does not register the module in
    ``sys.modules`` (dataclass annotation resolution needs that).
    """

    def __init__(
        self,
        *,
        site: str,
        kind: str,  # detail | search | api | robots (from the meta)
        name: str,
        fixture_path: Path,
        meta_path: Path,
        expected_path: Optional[Path],  # sibling golden, if any
        meta: dict[str, Any],
    ) -> None:
        self.site = site
        self.kind = kind
        self.name = name
        self.fixture_path = fixture_path
        self.meta_path = meta_path
        self.expected_path = expected_path
        self.meta = meta


def _iter_site_fixtures(site: str) -> list[FixtureRef]:
    """Resolve every committed fixture of ``site`` (html, json and robots subdirs)."""
    refs: list[FixtureRef] = []
    search_dirs = [
        FIXTURE_ROOT / "html" / site,
        FIXTURE_ROOT / "json" / site,
        FIXTURE_ROOT / "robots",
    ]
    for subdir in search_dirs:
        if not subdir.exists():
            continue
        for meta_path in sorted(subdir.rglob("*.meta.json")):
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("site") != site:
                continue
            base = meta_path.name[: -len(".meta.json")]
            ext = _KIND_TO_EXT.get(meta.get("kind", ""), "")
            fixture_path = meta_path.parent / f"{base}.{ext}"
            if not fixture_path.exists():
                continue
            expected = meta_path.parent / f"{base}.expected.json"
            refs.append(
                FixtureRef(
                    site=site,
                    kind=meta["kind"],
                    name=base,
                    fixture_path=fixture_path,
                    meta_path=meta_path,
                    expected_path=expected if expected.exists() else None,
                    meta=meta,
                )
            )
    return refs


async def _refresh_capture(url: str, site: str, kind: str) -> str:
    """Re-capture ``url`` through the capture code paths (FIX-7), rate-limited.

    A thin seam over ``_capture_html`` / ``_fetch_body`` so refresh reuses the exact
    logged-out, cookie-less, proxy-less fetch the one-off capture uses; tests monkeypatch
    this to return a tweaked fixture copy instead of hitting the network.
    """
    time.sleep(REFRESH_REQUEST_DELAY_SECONDS)
    if kind in ("detail", "search"):
        return await _capture_html(url, site)
    return _fetch_body(url)


def _normalize_scrape_result(result: Any) -> dict[str, Any]:
    """Single source of truth for golden-result normalization (imported by the harness).

    The subtask-04 golden tier (``tests/mock/scraping/html/test_detail_goldens.py``) imports
    this as ``normalize_result`` rather than keeping its own copy, so ``--refresh``'s
    golden-drift check here and that golden tier cannot diverge.

    Since subtask 03.0/02 the production scrapers always return a
    :class:`bookscraper.scraping.results.ScrapeResult`. A ``SUCCESS`` result is normalized to
    its book document minus the volatile ``scraped_at`` (so the book-document goldens stay
    byte-identical to the pre-typed-result ones); every other status becomes
    ``{"_scrape_status": STATUS}`` plus a ``_scrape_error`` when the result carries one, so a
    404 / timeout / parse error reads as a distinct, diffable golden rather than one opaque
    ``FAILED``. The legacy dict / ``(None, STATUS)`` / bare-``None`` shapes are still accepted
    for any pre-typed caller that has not migrated.
    """
    from bookscraper.scraping.results import ScrapeResult, ScrapeStatus

    if isinstance(result, ScrapeResult):
        if result.status is ScrapeStatus.SUCCESS:
            record = dict(result.book)
            record.pop("scraped_at", None)
            return record
        normalized: dict[str, Any] = {"_scrape_status": result.status.value}
        if result.error is not None:
            normalized["_scrape_error"] = result.error
        return normalized
    if result is None:
        return {"_scrape_status": "NONE"}
    if isinstance(result, tuple):
        return {"_scrape_status": result[1]}
    record = dict(result)
    record.pop("scraped_at", None)
    return record


async def _golden_result_for_snapshot(ref: FixtureRef, snapshot: str) -> dict[str, Any]:
    """Run the production parser over the new snapshot and return the normalized record.

    ``api`` (Leanpub JSON) runs ``get_leanpub_book_details`` through an offline
    ``httpx.MockTransport`` - no browser. ``detail`` runs ``scrape_book`` against a
    headless Chromium that is served the snapshot for every document request (every other
    request aborted), reusing the production navigation path. Raises on any failure so the
    caller can report "golden drift: could not evaluate".
    """
    url = ref.meta["url"]
    if ref.kind == "api":
        from bookscraper.scraping.scrape_details import get_leanpub_book_details

        transport = httpx.MockTransport(
            lambda _req: httpx.Response(
                200, content=snapshot.encode("utf-8"), headers={"content-type": "application/json"}
            )
        )
        real_async_client = httpx.AsyncClient

        def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            kwargs.setdefault("transport", transport)
            return real_async_client(*args, **kwargs)

        original = httpx.AsyncClient
        httpx.AsyncClient = factory  # type: ignore[misc,assignment]
        try:
            result = await get_leanpub_book_details(url)
        finally:
            httpx.AsyncClient = original  # type: ignore[misc]
        return _normalize_scrape_result(result)

    from bookscraper.scraping.scrape_details import scrape_book

    body = snapshot.encode("utf-8")

    async def _serve(route: Any) -> None:
        if route.request.resource_type == "document":
            await route.fulfill(status=200, body=body, headers={"content-type": "text/html; charset=utf-8"})
        else:
            await route.abort()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=HEADLESS_BROWSER)
        try:
            original_new_page = browser.new_page

            async def _wrapped_new_page(*args: Any, **kwargs: Any) -> Any:
                page = await original_new_page(*args, **kwargs)
                original_route = page.route
                await original_route("**/*", _serve)

                async def _wrapped_route(pattern: Any, handler: Any, **kw: Any) -> None:
                    await original_route(pattern, handler, **kw)
                    await original_route("**/*", _serve)

                page.route = _wrapped_route  # type: ignore[method-assign]
                return page

            browser.new_page = _wrapped_new_page  # type: ignore[method-assign]
            result = await scrape_book(url, browser, ref.site, storage_backend=None)
        finally:
            await browser.close()
    return _normalize_scrape_result(result)


def _golden_drift_lines(ref: FixtureRef, expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """Field-by-field golden diff lines, ignoring the golden's ``_known_issues`` key."""
    exp = {k: v for k, v in expected.items() if k != "_known_issues"}
    lines: list[str] = []
    for key in sorted(set(exp) | set(actual)):
        if key not in actual:
            lines.append(f"      {key}: in golden, MISSING from scrape")
        elif key not in exp:
            lines.append(f"      {key}: produced by scrape, ABSENT from golden")
        elif exp[key] != actual[key]:
            lines.append(f"      {key}: golden={exp[key]!r} != scrape={actual[key]!r}")
    return lines


async def refresh_fixture(ref: FixtureRef, tmp_dir: Path, apply: bool) -> tuple[list[str], bool]:
    """Refresh one fixture: report drift, write the new snapshot ONLY to ``tmp_dir``.

    Returns ``(report_lines, drift_found)``. Writes into ``tests/fixtures`` only when
    ``apply`` is set and the capture is not refused (hand-built + walled). Derived/hand-built
    fixtures with no live source are not re-captured; their selector-drift check runs against
    the CURRENT fixture (useful after a ``parameters.py`` edit).
    """
    lines = [f"  {ref.kind}/{ref.name}"]
    drift_found = False
    old_text = ref.fixture_path.read_text(encoding="utf-8")
    hand_built = bool(ref.meta.get("hand_built"))
    derived_from = ref.meta.get("derived_from")
    selectors = drift_selectors_for(ref.site, ref.kind)

    # Derived fixtures have no real page to re-capture from.
    if derived_from:
        lines.append(f"    hand-built (derived from {derived_from}): no live source; no capture attempted")
        if selectors:
            old_counts = count_selector_matches(old_text, selectors)
            zero = sorted(f for f, c in old_counts.items() if c == 0)
            if zero:
                drift_found = True
                lines.append(f"    ZERO-MATCH on current fixture (check after a parameters.py edit): {zero}")
            else:
                lines.append(f"    selectors all match the current fixture ({len(selectors)} checked)")
        return lines, drift_found

    # Live or base-hand-built: one capture attempt from meta.url.
    try:
        new_raw = await _refresh_capture(ref.meta["url"], ref.site, ref.kind)
    except Exception as exc:  # noqa: BLE001 - any fetch failure is reported, never raised
        lines.append(f"    capture FAILED ({type(exc).__name__}: {exc}); fixture left unchanged")
        return lines, drift_found

    new_sanitized = _sanitize_payload(new_raw, ref.site, ref.kind)
    tmp_path = tmp_dir / ref.fixture_path.name
    tmp_path.write_text(new_sanitized, encoding="utf-8")

    old_sha = ref.meta.get("sha256")
    new_sha = hashlib.sha256(new_sanitized.encode("utf-8")).hexdigest()
    lines.append(f"    sha256 {'CHANGED' if old_sha != new_sha else 'unchanged'} (temp: {tmp_path})")

    wall_reason = (
        detect_wall(new_sanitized, site_constants.get(ref.site, {}).get("BOOK_TITLE")) if ref.kind == "detail" else None
    )
    if wall_reason:
        lines.append(f"    WALL detected: {wall_reason}")

    if selectors:
        old_counts = count_selector_matches(old_text, selectors)
        new_counts = count_selector_matches(new_sanitized, selectors)
        drift = compare_selector_matches(old_counts, new_counts)
        if drift:
            drift_found = True
            lines.append(f"    SELECTOR DRIFT ({len(drift)}): " + ", ".join(f"{f} {old_counts[f]}->0" for f in drift))
        else:
            lines.append(f"    no selector drift ({len(selectors)} selectors checked)")

    if ref.expected_path is not None and ref.kind in ("detail", "api"):
        try:
            actual = await _golden_result_for_snapshot(ref, new_sanitized)
            expected = json.loads(ref.expected_path.read_text(encoding="utf-8"))
            gdiff = _golden_drift_lines(ref, expected, actual)
            if gdiff:
                drift_found = True
                lines.append("    GOLDEN DRIFT:")
                lines.extend(gdiff)
            else:
                lines.append("    no golden drift")
        except Exception as exc:  # noqa: BLE001 - golden drift is best-effort (needs a browser for html)
            lines.append(f"    golden drift: could not evaluate ({type(exc).__name__}: {exc})")

    if apply:
        if wall_reason:
            # Refuse for EVERY fixture (live or hand-built): a bot-wall/CAPTCHA recapture must
            # never overwrite a good fixture, even a live-sourced one (FIX-6/FIX-10).
            built = "hand-built " if hand_built else ""
            lines.append(f"    --apply REFUSED: would overwrite a {built}fixture with a walled page ({wall_reason})")
        else:
            meta = _build_meta(
                ref.meta["url"], ref.site, ref.kind, new_raw, new_sanitized, ref.meta.get("chromium_version", "unknown")
            )
            if ref.kind in ("detail", "search") and not HEADLESS_BROWSER:
                meta["headed"] = True
            # _write_fixture re-runs the sanitize budgets + assert_clean on the payload AND
            # the meta before writing (FIX-10): the only in-tree write path, never silent.
            written = _write_fixture(ref.site, ref.kind, ref.name, new_sanitized, meta, force=True)
            lines.append(f"    --apply wrote {written}")

    return lines, drift_found


async def run_refresh(args: argparse.Namespace) -> int:
    """Re-capture every fixture of ``--site`` into a temp dir and print a drift report."""
    refs = _iter_site_fixtures(args.site)
    if not refs:
        print(f"no fixtures found for site {args.site!r}", file=sys.stderr)
        return 1

    tmp_dir = Path(tempfile.mkdtemp(prefix=f"bookscraper_refresh_{args.site}_"))
    print(f"refresh: {len(refs)} fixture(s) for {args.site}; snapshots -> {tmp_dir}")
    if args.apply:
        print("--apply: the only in-tree write path; each write re-runs sanitize + assert_clean + budgets (FIX-10)")
    else:
        print("(report only; --refresh writes nothing into tests/fixtures without --apply)")

    any_drift = False
    for ref in refs:
        lines, drift = await refresh_fixture(ref, tmp_dir, args.apply)
        any_drift = any_drift or drift
        print("\n".join(lines))

    print(f"\nsummary: {'DRIFT found' if any_drift else 'no drift'}; temp snapshots in {tmp_dir}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture and sanitize a page into the test corpus.")
    parser.add_argument("--site", required=True, choices=["amazon", "packtpub", "leanpub", "oreilly"])
    parser.add_argument("--kind", choices=["detail", "search", "api", "robots"], help="Capture kind (one-off capture).")
    parser.add_argument("--url", help="The URL to capture (one-off capture).")
    parser.add_argument("--name", help="Fixture base name, no extension (one-off capture).")
    parser.add_argument(
        "--force", action="store_true", help="Overwrite an existing fixture (never bypasses assert_clean)."
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-capture every fixture of --site into a temp dir and report drift (no --kind/--url/--name).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="With --refresh: write the refreshed snapshots into tests/fixtures (the only in-tree write path).",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.refresh:
        return asyncio.run(run_refresh(args))
    if args.apply:
        print("--apply is only valid together with --refresh.", file=sys.stderr)
        return 2
    missing = [flag for flag, value in (("--kind", args.kind), ("--url", args.url), ("--name", args.name)) if not value]
    if missing:
        print(f"one-off capture requires {', '.join(missing)} (or pass --refresh).", file=sys.stderr)
        return 2
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
