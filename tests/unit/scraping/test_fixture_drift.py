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
    """Scenario: a selector that matched then stops matching is the DRIFT signal.

    Boundaries: only a ``>=1 -> 0`` transition is flagged; results are sorted.
    On failure: a real markup change would pass unnoticed and corrupt the corpus.
    """
    old = {"ISBN10": 1, "BOOK_TITLE": 1, "TAGS": 3}
    new = {"ISBN10": 0, "BOOK_TITLE": 1, "TAGS": 0}
    assert capture.compare_selector_matches(old, new) == ["ISBN10", "TAGS"]


def test_compare_selector_matches_ignores_non_drift_transitions() -> None:
    """Scenario: 0->0, staying-matched, a count drop that is still >=1, and a brand-new field.

    Boundaries: none of these is a ``>=1 -> 0`` transition, so none is flagged.
    On failure: noisy false positives would train maintainers to ignore the report.
    """
    old = {"zero": 0, "same": 2, "shrank": 5, "gone": 1}
    new = {"zero": 0, "same": 2, "shrank": 1, "gone": 0, "added": 4}
    assert capture.compare_selector_matches(old, new) == ["gone"]


def test_compare_selector_matches_empty() -> None:
    """Scenario: an empty selector map yields no drift (api/robots kinds)."""
    assert capture.compare_selector_matches({}, {}) == []


# --------------------------------------------------------------------------- #
# Pure helper: count_selector_matches (offline, via sanitize._select)
# --------------------------------------------------------------------------- #


def test_count_selector_matches_counts_offline() -> None:
    """Scenario: selector match counts come from the sanitizer's own parser, no browser.

    Boundaries: present selectors count their elements; an absent one counts 0.
    On failure: drift counting would need Chromium and break the no-browser baseline.
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
    """Scenario: detail drift set carries the real detail selectors incl. DESCRIPTION.

    Boundaries: BASE_URL, the 404 title string and the SEARCH_* family are excluded.
    On failure: a drift run would feed non-selectors to the counter or miss the description.
    """
    sel = capture.drift_selectors_for("amazon", "detail")
    assert "BOOK_TITLE" in sel and "DESCRIPTION" in sel
    assert "BASE_URL" not in sel and "404_PAGE_TITLE" not in sel
    assert not any(k.startswith("SEARCH_") for k in sel)


def test_drift_selectors_for_search_only_css_selectors() -> None:
    """Scenario: search drift set carries SEARCH_* CSS selectors, not the non-selector keys."""
    sel = capture.drift_selectors_for("amazon", "search")
    assert "SEARCH_BOOK_CARD" in sel and "SEARCH_TITLE" in sel
    assert "SEARCH_BASE_URL" not in sel and "SEARCH_MAXIMUM_PAGES" not in sel
    assert "BOOK_TITLE" not in sel


def test_drift_selectors_for_api_is_empty() -> None:
    """Scenario: a JSON/robots kind has no CSS selectors to drift-check."""
    assert capture.drift_selectors_for("leanpub", "api") == {}


# --------------------------------------------------------------------------- #
# Pure helper: detect_wall
# --------------------------------------------------------------------------- #


def test_detect_wall_flags_known_marker() -> None:
    """Scenario: a page carrying a CAPTCHA marker is detected as a wall."""
    assert capture.detect_wall(_WALL_HTML, "#productTitle") is not None


def test_detect_wall_flags_missing_title() -> None:
    """Scenario: a page missing the site's title selector is treated as a wall."""
    reason = capture.detect_wall("<html><body>nothing here</body></html>", "#productTitle")
    assert reason is not None and "title selector" in reason


