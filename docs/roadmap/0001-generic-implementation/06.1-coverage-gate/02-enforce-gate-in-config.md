# 02 - Enforce the gate in configuration

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/06.1-coverage-gate/README.md)
**Status:** ⬜ Not started
**Depends on:** [01-reach-ninety-percent-total.md](/docs/roadmap/0001-generic-implementation/06.1-coverage-gate/01-reach-ninety-percent-total.md)
**Role:** Testing Expert

## Requirements

- Add to `pyproject.toml`:

  ```toml
  [tool.coverage.run]
  source = ["bookscraper"]
  branch = false          # flipped by subtask 05
  omit = ["*/__main__.py"]   # removed again by subtask 04 once covered

  [tool.coverage.report]
  fail_under = 90
  show_missing = true
  skip_covered = true
  exclude_also = [
      "if TYPE_CHECKING:",
      "raise NotImplementedError",
      "^\s*\.\.\.$",
      "if __name__ == .__main__.:",
  ]
  ```
- Leave `--cov` out of `addopts` (it would slow every single-file run and interfere with the `run_tests` hook).
  Instead, the **canonical command** is `uv run pytest --cov` (the `source` config makes `--cov` alone sufficient),
  and `fail_under` makes it fail below 90%.
- Update every reference to the old command form (CLAUDE.md *Testing*, `.claude/loops/implement-subtasks.md`
  Step 7, `.claude/hooks/run_tests.py` if it hard-codes flags).

## Files

- Modify `pyproject.toml`, `CLAUDE.md`, `.claude/loops/implement-subtasks.md`, possibly `.claude/hooks/run_tests.py`.

## Tests

- Manual: temporarily set `fail_under = 99`; `uv run pytest --cov` must exit non-zero (noted in the PR).
- `test_pyproject_declares_coverage_fail_under_90` (unit: parse `pyproject.toml` with `tomllib`, assert the value).

## Success criteria

- [ ] `uv run pytest --cov` fails when total coverage < 90%.
