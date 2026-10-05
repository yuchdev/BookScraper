"""Unit tests for the fixture refresh/drift workflow (task 06.0, subtask 07).

Two layers are covered:

* the **pure** drift helpers in ``scripts/capture_fixtures.py`` -
  ``compare_selector_matches`` (the unit-testable ``>=1 -> 0`` comparison the spec calls
  out), ``count_selector_matches`` (offline counting via the sanitizer's own ``_select``),
  ``drift_selectors_for`` and ``detect_wall``;
* the **offline refresh flow** - ``run_refresh`` with the network fetch monkeypatched to a
  tweaked fixture copy, proving it flags DRIFT, writes nothing into ``tests/fixtures``
  without ``--apply``, refuses ``--apply`` on a walled page, and re-sanitizes on a clean
  apply (FIX-10). No real site and no real corpus are touched; the corpus root is pointed
  at a ``tmp_path`` throughout, and no browser is launched (counting is offline).

The script is a dev tool outside the package, so it is loaded by path with importlib (the
same pattern as ``tests/mock/scraping/test_capture_fixtures.py``).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "capture_fixtures.py"


def _load_capture_module() -> ModuleType:
    """Load scripts/capture_fixtures.py by path so it can be tested without being a package."""
    spec = importlib.util.spec_from_file_location("capture_fixtures", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


capture = _load_capture_module()


# A minimal but realistic Amazon detail page (mirrors the committed hand-built fixture's
# structure) that the real sanitize pipeline keeps intact.
_DETAIL_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>C++ Primer</title></head>
<body><div id="dp-container">
  <div id="titleblock"><span id="productTitle">C++ Primer</span>
    <div id="bylineInfo"><span class="author"><a class="a-link-normal" href="/e/author">Stanley B. Lippman</a></span></div>
  </div>
  <div id="detailBullets_feature_div">
    <div id="rpi-attribute-book_details-isbn10"><div class="rpi-attribute-value"><span>0321714113</span></div></div>
    <div id="rpi-attribute-book_details-isbn13"><div class="rpi-attribute-value"><span>978-0321714114</span></div></div>
    <div id="rpi-attribute-book_details-publication_date"><div class="rpi-attribute-value"><span>August 16, 2012</span></div></div>
  </div>
  <div id="bookDescription_feature_div"><div class="a-expander-content">Neutralized prose omitted from the public test corpus.</div></div>
</div></body></html>
"""

# Same page with the ISBN-10 subtree removed: ISBN10 drifts from 1 match to 0.
_DETAIL_HTML_NO_ISBN10 = _DETAIL_HTML.replace(
    '<div id="rpi-attribute-book_details-isbn10"><div class="rpi-attribute-value"><span>0321714113</span></div></div>\n    ',
    "",
)

# A CAPTCHA wall: a known marker and no #productTitle.
_WALL_HTML = (
    "<!DOCTYPE html><html><head><title>Robot Check</title></head>"
    "<body><h4>Enter the characters you see below</h4></body></html>"
)


# --------------------------------------------------------------------------- #
# Pure helper: compare_selector_matches
# --------------------------------------------------------------------------- #


def test_compare_selector_matches_flags_one_to_zero() -> None:
    """
    [Unit] fixture drift: verifies compare_selector_matches flags only >=1 -> 0 transitions.

    Scenario:
        Given old match counts where ISBN10 and TAGS were >=1, and new counts where they are 0
        When compare_selector_matches is called
        Then it returns exactly ["ISBN10", "TAGS"] in sorted order

    Boundaries:
        - Only the >=1 -> 0 transition is flagged; results are sorted
        - Pure dict comparison; no file I/O or browser

    On failure, first check:
        - A real markup change would pass unnoticed and corrupt the corpus
    """
    old = {"ISBN10": 1, "BOOK_TITLE": 1, "TAGS": 3}
    new = {"ISBN10": 0, "BOOK_TITLE": 1, "TAGS": 0}
    assert capture.compare_selector_matches(old, new) == ["ISBN10", "TAGS"]


