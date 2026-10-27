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
    """Return True if ``path`` is a ``.meta.json`` sidecar file."""
    return path.name.endswith(_META_SUFFIX)


def _is_expected(path: Path) -> bool:
    """Return True if ``path`` is an ``.expected.json`` golden file."""
    return path.name.endswith(_EXPECTED_SUFFIX)


# The ``schema_detection/`` (subtask-07) and ``ai_extraction/`` (task 04.1/05) subtrees hold DERIVED
# artifacts, not captured pages: hand-authored ``--replay`` recordings (and schema_detection's
# ``.report.txt`` goldens) - see tests/fixtures/README.md. They carry no ``.meta.json``/``sha256``
# provenance - that integrity model is for captured-page bytes - so they are excluded from the
# captured-corpus payload checks below. The size budget (``_all_files``) still covers them.
_NON_CAPTURE_DIRS = frozenset({"schema_detection", "ai_extraction"})


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


def test_every_fixture_has_meta_json():
    """
    [Unit] fixture corpus: verifies every captured payload has a sibling .meta.json.

    Scenario:
        Given all payload files under the captured-corpus directories
        When each payload is checked for a sibling <base>.meta.json
        Then no payload is missing its provenance metadata file.

    Boundaries:
        - Non-capture subtrees (schema_detection/) are excluded
        - No network; pure filesystem read against committed bytes

    On failure, first check:
        - Whether a new fixture was added without running capture_fixtures.py (which writes the meta)
        - Whether a meta file was accidentally deleted or renamed
    """
    missing = [
        str(p.relative_to(FIXTURES_DIR))
        for p in _payload_files()
        if not (p.parent / f"{_base_name(p)}{_META_SUFFIX}").is_file()
    ]
    assert not missing, f"fixtures missing a .meta.json: {missing}"


def test_no_orphan_meta_or_expected_files():
    """
    [Unit] fixture corpus: verifies no .meta.json or .expected.json survives its payload being removed.

    Scenario:
        Given all .meta.json and .expected.json sidecars under the corpus
        When each sidecar's corresponding payload is looked up in the same directory
        Then no sidecar is missing its payload (i.e. no orphans exist).

    Boundaries:
        - Both .meta.json and .expected.json kinds are checked
        - An orphaned sidecar with no payload in the same directory is the failure condition

    On failure, first check:
        - Whether a payload was deleted or renamed without removing its sidecar files
        - Whether capture_fixtures.py was run leaving behind a stale meta for a removed fixture
    """
    sidecars = [p for p in _all_files() if _is_meta(p) or _is_expected(p)]
    orphans = [str(p.relative_to(FIXTURES_DIR)) for p in sidecars if _payload_for(p) is None]
    assert not orphans, f"orphaned metadata/golden files: {orphans}"


def test_fixture_file_size_budget():
    """
    [Unit] fixture corpus: verifies no single corpus file exceeds the 400 KB per-file budget.

    Scenario:
        Given every regular file under tests/fixtures/ (payloads, meta, goldens)
        When each file's size is compared against PER_FILE_BUDGET (400 KB)
        Then no file exceeds the budget.

    Boundaries:
        - Applies to all corpus files including README.md is excluded
        - PER_FILE_BUDGET = 400 * 1024 bytes

    On failure, first check:
        - Whether a newly captured fixture was trimmed/pruned before committing
        - Whether an over-large HTML page was captured without the 400 KB cap applied
    """
    over = [
        (str(p.relative_to(FIXTURES_DIR)), p.stat().st_size)
        for p in _all_files()
        if p.name != "README.md" and p.stat().st_size > PER_FILE_BUDGET
    ]
    assert not over, f"files over the {PER_FILE_BUDGET}-byte budget: {over}"


def test_corpus_total_size_budget():
    """
    [Unit] fixture corpus: verifies the total corpus size stays under the 15 MB budget.

    Scenario:
        Given the sum of all regular files under tests/fixtures/
        When the total is compared against CORPUS_BUDGET (15 MB)
        Then the total does not exceed the budget.

    Boundaries:
        - Sums every regular file including meta, goldens, and non-capture subtrees
        - CORPUS_BUDGET = 15 * 1024 * 1024 bytes

    On failure, first check:
        - Whether many large fixtures were added in a recent commit
        - Whether the schema_detection/ replay/golden files grew disproportionately
    """
    total = sum(p.stat().st_size for p in _all_files())
    assert total <= CORPUS_BUDGET, f"corpus is {total} bytes, over the {CORPUS_BUDGET} budget"


def test_meta_sha256_matches_file():
    """
    [Unit] fixture corpus: verifies no fixture was hand-edited after capture (FIX-13 compensating control).

    Scenario:
        Given every captured payload and its .meta.json
        When the sha256 in .meta.json is compared against sha256(payload bytes on disk)
        Then every hash matches, confirming the payload is byte-identical to what was captured.

    Boundaries:
        - Covers all captured-corpus payloads; schema_detection/ subtree is excluded
        - A single byte change after capture causes a mismatch

    On failure, first check:
        - Whether a fixture was edited by hand (the -diff gitattribute suppresses the diff)
        - Whether capture_fixtures.py was rerun without updating the .meta.json sha256
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
def test_minimum_initial_corpus_present(site: str, kind: str, count: int):
    """
    [Unit] fixture corpus: verifies the minimum initial corpus per site is present.

    Scenario:
        Given the fixtures/<kind>/<site>/ directory
        When payload files (not meta or goldens) are counted
        Then at least ``count`` payload files exist for the site.

    Boundaries:
        - Parametrised over amazon/packtpub/oreilly (html, 3 each) and leanpub (json, 3)
        - Counts only payload files, not .meta.json or .expected.json

    On failure, first check:
        - Whether a fixture was deleted without adding a replacement
        - Whether the fixture directory structure changed (html/json layout)
    """
    site_dir = FIXTURES_DIR / kind / site
    found = sorted(
        p.name
        for p in (site_dir.glob("*") if site_dir.is_dir() else [])
        if p.is_file() and not _is_meta(p) and not _is_expected(p)
    )
    assert len(found) >= count, f"{site}: expected >= {count} {kind} fixtures, found {found}"
