# 04 - Packaging metadata & build smoke test

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/08.0-repository-hygiene-and-docs/README.md)
**Status:** ✅ Complete
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

## Notes (as built)

- **Build backend kept as `setuptools`.** No concrete problem was found that switching to
  `hatchling`/`uv_build` would remove: `uv build` produces a clean sdist+wheel and the stray
  `src/bookscraper.egg-info` is a build artifact removed by the build step, not a backend defect.
  The `requires` floor was bumped to `setuptools>=77` only so the PEP 639 SPDX `license = "MIT"`
  expression is guaranteed to build (it needs setuptools 77+); the backend itself is unchanged.
- **`--version` already present.** The CLI already carries `-V`/`--version` (argparse
  `action="version"` via `cli._version_string()` using `importlib.metadata`), with unit tests
  (`tests/unit/test_cli.py`) and docs (`docs/cli/cli.md`), so no new flag/test/doc was needed.
- **Metadata:** `description` updated to "…with JSON or MongoDB Atlas storage"; added `license`
  (SPDX `MIT`, matching `LICENSE`), `authors` (name only, no email), `keywords`, and
  `[project.urls]` (Repository/Issues/Documentation off the `origin` https URL). Classifiers kept.
- **pandas removed** from `[project].dependencies`; `commands/scrape_urls.py` now reads the
  one-column URL CSV with stdlib `csv.DictReader` (`utf-8-sig` BOM strip, tolerant of extra
  columns), preserving the four malformed-input messages/exit codes. `uv lock` dropped pandas and
  its transitive deps (numpy, python-dateutil, six, tzdata) with no other version changes.
- **`/dep-audit`:** the only findings are the known-open `certifi`/`pymongo` advisories, left as-is
  per task scope.
