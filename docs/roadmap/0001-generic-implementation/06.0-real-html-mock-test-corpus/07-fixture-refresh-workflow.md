# 07 - Fixture refresh & drift workflow

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/06.0-real-html-mock-test-corpus/README.md)
**Status:** ⬜ Not started
**Role:** Docs Writer → Scraping Expert

## Requirements

- `scripts/capture_fixtures.py --refresh --site <site>` re-captures every fixture of that site from its
  `meta.url` into a temporary directory, then prints a report per fixture:
  - `sha256` changed? (expected: yes, sites change constantly)
  - **Selector drift:** for every current `site_constants[site]` detail selector, its match count in the old vs the
    new snapshot (loaded with `set_content` in headless Chromium). Any `≥1 → 0` transition is flagged `DRIFT`.
  - Golden drift: run `scrape_book` against the new snapshot and diff against `.expected.json`.
  - Writes nothing into `tests/fixtures/` unless `--apply` is given, which then also rewrites the metas.
- `docs/test/html-fixtures.md`: when to refresh (a drift report, a failing live smoke test, quarterly), the
  step-by-step (refresh → inspect → `detect-schema --html-file` on the new snapshots → update `parameters.py` by
  hand → `pytest --update-goldens` → review), and the sanitize/size rules.
- Link it from `tests/fixtures/README.md`, CLAUDE.md *Testing*, and `docs/scraping/schema-detection.md`.

## Files

- Modify `scripts/capture_fixtures.py`.
- Create `docs/test/html-fixtures.md`.
- Modify `tests/fixtures/README.md`, `CLAUDE.md`, `docs/scraping/schema-detection.md`, `docs/README.md`.

## Tests

- Unit tests for the pure drift-comparison helper (`compare_selector_matches(old_counts, new_counts)`) in
  `tests/unit/scraping/test_sanitize.py` or a new `test_fixture_drift.py`.
- `/link-check docs/test/ tests/fixtures/README.md`.

## Success criteria

- [ ] A maintainer can go from "Amazon changed its markup" to an updated selector + golden by following the doc.
