# 05 - Branch coverage baseline

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/06.1-coverage-gate/README.md)
**Status:** ⬜ Not started
**Depends on:** [03-per-module-floor.md](/docs/roadmap/0001-generic-implementation/06.1-coverage-gate/03-per-module-floor.md)
**Role:** Testing Expert

## Requirements

- Flip `branch = true`. Record the resulting combined (line+branch) percentage per module in
  `docs/test/code_test_coverage.md` (subtask 06) as a dated baseline.
- If combined total < 90%: close the gap by testing the missing branches in **decision logic first** (use the
  `/test-gap` risk ranking: config resolution, dedup, filters, retry classification, exit-code mapping), before
  touching logging-only branches.
- Keep `fail_under = 90` applied to the combined figure. If reaching it needs more than this subtask's scope,
  temporarily set `fail_under` to the measured baseline (never below 88), with a follow-up subtask recorded in
  this README.

## Files

- Modify `pyproject.toml`, tests as needed, `docs/test/code_test_coverage.md`.

## Tests

- New branch tests as identified by `uv run pytest --cov --cov-branch --cov-report=term-missing`.

## Success criteria

- [ ] `branch = true` is committed and the gate is green.
