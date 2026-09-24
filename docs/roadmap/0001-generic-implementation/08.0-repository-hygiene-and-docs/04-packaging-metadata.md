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
- `pandas` is used only for `pd.read_csv` of a one-column CSV in `commands/scrape_urls.py`. Replace it with stdlib
  `csv.DictReader`, keeping identical error handling (missing file, parse error, missing `url` column → the same
  messages and exit codes), and drop the dependency. It is the heaviest install in the tree. Run `/dep-audit` after.
- Build smoke test: `uv build` produces an sdist and a wheel. Installing the wheel into a clean venv and running
  `bookscraper --version` works. Wire this into CI as a job in `tests.yml`.

## Files

- Modify `pyproject.toml`, `uv.lock`, `src/bookscraper/commands/scrape_urls.py`.
- Modify `tests/mock/commands/test_scrape_urls.py`, `.github/workflows/tests.yml`.

## Tests

- existing `scrape_urls` CSV tests pass after the pandas removal, plus
  `test_csv_with_bom_and_extra_columns_is_read` and `test_csv_missing_url_column_exits_with_same_message`.
- CI build job green.

## Success criteria

- [ ] `pandas` is absent from `uv.lock`'s direct dependencies.
- [ ] The wheel installs and runs in a clean venv.
