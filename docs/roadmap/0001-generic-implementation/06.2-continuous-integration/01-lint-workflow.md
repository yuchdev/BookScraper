# 01 - Lint workflow

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/06.2-continuous-integration/README.md)
**Status:** ⬜ Not started
**Role:** Python Expert

## Requirements

- Add `ruff` to the `dev` dependency group (pinned by `uv.lock`) so local and CI versions match.
- `.github/workflows/lint.yml` on `push` to `master` and on `pull_request`:
  - `astral-sh/setup-uv` (SHA-pinned), `uv sync --frozen --only-dev`.
  - `uv run ruff check .` and `uv run ruff format --check .`.
  - `uv run python scripts/check_doc_links.py docs/` (the existing link checker).
- Fix every existing ruff finding in the same PR, or record a per-file ignore **with a reason** in `ruff.toml`.
  `ruff.toml`'s `target-version` must match the `requires-python` floor chosen in task 08.0 subtask 01.

## Files

- Create `.github/workflows/lint.yml`.
- Modify `pyproject.toml`, `uv.lock`, `ruff.toml`, and any source files ruff flags.

## Tests

- The workflow passes on the PR that introduces it.
- A deliberately mis-formatted commit on a scratch branch fails it (noted in the PR).

## Success criteria

- [ ] `uv run ruff check . && uv run ruff format --check .` is clean on `master`.
