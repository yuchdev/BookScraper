# 06 - Accurate coverage documentation

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/06.1-coverage-gate/README.md)
**Status:** ⬜ Not started
**Role:** Docs Writer

## Requirements

Rewrite `docs/test/code_test_coverage.md` for **this** project:

- Threshold: 90% (total), per-module floor 90%, and branch coverage status (subtask 05).
- Commands: `uv sync`, `uv run pytest --cov`, `uv run pytest --cov --cov-report=html` (output `htmlcov/`,
  gitignored), and `uv run python scripts/check_module_coverage.py`.
- The tier model (unit / mock / mock-html (Chromium) / integration) and which tiers count toward coverage.
- Rules: no padding; the allowed `pragma: no cover` patterns; how to request a floor exception.
- Remove every PowerShell, `pip install -e .[dev]`, `.coveragerc`, and "85%" reference.
- Link it from CLAUDE.md *Testing* and `docs/README.md`.

## Files

- Modify `docs/test/code_test_coverage.md`, `CLAUDE.md`, `docs/README.md`, `.gitignore` (`htmlcov/`, `coverage.json`).

## Tests

- `/link-check docs/test/`
- `grep -n "85%\|powershell\|coveragerc" docs/test/code_test_coverage.md` returns nothing.

## Success criteria

- [ ] The doc's commands run verbatim on a fresh clone.
