# 01 - Python version alignment

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/08.0-repository-hygiene-and-docs/README.md)
**Status:** ⬜ Not started
**Role:** Python Expert

## Requirements

- Determine the real floor. `datetime.UTC` in `backends/mongo/cert_rotation.py` needs **3.11**, and
  `zip(..., strict=True)` in `commands/search.py` needs 3.10. Survey with
  `uvx vermin --no-tips --violations src/` (a one-off tool, not a dependency) and record the output in the PR.
- Set `requires-python = ">=3.11"` (unless the survey finds a higher requirement), set `ruff.toml` `target-version`
  to the same version (`py311`), and add `classifiers` for each supported minor version.
- Add a unit guard that parses `pyproject.toml` and `ruff.toml` and asserts that the two floors match.
- The CI matrix (task 06.2 subtask 02) covers the floor through the newest stable release.

## Files

- Modify `pyproject.toml`, `ruff.toml`, `uv.lock` (re-lock).
- Create `tests/unit/test_project_metadata.py`.

## Tests

- `test_requires_python_matches_ruff_target_version`
- The full suite passes on the floor version (locally via `uv run --python 3.11 pytest`, noted in the PR).

## Success criteria

- [ ] `uv sync` on Python 3.10 fails fast with a clear `requires-python` message instead of a runtime `ImportError`.
