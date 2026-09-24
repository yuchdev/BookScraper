# Task 01.0 - Storage Backend Abstraction

**Parent milestone:** [plan.md](/docs/roadmap/0001-generic-implementation/plan.md)
**Status:** 🔶 In progress
**Category:** feature | **Priority:** P0

## Scope

One `StorageBackend` contract (`save_books`, `book_exists_by_hash`, `book_exists_by_asin`, `leanpub_book_exists`,
`close`) with two interchangeable implementations: `MongoBackend` (MongoDB Atlas, unique index on `hash`) and
`JsonBackend` (a persistent, accumulating repo-root `books.json`, schema-identical to the Mongo documents). A required
`--store-backend {mongo|json}` flag picks exactly one backend for both reads and writes, and
`resolve_store_backend()` pre-flights it (Mongo ping or local write test) and exits if it is unusable.

Subtasks 01-04 record the delivered core. Subtasks 05-08 fix correctness and scale gaps found while reviewing it:
`save_books_to_mongodb()` does not stop when the collection is `None`, the JSON store rewrites non-atomically and
re-parses the whole file on every lookup, and pre-scrape lookups on `asin` / `book_id` / `slug` have no Mongo index.

## Subtasks

| #  | Document                                                                    | Status         | Blocks |
|----|-----------------------------------------------------------------------------|----------------|--------|
| 01 | [StorageBackend contract](/docs/roadmap/0001-generic-implementation/01.0-storage-backend-abstraction/01-storage-backend-contract.md)                   | ✅ Complete    | 02, 03 |
| 02 | [JSON local store](/docs/roadmap/0001-generic-implementation/01.0-storage-backend-abstraction/02-json-local-store.md)                                  | ✅ Complete    | 04, 07 |
| 03 | [Mongo backend wrapper](/docs/roadmap/0001-generic-implementation/01.0-storage-backend-abstraction/03-mongo-backend-wrapper.md)                        | ✅ Complete    | 04, 05 |
| 04 | [`--store-backend` selection & pre-flight](/docs/roadmap/0001-generic-implementation/01.0-storage-backend-abstraction/04-store-backend-selection.md)   | ✅ Complete    | 08     |
| 05 | [Mongo save hardening](/docs/roadmap/0001-generic-implementation/01.0-storage-backend-abstraction/05-mongo-save-hardening.md)                          | ⬜ Not started | 08     |
| 06 | [Secondary lookup indexes](/docs/roadmap/0001-generic-implementation/01.0-storage-backend-abstraction/06-secondary-lookup-indexes.md)                  | ⬜ Not started | -      |
| 07 | [JSON store atomic write & index cache](/docs/roadmap/0001-generic-implementation/01.0-storage-backend-abstraction/07-json-store-atomic-write-and-cache.md) | ⬜ Not started | 08 |
| 08 | [Backend parity contract tests](/docs/roadmap/0001-generic-implementation/01.0-storage-backend-abstraction/08-backend-parity-contract-tests.md)        | ⬜ Not started | -      |

## Key constraints

- **No dual-write, no implicit default.** One backend per run, always named on the command line.
- **Behavioral parity is the contract.** Anything one backend dedups, the other dedups the same way; subtask 08
  turns that promise into an executable test.
- `books.json` persists across runs and is **never** deleted or truncated by the app - a write failure must leave
  the previous file intact (subtask 07).
- `backends/` imports only from `book_utils.py`; it never imports `scraping/` or `commands/`.
