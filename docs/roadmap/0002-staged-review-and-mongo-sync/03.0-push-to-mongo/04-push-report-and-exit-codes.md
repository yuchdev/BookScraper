# 04 - Push report & exit codes

**Parent task:** [README.md](/docs/roadmap/0002-staged-review-and-mongo-sync/03.0-push-to-mongo/README.md)
**Status:** ✅ Complete
**Depends on:** [03-push-command.md](/docs/roadmap/0002-staged-review-and-mongo-sync/03.0-push-to-mongo/03-push-command.md)
**Role:** Python Expert

## Context

A push is a write to the system of record; the user needs a durable record of exactly what happened per document,
and scripts need an exit code they can branch on.

## Requirements

- `push_report.json` via `book_utils.save_json_report()`: one record per staging document -
  `{index, action, outcome, title, hash, hash_changed, _id, changed_fields, error}` - in staging-file order.
  Written for dry runs too (`outcome: "planned"`). Gitignore it alongside the other diagnostics.
- Unlike the other diagnostics, write the report even when every item is `unchanged` (an empty-effect push is
  still worth recording); overwrite per run.
- Exit codes: `0` - all items reached their planned outcome (incl. dry run); `1` - refused before writing
  (invalid file, plan error, missing `--allow-updates`, no connection); `3` - wrote, but at least one item errored.
  Align with 0001 05.0/05 (exit-code contract) if it has landed; otherwise this subtask proposes the codes there.
- Final console summary: `inserted / updated / duplicate / unchanged / skipped / errors` and the report path.

## Files

- Modify `src/bookscraper/commands/push.py`, `.gitignore`.
- Extend `tests/mock/commands/test_push.py`.

## Tests

`test_report_one_record_per_document_in_order`, `test_report_written_on_dry_run`,
`test_report_written_when_all_unchanged`, `test_partial_write_error_exits_3`, `test_refusal_exits_1`,
`test_report_contains_no_secrets`.

## Success criteria

- [x] `push_report.json` alone is enough to reconstruct what a run did to the database.
