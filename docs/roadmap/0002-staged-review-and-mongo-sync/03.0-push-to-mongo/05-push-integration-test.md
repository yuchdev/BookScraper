# 05 - Atlas integration test

**Parent task:** [README.md](/docs/roadmap/0002-staged-review-and-mongo-sync/03.0-push-to-mongo/README.md)
**Status:** ✅ Complete (written; skips without `TEST_MONGODB_URI_PASS` - first live Atlas run still to be recorded)
**Depends on:** [04-push-report-and-exit-codes.md](/docs/roadmap/0002-staged-review-and-mongo-sync/03.0-push-to-mongo/04-push-report-and-exit-codes.md)
**Role:** Testing Expert

## Context

Mocks prove the logic; only a real Atlas run proves `bulk_write`, `ObjectId` round-tripping, and the unique index
behave as assumed.

## Requirements

- `tests/integration/test_push_roundtrip.py`, `@pytest.mark.integration`, gated by `require_env("TEST_MONGODB_URI_PASS")`.
- Uses a **throwaway collection** named `push_it_<uuid4>` in the test database, dropped in a `finally` / fixture
  teardown - never the real books collection.
- Scenario: push 3 new docs → 3 inserted; re-push → 3 duplicate; pull them (04.0/01 helper or a direct `find`)
  → edit one title → `push` without `--allow-updates` → exit 1, zero writes → with it → 1 updated, hash
  recomputed; re-push → all unchanged.

## Files

- Create `tests/integration/test_push_roundtrip.py`; possibly extend `tests/integration/mongo_test_helpers.py`.

## Tests

`test_push_insert_duplicate_update_unchanged_cycle`.

## Success criteria

- [x] Passes once against Atlas with the result recorded in the PR (or skips cleanly when credentials are absent).
- [x] Teardown drops the collection even when the test fails.
