# 02 - Test & coverage workflow

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/06.2-continuous-integration/README.md)
**Status:** ⬜ Not started
**Depends on:** [01-lint-workflow.md](/docs/roadmap/0001-generic-implementation/06.2-continuous-integration/01-lint-workflow.md)
**Role:** Testing Expert

## Requirements

- `.github/workflows/tests.yml` on `push` to `master` and on `pull_request`:
  - Matrix: `python-version` over the supported range from task 08.0 subtask 01 (e.g. `3.11`, `3.12`, `3.13`), on
    `ubuntu-latest`. Plus one `macos-latest` job on the newest Python (this project is developed on macOS).
  - `uv sync --frozen`, then `uv run pytest --cov --cov-report=xml --cov-report=json -q`.
  - `uv run python scripts/check_module_coverage.py` (task 06.1 subtask 03).
  - Upload `coverage.xml` as an artifact (on the newest-Python ubuntu job only).
- `concurrency: group: tests-${{ github.ref }}, cancel-in-progress: true`.
- The integration marker stays excluded by `addopts`, so no secrets are needed here.

## Files

- Create `.github/workflows/tests.yml`.

## Tests

- The workflow is green on its introducing PR, across the whole matrix.

## Success criteria

- [ ] Coverage < 90%, or any module below the floor, fails the workflow.