def test_detect_wall_passes_real_page() -> None:
    """Scenario: a normal detail page (title present, no markers) is not a wall."""
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
    """Scenario: refresh of a drifted page flags DRIFT and leaves tests/fixtures untouched.

    Boundaries: no sibling golden (so no browser is needed); the recapture is monkeypatched
    to a copy with the ISBN-10 subtree gone; without ``--apply`` nothing is written.
    On failure: either drift would go unreported, or ``--refresh`` would silently write into
    the committed corpus (the FIX-10 "writes only to a temp dir" boundary).
    """
    fixture = _make_detail_fixture(temp_corpus, name="drifter", body=_DETAIL_HTML, hand_built=False)
    before = fixture.read_bytes()

    async def _fake_capture(url: str, site: str, kind: str) -> str:
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
    """Scenario: ``--apply`` refuses to overwrite a hand-built fixture with a walled page.

    Boundaries: the recapture returns a CAPTCHA wall (marker + no #productTitle); the
    hand-built fixture on disk must be left exactly as-is.
    On failure: a bot-wall page would launder into the corpus, destroying a hand-built
    fixture built precisely because the site walls the capture (FIX-6/FIX-10).
    """
    fixture = _make_detail_fixture(temp_corpus, name="walled", body=_DETAIL_HTML, hand_built=True)
    before = fixture.read_bytes()

    async def _fake_capture(url: str, site: str, kind: str) -> str:
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
    """Scenario: ``--apply`` refuses to overwrite a LIVE (non-hand-built) fixture with a wall.

    Boundaries: the fixture is not ``hand_built``; the recapture returns a CAPTCHA wall. The
    refusal must apply to every fixture, not only hand-built ones, so the live fixture on disk
    stays exactly as-is and the report says REFUSED.
    On failure: a bot-wall page would launder into the corpus over a good live fixture - the
    FIX-6/FIX-10 "a walled recapture never overwrites a good fixture" boundary, previously
    enforced only for hand-built fixtures.
    """
    fixture = _make_detail_fixture(temp_corpus, name="live_walled", body=_DETAIL_HTML, hand_built=False)
    before = fixture.read_bytes()

    async def _fake_capture(url: str, site: str, kind: str) -> str:
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
    """Scenario: ``--apply`` on a clean (non-wall) recapture rewrites the fixture and meta.

    Boundaries: the new bytes differ from the old; ``_write_fixture`` re-runs sanitize +
    assert_clean + budgets (FIX-10), and the rewritten meta's sha256 matches the new bytes.
    On failure: either the only in-tree write path is broken, or it would write bytes whose
    recorded sha drifts from the payload (the corpus integrity guard).
    """
    fixture = _make_detail_fixture(temp_corpus, name="freshen", body=_DETAIL_HTML, hand_built=True)
    before = fixture.read_bytes()

    async def _fake_capture(url: str, site: str, kind: str) -> str:
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
    """Scenario: a derived fixture is not re-captured; its selectors are checked in place.

    Boundaries: no capture is attempted (``no live source``); the current-fixture selector
    check still runs and surfaces a selector that matches nothing (the useful signal after a
    ``parameters.py`` edit). The recapture seam must never be called.
    On failure: refresh would try to fetch a page that does not exist for a derived edge case,
    or skip the useful post-``parameters.py``-edit selector check.
    """
    _make_detail_fixture(
        temp_corpus, name="derived_edge", body=_DETAIL_HTML, hand_built=True, derived_from="amazon/cpp_primer_5e"
    )

    async def _must_not_call(url: str, site: str, kind: str) -> str:
        raise AssertionError("derived fixtures must not trigger a capture")

    monkeypatch.setattr(capture, "_refresh_capture", _must_not_call)

    rc = await capture.run_refresh(argparse.Namespace(site="amazon", apply=False))
    out = capsys.readouterr().out

    assert rc == 0
    assert "no live source" in out
    # The minimal derived page omits TAGS/DESCRIPTION_ALT, so the in-place selector check
    # surfaces them as ZERO-MATCH (what a parameters.py edit breaking a selector looks like).
    assert "ZERO-MATCH" in out and "TAGS" in out
