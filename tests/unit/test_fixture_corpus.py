"""Corpus-integrity tests for tests/fixtures/ (task 06.0, subtask 02).

These guard the *shape and integrity* of the committed real-HTML/JSON test corpus,
independent of what any fixture contains: every fixture has provenance metadata, no
metadata or golden is orphaned, per-file and whole-corpus size budgets hold, and no
fixture was hand-edited after capture (the sha256 in each `.meta.json` still matches
its payload). The sha256 check is the compensating control for the `-diff` gitattribute
on `*.html` (threat model FIX-13): a byte that changed after capture cannot hide in a
suppressed diff, because the hash would drift.

No network, no Playwright - pure filesystem work against the committed bytes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

import pytest

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"

PER_FILE_BUDGET = 400 * 1024
CORPUS_BUDGET = 15 * 1024 * 1024

_META_SUFFIX = ".meta.json"
_EXPECTED_SUFFIX = ".expected.json"


def _all_files() -> list[Path]:
    """Every regular file under the corpus (payloads, meta, goldens, robots, README)."""
    return [p for p in FIXTURES_DIR.rglob("*") if p.is_file()]


def _is_meta(path: Path) -> bool:
    return path.name.endswith(_META_SUFFIX)


def _is_expected(path: Path) -> bool:
    return path.name.endswith(_EXPECTED_SUFFIX)


# The subtask-07 ``schema_detection/`` subtree holds DERIVED artifacts, not captured pages:
# hand-authored ``--replay`` recordings and ``.report.txt`` goldens (see tests/fixtures/README.md).
# They carry no ``.meta.json``/``sha256`` provenance - that integrity model is for captured-page
# bytes - so they are excluded from the captured-corpus payload checks below. The size budget
# (``_all_files``) still covers them.
_NON_CAPTURE_DIRS = frozenset({"schema_detection"})


def _is_captured_corpus(path: Path) -> bool:
    """False for files under a non-capture subtree (e.g. the subtask-07 schema_detection/ dir)."""
    return not any(part in _NON_CAPTURE_DIRS for part in path.relative_to(FIXTURES_DIR).parts)


def _payload_files() -> list[Path]:
    """Captured-page payloads: HTML/JSON/txt bytes under html|json|robots, not meta/golden.

    README.md, any dotfile, and derived (non-capture) subtrees are not captured fixtures.
    """
    payloads: list[Path] = []
    for path in _all_files():
        if _is_meta(path) or _is_expected(path):
            continue
        if path.name == "README.md" or path.name.startswith("."):
            continue
        if not _is_captured_corpus(path):
            continue
        payloads.append(path)
    return payloads


def _base_name(path: Path) -> str:
    """Strip the fixture's compound suffix to its base name.

    ``cpp_primer_5e.meta.json`` -> ``cpp_primer_5e``; ``x.expected.json`` -> ``x``;
    ``x.html`` -> ``x``.
    """
    if _is_meta(path):
        return path.name[: -len(_META_SUFFIX)]
    if _is_expected(path):
        return path.name[: -len(_EXPECTED_SUFFIX)]
    return path.stem


def _payload_for(sidecar: Path) -> Optional[Path]:
    """Find the payload a .meta.json / .expected.json belongs to, in the same dir."""
    base = _base_name(sidecar)
    for candidate in sidecar.parent.iterdir():
        if not candidate.is_file():
            continue
        if _is_meta(candidate) or _is_expected(candidate):
            continue
        if candidate.name == "README.md" or candidate.name.startswith("."):
            continue
        if _base_name(candidate) == base:
            return candidate
    return None


def test_every_fixture_has_meta_json() -> None:
    """Scenario: every captured/hand-built payload carries provenance metadata.

    Boundaries: a payload whose sibling ``<base>.meta.json`` is missing.
    On failure: a fixture with no audit trail (how/whether it was sanitized, captured
    logged-out) slips into the corpus.
    """
    missing = [
        str(p.relative_to(FIXTURES_DIR))
        for p in _payload_files()
        if not (p.parent / f"{_base_name(p)}{_META_SUFFIX}").is_file()
    ]
    assert not missing, f"fixtures missing a .meta.json: {missing}"


def test_no_orphan_meta_or_expected_files() -> None:
    """Scenario: no .meta.json or golden survives its payload being removed/renamed.

    Boundaries: both sidecar kinds are checked; goldens do not exist yet (subtask 04),
    so their absence must be tolerated while an orphaned one is still flagged.
    On failure: a stale metadata/golden points at a fixture that no longer exists.
    """
    sidecars = [p for p in _all_files() if _is_meta(p) or _is_expected(p)]
    orphans = [str(p.relative_to(FIXTURES_DIR)) for p in sidecars if _payload_for(p) is None]
    assert not orphans, f"orphaned metadata/golden files: {orphans}"


def test_fixture_file_size_budget() -> None:
    """Scenario: no single fixture file exceeds the 400 KB per-file budget.

    Boundaries: applies to every committed corpus file (payload, meta, golden).
    On failure: an over-large capture (hostile bloat or un-trimmed noise) is committed.
    """
    over = [
        (str(p.relative_to(FIXTURES_DIR)), p.stat().st_size)
        for p in _all_files()
        if p.name != "README.md" and p.stat().st_size > PER_FILE_BUDGET
    ]
    assert not over, f"files over the {PER_FILE_BUDGET}-byte budget: {over}"


def test_corpus_total_size_budget() -> None:
    """Scenario: the whole corpus stays under the 15 MB budget.

    Boundaries: sums every regular file under tests/fixtures/.
    On failure: the corpus grows without bound and bloats every clone/CI run.
    """
    total = sum(p.stat().st_size for p in _all_files())
    assert total <= CORPUS_BUDGET, f"corpus is {total} bytes, over the {CORPUS_BUDGET} budget"


def test_meta_sha256_matches_file() -> None:
    """Scenario: no fixture was hand-edited after capture (FIX-13 compensating control).

    Boundaries: covers every fixture - each payload must have a .meta.json whose
    ``sha256`` equals the sha256 of the payload bytes on disk.
    On failure: a byte changed after capture (a smuggled secret, a silent tweak) that a
    suppressed HTML diff would hide.
    """
    payloads = _payload_files()
    assert payloads, "corpus is empty - expected the minimum initial fixtures"

    mismatches: list[str] = []
    for payload in payloads:
        meta_path = payload.parent / f"{_base_name(payload)}{_META_SUFFIX}"
        assert meta_path.is_file(), f"no .meta.json for {payload.relative_to(FIXTURES_DIR)}"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        recorded = meta.get("sha256")
        actual = hashlib.sha256(payload.read_bytes()).hexdigest()
        if recorded != actual:
            mismatches.append(f"{payload.relative_to(FIXTURES_DIR)}: meta={recorded} actual={actual}")
    assert not mismatches, f"sha256 drift (fixture edited after capture?): {mismatches}"


@pytest.mark.parametrize(
    "site, kind, count",
    [
        ("amazon", "html", 3),
        ("packtpub", "html", 3),
        ("oreilly", "html", 3),
        ("leanpub", "json", 3),
    ],
)
def test_minimum_initial_corpus_present(site: str, kind: str, count: int) -> None:
    """Scenario: the minimum initial corpus (amazon/packtpub/oreilly x3, leanpub x3) exists.

    Boundaries: counts only payload files under the site's kind directory.
    On failure: the corpus is short of the minimum the success criterion requires.
    """
    site_dir = FIXTURES_DIR / kind / site
    found = sorted(
        p.name
        for p in (site_dir.glob("*") if site_dir.is_dir() else [])
        if p.is_file() and not _is_meta(p) and not _is_expected(p)
    )
    assert len(found) >= count, f"{site}: expected >= {count} {kind} fixtures, found {found}"
