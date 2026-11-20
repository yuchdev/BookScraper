# 04 - Packaging metadata & build smoke test

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/08.0-repository-hygiene-and-docs/README.md)
**Status:** ⬜ Not started
**Depends on:** [01-python-version-alignment.md](/docs/roadmap/0001-generic-implementation/08.0-repository-hygiene-and-docs/01-python-version-alignment.md)
**Role:** Python Expert

## Requirements

- `pyproject.toml` `[project]`: fix `description` ("…with JSON or MongoDB Atlas storage"), and add `license`
  (matching `LICENSE`), `authors`, `keywords`, `classifiers`, and `[project.urls]` (repository, issues,
  documentation).
- Evaluate switching the build backend from `setuptools` to `hatchling` or `uv_build`. Keep setuptools unless the
  switch removes a concrete problem (e.g. the stray `egg-info`), and record the decision in the PR.
- ~~Drop `pandas`.~~ **Done** as part of the CSV → JSON migration: `scrape-urls` input became JSON, read by
  stdlib `json` in `commands/scrape_urls.py`'s `load_input_urls()`, and `pandas` was removed from `pyproject.toml`
  and `uv.lock`. Still run `/dep-audit` once against the result.
- Build smoke test: `uv build` produces an sdist and a wheel. Installing the wheel into a clean venv and running
  `bookscraper --version` works. Wire this into CI as a job in `tests.yml`.

## Files

- Modify `pyproject.toml`, `uv.lock`.
- Modify `.github/workflows/tests.yml`.

## Tests

- CI build job green.

## Success criteria

- [x] `pandas` is absent from `uv.lock`'s direct dependencies.
- [ ] The wheel installs and runs in a clean venv.
