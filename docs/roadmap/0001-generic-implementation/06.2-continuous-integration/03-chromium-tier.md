# 03 - Chromium tier in CI

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/06.2-continuous-integration/README.md)
**Status:** ⬜ Not started
**Depends on:** [02-test-and-coverage-workflow.md](/docs/roadmap/0001-generic-implementation/06.2-continuous-integration/02-test-and-coverage-workflow.md), [Task 06.0 / 03](/docs/roadmap/0001-generic-implementation/06.0-real-html-mock-test-corpus/03-fixture-serving-harness.md)
**Role:** Testing Expert

## Requirements

- In `tests.yml`, before pytest: resolve the installed Playwright version
  (`uv run python -c "import importlib.metadata as m; print(m.version('playwright'))"`), cache
  `~/.cache/ms-playwright` keyed on OS + that version, and run
  `uv run playwright install --with-deps chromium` on a cache miss (`install-deps` only on Linux).
- Assert the Chromium tier actually ran: a final step greps the pytest summary for `skipped` inside
  `tests/mock/scraping/html/` and fails if the harness skipped because Chromium was missing. A silently skipped
  tier is the failure mode CLAUDE.md warns about.

## Files

- Modify `.github/workflows/tests.yml`.

## Tests

- A cache hit on the second run (visible in the job log, noted in the PR).
- Deliberately removing the install step makes the workflow fail at the "tier ran" assertion.

## Success criteria

- [ ] The real-HTML goldens run on every PR.
