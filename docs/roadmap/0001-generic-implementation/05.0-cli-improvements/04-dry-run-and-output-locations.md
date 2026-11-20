# 04 - Dry run & output locations

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/05.0-cli-improvements/README.md)
**Status:** ⬜ Not started
**Depends on:** [01-shared-parent-parsers.md](/docs/roadmap/0001-generic-implementation/05.0-cli-improvements/01-shared-parent-parsers.md)
**Role:** Python Expert

## Requirements

| Flag            | `dest`        | Applies to               | Behavior                                                                                  |
|-----------------|---------------|--------------------------|-------------------------------------------------------------------------------------------|
| `--dry-run`     | `dry_run`     | `scrape-urls`, `search`  | Run everything (including dedup reads from the staging file and optionally Mongo), but write nothing to staging; print what *would* be appended (title, site, hash) |
| `--output-dir`  | `output_dir`  | `scrape-urls`, `search`  | Directory for `failed_*.json`, `other_links.json`, `filtered_books.json`; created if missing; default `.` |
| `--staging-file` | `staging_file` | `scrape-urls`, `search` | Delivered by milestone 0002 as `--staging-file` (default `staging/books.json`); see [0002 02.0/01](/docs/roadmap/0002-staged-review-and-mongo-sync/02.0-scrape-to-staging/01-cli-staging-file-flag.md). The original `--json-store` row in this spec is superseded by that design. |

- Dry run reads the staging file (and MongoDB with `--check-mongo`) for dedup; no `--store-backend` flag exists.
  `push --dry-run` additionally exists for the write phase.
- `--staging-file` is threaded into `resolve_dedup_source(staging_file, check_mongo)` from `backends/dedup.py`
  (`JsonBackend` / `backends/storage.py` no longer exist).
- The diagnostic JSON report writers accept a directory. Their hard-coded filenames stay the same.
- The end-of-run summary starts with `DRY RUN - nothing was saved.` when applicable.

## Files

- Modify `src/bookscraper/cli.py`, `src/bookscraper/backends/dedup.py`, `src/bookscraper/backends/local/staging.py`,
  `src/bookscraper/book_utils.py` (`check_local_write_permission(directory)` already takes a directory),
  `src/bookscraper/commands/scrape_urls.py`, `src/bookscraper/commands/search.py`.
- Tests: `tests/unit/test_cli.py`, `tests/mock/commands/*`, `tests/mock/backends/test_storage.py`.

## Tests

- `test_dry_run_never_writes_to_staging_file` (both commands)
- `test_dry_run_still_performs_dedup_reads`
- `test_output_dir_created_and_used_for_all_diagnostics`
- `test_staging_file_path_used_by_dedup_source`

## Success criteria

- [ ] A dry run leaves the staging file byte-identical (mock-verified via a hash before and after).
