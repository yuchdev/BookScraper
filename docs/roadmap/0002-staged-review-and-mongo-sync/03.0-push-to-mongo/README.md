# Task 03.0 - Push to MongoDB (Step 2)

**Parent milestone:** [plan.md](/docs/roadmap/0002-staged-review-and-mongo-sync/plan.md)
**Status:** ✅ Complete
**Category:** feature | **Priority:** P0

## Scope

A new `bookscraper push` subcommand that takes a reviewed staging file (or a `pull` export) and applies it to
MongoDB Atlas. It runs in three phases: **validate** the whole file (01.0/04) → **plan** (a pure function that
classifies every document as *insert*, *duplicate*, *update*, *unchanged*, *skipped-rejected*, or *invalid*) →
**write** (bulk insert + per-`_id` replace). `--dry-run` stops after planning and prints the plan. Updates to
existing documents require `--allow-updates`. Every run writes a machine-readable `push_report.json`.

## Subtasks

| #  | Document                                                                                                                         | Status         | Blocks     |
|----|----------------------------------------------------------------------------------------------------------------------------------|----------------|------------|
| 01 | [Push planner (pure)](/docs/roadmap/0002-staged-review-and-mongo-sync/03.0-push-to-mongo/01-push-planner.md)                              | ✅ Complete    | 02, 03     |
| 02 | [Mongo bulk writer](/docs/roadmap/0002-staged-review-and-mongo-sync/03.0-push-to-mongo/02-mongo-bulk-writer.md)                               | ✅ Complete    | 03         |
| 03 | [`push` command & flags](/docs/roadmap/0002-staged-review-and-mongo-sync/03.0-push-to-mongo/03-push-command.md)                           | ✅ Complete    | 04, 05     |
| 04 | [Push report & exit codes](/docs/roadmap/0002-staged-review-and-mongo-sync/03.0-push-to-mongo/04-push-report-and-exit-codes.md)           | ✅ Complete    | 05         |
| 05 | [Atlas integration test](/docs/roadmap/0002-staged-review-and-mongo-sync/03.0-push-to-mongo/05-push-integration-test.md)                    | ✅ Complete    | -          |

## Key constraints

- **Nothing is written unless the whole file validates** and, if the plan contains updates, `--allow-updates` is set.
- **Push never deletes** and **never modifies the staging file.**
- **Idempotent:** pushing the same file twice produces zero inserts and zero updates the second time.
- Planner is pure (no I/O beyond what it is handed); the writer is the only code that calls `insert_*` / `replace_*`.
- `--dry-run` makes **no** write calls - it may read (to classify `_id` documents as update vs unchanged).
- Secrets rule: no connection string or Atlas error body in output or report; per-document errors report the
  exception class and code only.
