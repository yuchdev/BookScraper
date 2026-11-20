# 03 - `push` command & flags

**Parent task:** [README.md](/docs/roadmap/0002-staged-review-and-mongo-sync/03.0-push-to-mongo/README.md)
**Status:** ✅ Complete
**Depends on:** [01-push-planner.md](/docs/roadmap/0002-staged-review-and-mongo-sync/03.0-push-to-mongo/01-push-planner.md), [02-mongo-bulk-writer.md](/docs/roadmap/0002-staged-review-and-mongo-sync/03.0-push-to-mongo/02-mongo-bulk-writer.md)
**Role:** Python Expert

## Context

Wires the planner and writer into a CLI workflow that follows the existing `commands/*.run(args)` shape.

## Requirements

- `cli.py`: new `push` subparser - `--staging-file` (`dest="staging_file"`, default `staging/books.json`), `--dry-run`,
  `--allow-updates`, `--approved-only`, `--report-file` (default `push_report.json`), `--log-severity`. Add it to
  the subparsers `metavar`.
- `commands/push.py` `run(args)`:
  1. `load_staging(path, missing_ok=False)`; `StagingFileError` → exit 1.
  2. `validate_staging_documents`; any problem → print all of them (index + field path + message) → exit 1.
  3. Connect via `database.get_mongo_collection()`; `None` → exit 1.
  4. `fetch_existing` → `build_push_plan`; plan errors → print → exit 1.
  5. Print `format_plan(plan)`. If `--dry-run` → write report → exit 0.
  6. If `plan.requires_allow_updates` and not `--allow-updates` → print which documents would change → exit 1.
  7. `apply_push_plan` in a worker thread (`asyncio.to_thread`, blocking `pymongo`) → report → summary.
- Print the resolved staging file and the target database/collection names (never the URI) at start.
- `main.py`: dispatch `push`.

## Files

- Create `src/bookscraper/commands/push.py`.
- Modify `src/bookscraper/cli.py`, `src/bookscraper/main.py`, `tests/unit/test_cli.py`, `tests/mock/test_main.py`.
- Create `tests/mock/commands/test_push.py`.

## Tests

`test_push_parser_defaults`, `test_missing_staging_file_exits_1`, `test_validation_problems_all_printed_exit_1`,
`test_connection_failure_exits_1`, `test_dry_run_makes_no_write_calls`,
`test_updates_without_flag_refused_before_any_write`, `test_updates_with_flag_applied`,
`test_second_push_is_noop`, `test_uri_never_printed`, `test_main_dispatches_push`.

## Success criteria

- [x] `bookscraper push --dry-run` on any valid file never calls a write method (asserted with a raising fake).
- [x] Refusals happen before the first write, never midway.
