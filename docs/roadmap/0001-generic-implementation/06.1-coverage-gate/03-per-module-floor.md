# 03 - Per-module floor

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/06.1-coverage-gate/README.md)
**Status:** ⬜ Not started
**Depends on:** [02-enforce-gate-in-config.md](/docs/roadmap/0001-generic-implementation/06.1-coverage-gate/02-enforce-gate-in-config.md), [Task 06.0 / 04](/docs/roadmap/0001-generic-implementation/06.0-real-html-mock-test-corpus/04-detail-page-golden-tests.md), [Task 06.0 / 05](/docs/roadmap/0001-generic-implementation/06.0-real-html-mock-test-corpus/05-search-and-api-fixtures.md)
**Role:** Testing Expert

## Requirements

- `scripts/check_module_coverage.py`: reads `coverage.json` (from `uv run pytest --cov --cov-report=json`) and
  fails (exit 1) if **any** module under `src/bookscraper/` is below `MODULE_FLOOR = 90`. It prints a table of the
  offenders.
  - Allow-list file `tests/coverage_floor_exceptions.toml`: `module = {floor = 85, reason = "...", until =
    "<task>"}`. It starts **empty**. Adding an entry needs a reason and a linked task.
- Bring `scrape_details.py` (80%) and `search_utils.py` (88%) to ≥ 90% through task 06.0 subtasks 04-05. Any branch
  real HTML can't reach (e.g. `Browser` crash mid-attempt) gets a focused `FakeLocator` test.
- CI runs the script after the test step (task 06.2 subtask 02).

## Files

- Create `scripts/check_module_coverage.py`, `tests/coverage_floor_exceptions.toml`.
- Create `tests/unit/test_check_module_coverage.py` (pure parsing logic, fed a synthetic `coverage.json`).
- Tests for `scrape_details.py` / `search_utils.py` as needed.

## Tests

- `test_floor_script_passes_when_all_modules_above_floor`
- `test_floor_script_fails_and_lists_offenders`
- `test_floor_exception_requires_reason_and_until`

## Success criteria

- [ ] Every module ≥ 90% with an empty exceptions file.
