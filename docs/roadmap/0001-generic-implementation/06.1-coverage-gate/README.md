# Task 06.1 - 90% Coverage Gate

**Parent milestone:** [plan.md](/docs/roadmap/0001-generic-implementation/plan.md)
**Status:** 🔶 In progress
**Category:** test | **Priority:** P0
**Depends on:** [Task 06.0](/docs/roadmap/0001-generic-implementation/06.0-real-html-mock-test-corpus/README.md)

## Scope

CLAUDE.md sets **≥90% line coverage** on `src/bookscraper`. On 2026-09-24,
`uv run pytest -m "not integration" --cov=bookscraper` reported **95% total (344 passed)**. The target is met in
aggregate, but:

- It is **not enforced**. `--cov-fail-under=90` exists only in a doc command line. `pyproject.toml` has no
  `[tool.coverage.*]` section, so a regression to 60% would pass `uv run pytest`.
- Two modules sit below the bar: `scraping/scrape_details.py` **80%** and `scraping/search_utils.py` **88%**. A
  high total hides them.
- `__main__.py` is 0% and `main.py:33` (`if __name__ == "__main__"`) is uncovered.
- Coverage is line-only. Branch coverage has never been measured.
- `docs/test/code_test_coverage.md` describes a different project: 85%, `pip install -e .[dev]`, PowerShell,
  `.coveragerc`, `.htmlcov/`.

## Subtasks

| #  | Document                                                              | Status         | Blocks |
|----|-----------------------------------------------------------------------|----------------|--------|
| 01 | [Reach ≥90% total line coverage](/docs/roadmap/0001-generic-implementation/06.1-coverage-gate/01-reach-ninety-percent-total.md) | ✅ Complete | 02     |
| 02 | [Enforce the gate in configuration](/docs/roadmap/0001-generic-implementation/06.1-coverage-gate/02-enforce-gate-in-config.md) | ⬜ Not started | 03   |
| 03 | [Per-module floor](/docs/roadmap/0001-generic-implementation/06.1-coverage-gate/03-per-module-floor.md)                      | ⬜ Not started | -      |
| 04 | [Entry-point coverage](/docs/roadmap/0001-generic-implementation/06.1-coverage-gate/04-entry-point-coverage.md)              | ⬜ Not started | -      |
| 05 | [Branch coverage baseline](/docs/roadmap/0001-generic-implementation/06.1-coverage-gate/05-branch-coverage-baseline.md)      | ⬜ Not started | -      |
| 06 | [Accurate coverage documentation](/docs/roadmap/0001-generic-implementation/06.1-coverage-gate/06-coverage-documentation.md)  | ⬜ Not started | -      |

## Key constraints

- **No coverage padding.** No test exists only to execute lines without asserting behavior. Every new test
  asserts an observable outcome. `# pragma: no cover` is allowed only for the patterns listed in subtask 02, each
  with a reason.
- `scripts/*.py` stays out of scope, as CLAUDE.md says.
- The two Playwright-DOM-heavy functions reach the floor mainly through task 06.0's real-HTML tier, not through
  more `FakeLocator` permutations.
