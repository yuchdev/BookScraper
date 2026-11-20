# Task 04.0 - Pull MongoDB to JSON (Reverse Flow)

**Parent milestone:** [plan.md](/docs/roadmap/0002-staged-review-and-mongo-sync/plan.md)
**Status:** ✅ Complete
**Category:** feature | **Priority:** P1

## Scope

A new `bookscraper pull -o FILE` subcommand that exports the **entire** MongoDB books collection to a JSON file in
the staging-file format, so data can flow both ways: Mongo → JSON → review/edit → `push` → Mongo. Every exported
document keeps its identity as a string `_id`, so `push` can tell an edited existing book from a new one. The
export is streamed, stably ordered, written atomically, and never overwrites an existing file without `--force`.

## Subtasks

| #  | Document                                                                                                                         | Status         | Blocks |
|----|----------------------------------------------------------------------------------------------------------------------------------|----------------|--------|
| 01 | [Collection export reader](/docs/roadmap/0002-staged-review-and-mongo-sync/04.0-pull-mongo-to-json/01-collection-export-reader.md)         | ✅ Complete    | 02, 03 |
| 02 | [`pull` command](/docs/roadmap/0002-staged-review-and-mongo-sync/04.0-pull-mongo-to-json/02-pull-command.md)                               | ✅ Complete    | 03, 04 |
| 03 | [Round-trip fidelity tests](/docs/roadmap/0002-staged-review-and-mongo-sync/04.0-pull-mongo-to-json/03-round-trip-fidelity.md)            | ✅ Complete    | -      |
| 04 | [Atlas integration test](/docs/roadmap/0002-staged-review-and-mongo-sync/04.0-pull-mongo-to-json/04-pull-integration-test.md)             | ✅ Complete    | -      |

## Key constraints

- **Read-only against Mongo.** `pull` never writes, never creates indexes.
- **Never clobber review work:** an existing output file is an error unless `--force` is given.
- **Lossless for the book schema:** pull → push with no edits must plan 100% `unchanged`.
- Output passes the same `validate_staging_documents` as any staging file (01.0/04).
