# 06 - `--version` & help polish

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/05.0-cli-improvements/README.md)
**Status:** ⬜ Not started
**Depends on:** [01-shared-parent-parsers.md](/docs/roadmap/0001-generic-implementation/05.0-cli-improvements/01-shared-parent-parsers.md)
**Role:** Python Expert

## Requirements

- Top-level `--version` / `-V` printing `bookscraper <version> (Python <x.y.z>, Playwright <v>)`. The version comes
  from `importlib.metadata.version("bookscraper")`, with a fallback of `"0+unknown"` when the package isn't
  installed. Never hard-code it.
- Top-level `epilog` with three example invocations and the exit-code table (subtask 05).
- Fix the stale `search` description: it says "deduplicate against MongoDB", but either backend is used.
- Every subparser gets a one-line `help` and a fuller `description`. Spot-check that `bookscraper <cmd> --help`
  fits in 100 columns without awkward wrapping.

## Files

- Modify `src/bookscraper/cli.py`.
- Tests: `tests/unit/test_cli.py`.

## Tests

- `test_version_flag_prints_package_version_and_exits_zero`
- `test_version_falls_back_when_metadata_missing`
- `test_search_description_does_not_mention_mongodb_only`
- `test_top_level_epilog_lists_exit_codes`

## Success criteria

- [ ] `uv run bookscraper --version` works.
