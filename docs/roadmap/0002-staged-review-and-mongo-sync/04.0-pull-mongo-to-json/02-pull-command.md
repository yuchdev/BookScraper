# 02 - `pull` command

**Parent task:** [README.md](/docs/roadmap/0002-staged-review-and-mongo-sync/04.0-pull-mongo-to-json/README.md)
**Status:** ✅ Complete
**Depends on:** [01-collection-export-reader.md](/docs/roadmap/0002-staged-review-and-mongo-sync/04.0-pull-mongo-to-json/01-collection-export-reader.md), [01.0/03 staging file I/O](/docs/roadmap/0002-staged-review-and-mongo-sync/01.0-staging-contract-and-adr/03-staging-file-io.md)
**Role:** Python Expert

## Context

The user-facing half of the reverse flow. The output file may later be edited for hours, so overwriting one by
accident would be costly.

## Requirements

- `cli.py`: `pull` subparser - `-o/--output` (`dest="output"`, **required**), `--force`, `--log-severity`. Add to
  the subparsers `metavar`.
- `commands/pull.py` `run(args)`:
  - Output exists and no `--force` → `print_log` error naming the file and `--force` → exit 1, before connecting.
  - Connect via `database.get_mongo_collection()` (read-only use); `None` → exit 1.
  - In a worker thread: stream `iter_collection` → `to_staging_document` → `write_staging(output, docs)`
    (atomic). An `ExportError` aborts with exit 1 and leaves any existing file untouched.
  - Print document count, output path, and the next step:
    `Edit <file>, then: bookscraper push --staging-file <file> --dry-run`.
- `main.py`: dispatch `pull`. Docs use `staging/books_mongo.json` as the example output (covered by the `staging/` gitignore from 02.0/01).

## Files

- Create `src/bookscraper/commands/pull.py`.
- Modify `src/bookscraper/cli.py`, `src/bookscraper/main.py`, `.gitignore`, `tests/unit/test_cli.py`,
  `tests/mock/test_main.py`.
- Create `tests/mock/commands/test_pull.py`.

## Tests

`test_pull_requires_output`, `test_existing_output_without_force_exits_before_connecting`,
`test_force_overwrites_atomically`, `test_connection_failure_exits_1`, `test_export_error_leaves_file_untouched`,
`test_pull_never_calls_write_methods`, `test_main_dispatches_pull`.

## Success criteria

- [x] An existing file is never modified unless `--force` was given and the export completed.