def test_compare_selector_matches_ignores_non_drift_transitions() -> None:
    """
    [Unit] fixture drift: verifies compare_selector_matches ignores non-drift transitions.

    Scenario:
        Given old/new maps with 0->0, stays-matched, count-shrank-but->=1, and a new key
        When compare_selector_matches is called
        Then only "gone" (1->0) is returned

    Boundaries:
        - None of the other transitions is >=1 -> 0, so none must be flagged
        - Pure dict comparison; no file I/O

    On failure, first check:
        - Noisy false positives would train maintainers to ignore the drift report
    """
    old = {"zero": 0, "same": 2, "shrank": 5, "gone": 1}
    new = {"zero": 0, "same": 2, "shrank": 1, "gone": 0, "added": 4}
    assert capture.compare_selector_matches(old, new) == ["gone"]


def test_compare_selector_matches_empty() -> None:
    """
    [Unit] fixture drift: verifies compare_selector_matches returns [] for empty maps.

    Scenario:
        Given empty old and new selector match dicts (e.g. api/robots fixture kinds)
        When compare_selector_matches is called
        Then it returns an empty list

    Boundaries:
        - Edge case: empty input must not raise and must produce no false positives
        - Pure dict comparison; no I/O

    On failure, first check:
        - compare_selector_matches raised on empty input instead of returning []
    """
    assert capture.compare_selector_matches({}, {}) == []


# --------------------------------------------------------------------------- #
# Pure helper: count_selector_matches (offline, via sanitize._select)
# --------------------------------------------------------------------------- #


def test_count_selector_matches_counts_offline() -> None:
    """
    [Unit] fixture drift: verifies count_selector_matches counts elements without a browser.

    Scenario:
        Given an HTML string and a selector map including one matching and one stripped element
        When count_selector_matches is called
        Then present selectors count >0 and a removed one counts 0

    Boundaries:
        - Uses the sanitizer's own parser; no Chromium launched
        - Counts exact element matches, not text content

    On failure, first check:
        - Drift counting would require Chromium and break the no-browser unit baseline
    """
    counts = capture.count_selector_matches(
        _DETAIL_HTML,
        {"BOOK_TITLE": "#productTitle", "ISBN10": "#rpi-attribute-book_details-isbn10 .rpi-attribute-value span"},
    )
    assert counts == {"BOOK_TITLE": 1, "ISBN10": 1}
    assert (
        capture.count_selector_matches(
            _DETAIL_HTML_NO_ISBN10, {"ISBN10": "#rpi-attribute-book_details-isbn10 .rpi-attribute-value span"}
        )["ISBN10"]
        == 0
    )


# --------------------------------------------------------------------------- #
# Pure helper: drift_selectors_for
# --------------------------------------------------------------------------- #


def test_drift_selectors_for_detail_includes_description_not_search() -> None:
    """
    [Unit] fixture drift: verifies drift_selectors_for returns CSS detail selectors only.

    Scenario:
        Given the amazon detail-page kind
        When drift_selectors_for("amazon", "detail") is called
        Then BOOK_TITLE and DESCRIPTION are included; BASE_URL, 404_PAGE_TITLE, and SEARCH_* are excluded

    Boundaries:
        - Pure classification over parameters.site_constants["amazon"]; no network
        - Does not test that the returned selectors are valid CSS

    On failure, first check:
        - A non-selector constant (BASE_URL, 404_PAGE_TITLE) was included, feeding garbage to the counter
        - DESCRIPTION was dropped, hiding description-selector drift
    """
    sel = capture.drift_selectors_for("amazon", "detail")
    assert "BOOK_TITLE" in sel and "DESCRIPTION" in sel
    assert "BASE_URL" not in sel and "404_PAGE_TITLE" not in sel
    assert not any(k.startswith("SEARCH_") for k in sel)


