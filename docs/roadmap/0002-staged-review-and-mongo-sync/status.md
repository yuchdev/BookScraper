# Milestone 0002 - Staged Review & Two-Way Mongo Sync - Status

Tracks progress against [plan.md](/docs/roadmap/0002-staged-review-and-mongo-sync/plan.md). Updated by the
`implement-subtasks` loop with a targeted row edit as each task lands.

## Current status

| Task | Name                                | Status      | Subtasks done | Tests |
|------|-------------------------------------|-------------|---------------|-------|
| 01.0 | Staging contract & ADR              | ✅ Complete | 4 / 4         | `tests/unit/backends/local/test_staging.py`, `tests/unit/backends/local/test_staging_validation.py` |
| 02.0 | Scrape to staging (step 1)          | ✅ Complete | 5 / 5         | `tests/unit/test_cli.py`, `tests/mock/backends/test_dedup.py`, `tests/mock/commands/test_staging_output.py`, `tests/mock/commands/test_scrape_urls.py`, `tests/mock/commands/test_search.py` |
| 03.0 | Push to MongoDB (step 2)            | ✅ Complete | 5 / 5         | `tests/unit/backends/mongo/test_push_plan.py`, `tests/mock/backends/mongo/test_push_writer.py`, `tests/mock/commands/test_push.py`, `tests/integration/test_push_roundtrip.py` |
| 04.0 | Pull MongoDB to JSON (reverse flow) | ✅ Complete | 4 / 4         | `tests/unit/backends/mongo/test_export.py`, `tests/mock/commands/test_pull.py`, `tests/mock/test_pull_push_roundtrip.py`, `tests/integration/test_pull_export.py` |
| 05.0 | Migration, tests & documentation    | ✅ Complete | 4 / 4         | `tests/mock/test_two_step_pipeline.py`; whole suite 500 passed, 96% total (2026-11-19) |

**Legend:** ✅ Complete · 🔶 In progress / partial · ⬜ Not started

**Totals:** 22 / 22 subtasks complete.

## Open follow-ups

- **First live Atlas run not yet recorded.** `tests/integration/test_push_roundtrip.py` and `test_pull_export.py`
  skip cleanly without `TEST_MONGODB_URI_PASS`; run `uv run pytest -m integration` once credentials are available,
  and keep the per-field BSON type survey Test B prints (evidence for 04.0/01's mapping). Until then the milestone's
  "once, against Atlas" definition-of-done item stays open.

## Implementation notes (deviations from the specs, all deliberate)

- **01.0/03:** a new `backends/local/staging.py` module replaced `backends/local/store.py` (deleted) rather than
  renaming it; `backends/storage.py` was replaced by `backends/dedup.py`.
- **04.0/01:** no `DATETIME_FIELDS` constant. `push_writer.restore_bson_types` turns an ISO string back into a
  datetime only where the **live** document's value at the same path is a datetime - the original type decides,
  never the string's shape, and nothing has to be kept in sync by hand.
- **03.0/01:** a pulled document whose hash inputs (title/authors/year) were not edited keeps its stored hash, so
  documents hashed under an older rule never become spurious updates on pull → push.
- **Security:** `docs/security/staging-file-threat-model.md` (verdict PASS_WITH_FOLLOWUP) required five rules - key
  checks inside lists, `_id` type-checked before format, error class + code only, an exact BSON size check plus a
  1 GiB file cap, and gitignored `staging/` / `push_report.json` - all implemented and tested. The dry-run plan
  shows `_id` next to each update (the accepted follow-up).
- **Leanpub** detail records now carry `url` (`https://leanpub.com/<slug>`), so every staging file is valid
  `scrape-urls -f` input.
- **Out of scope but fixed on the way:** `get_mongo_collection()` no longer claims success for a failed connection
  (0001 02.0/04's core bug); `database.save_books_to_mongodb` was removed (no callers after push_writer).
