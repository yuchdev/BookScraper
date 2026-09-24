# 02 - Remove template leftovers

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/08.0-repository-hygiene-and-docs/README.md)
**Status:** ⬜ Not started
**Role:** Python Expert

## Requirements

- Run `/doc-xref release_package.py` and `/doc-xref RELEASE_NOTES.json` to find every reference.
- Delete `release_package.py` and `RELEASE_NOTES.json` (and `init_template.py` if it reappears). CLAUDE.md already
  documents them as non-functional: they read and write a `setup.cfg` that no longer exists.
- If release automation is wanted later, it belongs in a new task built on `uv build` / `uv publish` (see subtask
  04). Don't revive the old scripts.
- Remove the corresponding bullet from CLAUDE.md *Known inconsistencies to watch for*.
- Remove the stale `src/bookscraper.egg-info/` and `src/__init__.py` if nothing imports them (`src/` is not a
  package, and `setuptools` `packages.find` uses `where = ["src"]`). Verify by running the suite and `uv build`
  afterwards.

## Files

- Delete `release_package.py`, `RELEASE_NOTES.json`, possibly `src/__init__.py`.
- Modify `CLAUDE.md`.

## Tests

- The full suite passes, and `uv build` succeeds (subtask 04).

## Success criteria

- [ ] The repo root holds only project files: no cookiecutter artifacts.