def test_drift_selectors_for_search_only_css_selectors() -> None:
    """
    [Unit] fixture drift: verifies drift_selectors_for returns only CSS SEARCH_* selectors.

    Scenario:
        Given the amazon search-page kind
        When drift_selectors_for("amazon", "search") is called
        Then SEARCH_BOOK_CARD and SEARCH_TITLE are included; SEARCH_BASE_URL, SEARCH_MAXIMUM_PAGES, and BOOK_TITLE are excluded

    Boundaries:
        - Pure classification; detail-page selectors must not bleed into the search drift set
        - No network or file I/O

    On failure, first check:
        - A non-selector SEARCH_* key (URL, count, attribute name) was included, feeding garbage to the counter
    """
    sel = capture.drift_selectors_for("amazon", "search")
    assert "SEARCH_BOOK_CARD" in sel and "SEARCH_TITLE" in sel
    assert "SEARCH_BASE_URL" not in sel and "SEARCH_MAXIMUM_PAGES" not in sel
    assert "BOOK_TITLE" not in sel


def test_drift_selectors_for_api_is_empty() -> None:
    """
    [Unit] fixture drift: verifies drift_selectors_for returns {} for a JSON/api kind.

    Scenario:
        Given the leanpub api kind (JSON, no CSS)
        When drift_selectors_for("leanpub", "api") is called
        Then the result is an empty dict

    Boundaries:
        - Pure classification; no network or file I/O
        - api/robots kinds have no CSS to check, so an empty result must not raise

    On failure, first check:
        - drift_selectors_for returned non-empty for a JSON-only kind, feeding no-op CSS selectors
    """
    assert capture.drift_selectors_for("leanpub", "api") == {}


# --------------------------------------------------------------------------- #
# Pure helper: detect_wall
# --------------------------------------------------------------------------- #


def test_detect_wall_flags_known_marker() -> None:
    """
    [Unit] fixture drift: verifies detect_wall flags a page with a known CAPTCHA marker.

    Scenario:
        Given an HTML page with the "Robot Check" CAPTCHA marker and no #productTitle
        When detect_wall is called with the #productTitle title selector
        Then the return value is not None (wall detected)

    Boundaries:
        - Pure string/HTML check; no network
        - Relies on the "Enter the characters you see below" marker string

    On failure, first check:
        - detect_wall's marker list was changed and no longer includes the CAPTCHA string
    """
    assert capture.detect_wall(_WALL_HTML, "#productTitle") is not None


def test_detect_wall_flags_missing_title() -> None:
    """
    [Unit] fixture drift: verifies detect_wall flags a page whose title selector matches nothing.

    Scenario:
        Given a minimal HTML page with no element matching #productTitle
        When detect_wall is called with #productTitle
        Then the return value is not None and the reason mentions "title selector"

    Boundaries:
        - Covers the missing-title branch independently of the marker-string branch
        - Pure HTML check; no network

    On failure, first check:
        - detect_wall's title-selector branch was removed or the reason string changed
    """
    reason = capture.detect_wall("<html><body>nothing here</body></html>", "#productTitle")
    assert reason is not None and "title selector" in reason


def test_detect_wall_passes_real_page() -> None:
    """
    [Unit] fixture drift: verifies detect_wall returns None for a clean detail page.

    Scenario:
        Given the _DETAIL_HTML fixture (title present, no CAPTCHA markers)
        When detect_wall is called with #productTitle
        Then the return value is None (not a wall)

    Boundaries:
        - Happy path for detect_wall; the walled-page branches are covered by separate tests
        - Pure HTML check; no network

    On failure, first check:
        - detect_wall produced a false positive on a good fixture page
    """
    assert capture.detect_wall(_DETAIL_HTML, "#productTitle") is None


# --------------------------------------------------------------------------- #
# Offline refresh flow
# --------------------------------------------------------------------------- #


