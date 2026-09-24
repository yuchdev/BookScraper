# 04 - Dry run & output locations

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/05.0-cli-improvements/README.md)
**Status:** ⬜ Not started
**Depends on:** [01-shared-parent-parsers.md](/docs/roadmap/0001-generic-implementation/05.0-cli-improvements/01-shared-parent-parsers.md)
**Role:** Python Expert

## Requirements

| Flag            | `dest`        | Applies to               | Behavior                                                                                  |
|-----------------|---------------|--------------------------|-------------------------------------------------------------------------------------------|
| `--dry-run`     | `dry_run`     | `scrape-urls`, `search`  | Run everything (including backend **reads** for dedup), but call no `save_books`; print what *would* be saved (title, site, hash) |
| `--output-dir`  | `output_dir`  | `scrape-urls`, `search`  | Directory for `failed_*.csv`, `other_links.csv`, `filtered_books.csv`; created if missing; default `.` |
| `--json-store`  | `json_store`  | `scrape-urls`, `search`  | Path of the `json` backend file (default `books.json`); an error if combined with `--store-backend mongo` |

- Dry run still requires `--store-backend` (dedup reads need a backend) and still runs the pre-flight.
- `--json-store` is threaded into `JsonBackend(path)`, which gains a constructor parameter (default
  `LOCAL_STORE_FILE`). The write-permission pre-flight checks **that** file's directory, not `.`.
- The diagnostic CSV writers accept a directory. Their hard-coded filenames stay the same.
- The end-of-run summary starts with `DRY RUN - nothing was saved.` when applicable.

## Files

- Modify `src/bookscraper/cli.py`, `src/bookscraper/backends/storage.py`, `src/bookscraper/backends/local/store.py`,
  `src/bookscraper/book_utils.py` (`check_local_write_permission(directory)` already takes a directory),
  `src/bookscraper/commands/scrape_urls.py`, `src/bookscraper/commands/search.py`.
- Tests: `tests/unit/test_cli.py`, `tests/mock/commands/*`, `tests/mock/backends/test_storage.py`.

## Tests

- `test_dry_run_never_calls_save_books` (both commands)
- `test_dry_run_still_performs_dedup_reads`
- `test_output_dir_created_and_used_for_all_diagnostics`
- `test_json_store_path_used_by_backend_and_preflight`
- `test_json_store_with_mongo_backend_is_usage_error`

## Success criteria

- [ ] A dry run leaves `books.json` byte-identical (mock-verified via a hash before and after).
