# Task 02.0 - Scrape to Staging (Step 1)

**Parent milestone:** [plan.md](/docs/roadmap/0002-staged-review-and-mongo-sync/plan.md)
**Status:** ✅ Complete
**Category:** feature | **Priority:** P0

## Scope

Make step 1 of the pipeline write **only** to the staging file. `scrape-urls` and `search` lose `--store-backend`
and gain `--staging-file` (default `staging/books.json`). Duplicate checks read the staging file through
`StagingIndex`; an opt-in `--check-mongo` additionally consults MongoDB **read-only** so books already in the
database are not scraped again. The `StorageBackend` read/write abstraction is replaced by a narrower read-only
`DedupSource` used during scraping. New results are merged into an existing staging file without disturbing a
reviewer's edits, and every run ends by telling the user what to review and how to push.

## Subtasks

| #  | Document                                                                                                                                   | Status         | Blocks |
|----|--------------------------------------------------------------------------------------------------------------------------------------------|----------------|--------|
| 01 | [CLI: `--store-backend` → `--staging-file`](/docs/roadmap/0002-staged-review-and-mongo-sync/02.0-scrape-to-staging/01-cli-staging-file-flag.md)            | ✅ Complete    | 02, 05 |
| 02 | [Read-only `DedupSource` replaces `StorageBackend`](/docs/roadmap/0002-staged-review-and-mongo-sync/02.0-scrape-to-staging/02-dedup-source-refactor.md)  | ✅ Complete    | 03, 04 |
| 03 | [Opt-in read-only Mongo dedup (`--check-mongo`)](/docs/roadmap/0002-staged-review-and-mongo-sync/02.0-scrape-to-staging/03-check-mongo-read-only-dedup.md) | ✅ Complete    | -      |
| 04 | [Merge into an existing staging file](/docs/roadmap/0002-staged-review-and-mongo-sync/02.0-scrape-to-staging/04-merge-into-staging-file.md)               | ✅ Complete    | 05     |
| 05 | [Run summary & next-step hint](/docs/roadmap/0002-staged-review-and-mongo-sync/02.0-scrape-to-staging/05-run-summary-and-next-step.md)                     | ✅ Complete    | -      |

## Key constraints

- **No scraping code path holds a Mongo collection that can write.** Enforced structurally (the dedup interface has
  no write methods) and by a test (subtask 03).
- **Step 1 never needs Mongo credentials** unless `--check-mongo` is given.
- **Existing documents in the staging file are never modified or reordered** by a scrape run - only appended to.
- Diagnostic reports (`failed_books.json`, `other_links.json`, `failed_urls.json`) are unchanged.
