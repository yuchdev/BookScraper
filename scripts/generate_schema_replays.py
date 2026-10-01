#!/usr/bin/env python3
"""Generate the hand-authored ``--replay`` fixtures for the detect-schema golden tests.

User-ratified policy (status.md, 2026-10-02): detect-schema automation is OFFLINE-ONLY - there
is no live Anthropic spend in CI, so the committed
``tests/fixtures/schema_detection/<site>.replay.json`` recordings are **hand-authored**:
plausible model JSON responses that follow the exact output contract ``validate_ai_response``
expects (each field -> ``{"selector": <css|null>, "rationale": "..."}``). This script does NOT
call Anthropic; it only computes the ``sha256(prompt)`` KEYS the runtime will look up, via the
real ``schema_detection`` prompt builders (so keys can never drift from the code), and writes a
replay file pairing each key with the authored response for that fixture.

The authored response proposes the site's CURRENT ``site_constants`` selectors, so the golden
report characterizes which of today's selectors still match each real captured fixture's DOM.

Run from the repo root:  ``uv run python scripts/generate_schema_replays.py``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from bookscraper.scraping.parameters import site_constants  # noqa: E402
from bookscraper.scraping.schema_detection import (  # noqa: E402
    build_instruction_block,
    build_snapshot_block,
    detail_selector_fields,
    prompt_hash,
    prune_html,
    search_selector_fields,
)

FIXTURES_HTML = REPO_ROOT / "tests" / "fixtures" / "html"
OUT_DIR = REPO_ROOT / "tests" / "fixtures" / "schema_detection"

PROVENANCE = "hand-authored; not a recorded live model response"


def _detail_fixtures(site: str) -> list[Path]:
    """Detail-page fixtures for a site: no ``_`` edge cases, no ``search*`` pages."""
    return sorted(
        p
        for p in (FIXTURES_HTML / site).glob("*.html")
        if not p.name.startswith("_") and not p.name.startswith("search")
    )


def _authored_response(current: dict, fields: list[str]) -> str:
    """A plausible model reply: propose each field's current selector with a short rationale."""
    obj = {
        field: {
            "selector": current.get(field),
            "rationale": f"current site_constants selector for {field}",
        }
        for field in fields
    }
    return json.dumps(obj, indent=2, sort_keys=True)


def _write_replay(out_path: Path, fixtures: list[Path], site: str, page_type: str) -> None:
    current = site_constants[site]
    fields = search_selector_fields(current) if page_type == "search" else detail_selector_fields(current)
    instruction_block = build_instruction_block(site, fields, page_type=page_type)
    response = _authored_response(current, fields)

    payload: dict[str, str] = {"_provenance": PROVENANCE}
    for fixture in fixtures:
        snapshot_block = build_snapshot_block(prune_html(fixture.read_text(encoding="utf-8")))
        payload[prompt_hash(instruction_block, snapshot_block)] = response

    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out_path.relative_to(REPO_ROOT)} ({len(fixtures)} prompt key(s), page_type={page_type})")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for site in ("amazon", "packtpub", "oreilly"):
        fixtures = _detail_fixtures(site)
        if len(fixtures) < 2:
            continue
        _write_replay(OUT_DIR / f"{site}.replay.json", fixtures, site, "detail")

    # The deferred subtask-06 success criterion: an amazon --page-type search golden over the
    # two committed search-results fixtures.
    search_fixtures = [
        FIXTURES_HTML / "amazon" / "search_python_p1.html",
        FIXTURES_HTML / "amazon" / "search_python_p2.html",
    ]
    _write_replay(OUT_DIR / "amazon_search.replay.json", search_fixtures, "amazon", "search")


if __name__ == "__main__":
    main()
