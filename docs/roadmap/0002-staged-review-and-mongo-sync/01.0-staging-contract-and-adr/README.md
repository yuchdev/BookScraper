# Task 01.0 - Staging Contract & ADR

**Parent milestone:** [plan.md](/docs/roadmap/0002-staged-review-and-mongo-sync/plan.md)
**Status:** ✅ Complete
**Category:** feature | **Priority:** P0

## Scope

Define, once, what a staging file is and how it is read and written, so that `scrape-urls`/`search` (writers),
`push` (reader), and `pull` (writer) all share one contract instead of three ad hoc ones. Records the
decision to move from "Mongo **or** JSON" to "JSON, pause, Mongo" in ADR 0003, specifies the reserved
`_id` / `_staging` fields, and delivers a single `backends/local/staging.py` module with validated loading,
atomic writing, and push-time `hash` recomputation.

## Subtasks

| #  | Document                                                                                                                                   | Status         | Blocks          |
|----|--------------------------------------------------------------------------------------------------------------------------------------------|----------------|-----------------|
| 01 | [ADR 0003 - staged review & two-way sync](/docs/roadmap/0002-staged-review-and-mongo-sync/01.0-staging-contract-and-adr/01-adr-staged-review-pipeline.md) | ✅ Complete    | 02, 02.0, 03.0, 04.0 |
| 02 | [Staging document contract](/docs/roadmap/0002-staged-review-and-mongo-sync/01.0-staging-contract-and-adr/02-staging-document-contract.md)                | ✅ Complete    | 03, 04          |
| 03 | [Staging file I/O](/docs/roadmap/0002-staged-review-and-mongo-sync/01.0-staging-contract-and-adr/03-staging-file-io.md)                                  | ✅ Complete    | 02.0, 03.0, 04.0 |
| 04 | [Edit validation & hash recomputation](/docs/roadmap/0002-staged-review-and-mongo-sync/01.0-staging-contract-and-adr/04-edit-validation-and-hash-recompute.md) | ✅ Complete    | 03.0       |

## Key constraints

- **One file format everywhere.** A staging file, a `pull` export, and today's `books.json` are the same thing: one
  top-level JSON array written by `book_utils.dumps_documents()`. A `pull` export is valid `push` input and valid
  `scrape-urls -f` input.
- **Reserved keys are few and documented:** `_id` (24-hex string, only on documents that came from Mongo) and
  `_staging` (review metadata, never sent to Mongo). Everything else is book data.
- **Never lose a reviewer's edits.** Every write is atomic (temp file + `os.replace`); a crash mid-write leaves the
  previous file intact.
- `backends/local/staging.py` imports only from `book_utils.py`; it never imports `pymongo`, `scraping/`, or
  `commands/`.