def _make_detail_fixture(
    root: Path, *, name: str, body: str, hand_built: bool, derived_from: Optional[str] = None
) -> Path:
    """Write a sanitized detail fixture + meta into a temp corpus; return the fixture path."""
    site_dir = root / "html" / "amazon"
    site_dir.mkdir(parents=True, exist_ok=True)
    sanitized = capture._sanitize_payload(body, "amazon", "detail")
    fixture_path = site_dir / f"{name}.html"
    fixture_path.write_text(sanitized, encoding="utf-8")
    meta: dict[str, Any] = {
        "url": f"https://www.amazon.com/dp/{name}",
        "site": "amazon",
        "kind": "detail",
        "sha256": hashlib.sha256(sanitized.encode("utf-8")).hexdigest(),
        "sanitizer_version": capture.sanitize.SANITIZER_VERSION,
        "logged_out": True,
    }
    if hand_built:
        meta["hand_built"] = True
    if derived_from:
        meta["derived_from"] = derived_from
    (site_dir / f"{name}.meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return fixture_path


@pytest.fixture
def temp_corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the capture script's corpus root at a throwaway dir; no 10 s rate sleep."""
    monkeypatch.setattr(capture, "FIXTURE_ROOT", tmp_path)
    monkeypatch.setattr(capture, "REFRESH_REQUEST_DELAY_SECONDS", 0)
    return tmp_path


async def test_refresh_flags_drift_and_writes_nothing(
    temp_corpus: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    [Unit] fixture drift: verifies --refresh reports drift without writing to tests/fixtures.

    Scenario:
        Given a fixture whose ISBN-10 element is present on disk but absent in the recapture
        When run_refresh is called without --apply
        Then SELECTOR DRIFT and ISBN10 appear in stdout, return code is 0, and the file is unchanged

    Boundaries:
        - Recapture is monkeypatched; no browser launched; no real network call
        - Covers the FIX-10 "writes only to a temp dir without --apply" boundary

    On failure, first check:
        - Either drift went unreported, or --refresh silently wrote into the committed corpus
    """
    fixture = _make_detail_fixture(temp_corpus, name="drifter", body=_DETAIL_HTML, hand_built=False)
    before = fixture.read_bytes()

    async def _fake_capture(url: str, site: str, kind: str) -> str:
        """Return a drifted page (ISBN-10 subtree removed) for any URL."""
        return _DETAIL_HTML_NO_ISBN10

    monkeypatch.setattr(capture, "_refresh_capture", _fake_capture)

    rc = await capture.run_refresh(argparse.Namespace(site="amazon", apply=False))
    out = capsys.readouterr().out

    assert rc == 0
    assert "SELECTOR DRIFT" in out and "ISBN10" in out
    assert "DRIFT found" in out
    assert fixture.read_bytes() == before  # nothing written without --apply


async def test_refresh_apply_refuses_wall(
    temp_corpus: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    [Unit] fixture drift: verifies --apply refuses to overwrite a hand-built fixture with a walled page.

    Scenario:
        Given a hand-built fixture on disk and a recapture that returns a CAPTCHA wall
        When run_refresh is called with --apply
        Then stdout says REFUSED, return code is 0, and the fixture bytes are unchanged

    Boundaries:
        - Covers the FIX-6/FIX-10 "wall must never launder into the corpus" boundary for hand-built
        - Recapture monkeypatched; no real network call

    On failure, first check:
        - A bot-wall page would destroy a hand-built fixture built because the site walls the capture
    """
    fixture = _make_detail_fixture(temp_corpus, name="walled", body=_DETAIL_HTML, hand_built=True)
    before = fixture.read_bytes()

    async def _fake_capture(url: str, site: str, kind: str) -> str:
        """Return a CAPTCHA wall for any URL."""
        return _WALL_HTML

    monkeypatch.setattr(capture, "_refresh_capture", _fake_capture)

    rc = await capture.run_refresh(argparse.Namespace(site="amazon", apply=True))
    out = capsys.readouterr().out

    assert rc == 0
    assert "REFUSED" in out and "walled" in out
    assert fixture.read_bytes() == before  # hand-built fixture untouched


async def test_refresh_apply_refuses_wall_for_live_fixture(
    temp_corpus: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    [Unit] fixture drift: verifies --apply refuses to overwrite a live fixture with a walled page.

    Scenario:
        Given a live (non-hand-built) fixture on disk and a recapture returning a CAPTCHA wall
        When run_refresh is called with --apply
        Then stdout says REFUSED with "walled page", return code is 0, and the fixture is unchanged

    Boundaries:
        - Extends the FIX-6/FIX-10 wall-refusal boundary to live fixtures, not only hand-built ones
        - Recapture monkeypatched; no real network call

    On failure, first check:
        - A bot-wall page would launder over a good live fixture (the FIX-6/FIX-10 extension)
    """
    fixture = _make_detail_fixture(temp_corpus, name="live_walled", body=_DETAIL_HTML, hand_built=False)
    before = fixture.read_bytes()

    async def _fake_capture(url: str, site: str, kind: str) -> str:
        """Return a CAPTCHA wall for any URL."""
        return _WALL_HTML

    monkeypatch.setattr(capture, "_refresh_capture", _fake_capture)

    rc = await capture.run_refresh(argparse.Namespace(site="amazon", apply=True))
    out = capsys.readouterr().out

    assert rc == 0
    assert "REFUSED" in out and "walled page" in out
    assert fixture.read_bytes() == before  # live fixture untouched despite --apply


async def test_refresh_apply_writes_clean_recapture(
    temp_corpus: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    [Unit] fixture drift: verifies --apply writes and re-checksums a clean recapture.

    Scenario:
        Given a fixture on disk and a recapture that returns a different, non-walled page
        When run_refresh is called with --apply
        Then stdout says "wrote", return code is 0, the file bytes change, and meta sha256 matches

    Boundaries:
        - Covers the only in-tree write path; _write_fixture re-runs sanitize + assert_clean + budgets (FIX-10)
        - Recapture monkeypatched; no real network call

    On failure, first check:
        - The write path is broken, or it writes bytes whose recorded sha drifts from the payload
    """
    fixture = _make_detail_fixture(temp_corpus, name="freshen", body=_DETAIL_HTML, hand_built=True)
    before = fixture.read_bytes()

    async def _fake_capture(url: str, site: str, kind: str) -> str:
        """Return a real non-wall page that differs from the fixture on disk."""
        return _DETAIL_HTML_NO_ISBN10  # a real, non-wall page that differs from the old one

    monkeypatch.setattr(capture, "_refresh_capture", _fake_capture)

    rc = await capture.run_refresh(argparse.Namespace(site="amazon", apply=True))
    out = capsys.readouterr().out

    assert rc == 0 and "wrote" in out
    assert fixture.read_bytes() != before  # the clean recapture was applied
    meta = json.loads((fixture.parent / "freshen.meta.json").read_text(encoding="utf-8"))
    assert meta["sha256"] == hashlib.sha256(fixture.read_bytes()).hexdigest()


async def test_refresh_derived_reports_no_live_source(
    temp_corpus: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    [Unit] fixture drift: verifies derived fixtures are not re-captured but their selectors are checked.

    Scenario:
        Given a derived fixture (derived_from set) and a recapture seam that must never be called
        When run_refresh is called without --apply
        Then stdout says "no live source", rc is 0, and ZERO-MATCH appears for absent selectors

    Boundaries:
        - No capture attempted for derived fixtures; the recapture seam raises if called
        - The in-place selector check still runs (the useful post-parameters.py-edit signal)

    On failure, first check:
        - refresh tried to fetch a page that does not exist for a derived edge-case fixture
        - Or the useful post-parameters.py-edit selector check was skipped
    """
    _make_detail_fixture(
        temp_corpus, name="derived_edge", body=_DETAIL_HTML, hand_built=True, derived_from="amazon/cpp_primer_5e"
    )

    async def _must_not_call(url: str, site: str, kind: str) -> str:
        """Raise if a derived fixture incorrectly triggers a live capture."""
        raise AssertionError("derived fixtures must not trigger a capture")

    monkeypatch.setattr(capture, "_refresh_capture", _must_not_call)

    rc = await capture.run_refresh(argparse.Namespace(site="amazon", apply=False))
    out = capsys.readouterr().out

    assert rc == 0
    assert "no live source" in out
    # The minimal derived page omits TAGS/DESCRIPTION_ALT, so the in-place selector check
    # surfaces them as ZERO-MATCH (what a parameters.py edit breaking a selector looks like).
    assert "ZERO-MATCH" in out and "TAGS" in out
