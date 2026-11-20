# 01 - CLI: `--store-backend` → `--staging-file`

**Parent task:** [README.md](/docs/roadmap/0002-staged-review-and-mongo-sync/02.0-scrape-to-staging/README.md)
**Status:** ✅ Complete
**Depends on:** [01.0/01 ADR 0003](/docs/roadmap/0002-staged-review-and-mongo-sync/01.0-staging-contract-and-adr/01-adr-staged-review-pipeline.md) (Accepted)
**Role:** Python Expert

## Context

`cli.py` declares `--store-backend {mongo,json}` as required on `scrape-urls` and `search`. Under the two-step design
neither command chooses a store - both write the staging file.

## Requirements

- Remove `--store-backend` from both subparsers. Passing it must produce argparse's normal "unrecognized
  arguments" error (exit 2) - no hidden alias, no deprecation shim (the project has no external users relying on it;
  confirm in ADR 0003).
- Add to both: `--staging-file PATH`, `dest="staging_file"`, default `"staging/books.json"`, help text naming the next step
  (`bookscraper push`).
- `staging/` is created on first write (`Path.parent.mkdir(parents=True, exist_ok=True)`) and added to
  `.gitignore` as `/staging/`.
- Legacy file: if a repo-root `books.json` exists (from `--store-backend json` runs) and the resolved staging file
  does not, print a one-time `print_log` warning suggesting `mv books.json staging/books.json`. Never move it
  automatically.
- Add to both: `--check-mongo`, `dest="check_mongo"`, `action="store_true"` (behavior in subtask 03).
- Update the `search` description, which still says "deduplicate against MongoDB".
- If 0001 05.0/01 (shared parent parsers) has landed, put both flags in a shared `staging_parent` parser.

## Files

- Modify `src/bookscraper/cli.py`, `tests/unit/test_cli.py`.

## Tests

`test_scrape_urls_staging_file_defaults_to_staging_dir`, `test_search_staging_file_override`,
`test_store_backend_flag_rejected` (both subcommands, exit 2), `test_check_mongo_defaults_false`.
Remove `test_scrape_urls_requires_store_backend`, `test_search_requires_store_backend`,
`test_invalid_store_backend_choice_rejected`.

## Success criteria

- [x] `grep -rn store_backend src/bookscraper/cli.py` is empty.
- [x] `bookscraper scrape-urls --help` mentions `push` as the next step.
