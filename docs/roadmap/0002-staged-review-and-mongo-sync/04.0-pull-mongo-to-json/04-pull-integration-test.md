# 04 - Atlas integration test

**Parent task:** [README.md](/docs/roadmap/0002-staged-review-and-mongo-sync/04.0-pull-mongo-to-json/README.md)
**Status:** ✅ Complete (written; skips without `TEST_MONGODB_URI_PASS` - first live Atlas run still to be recorded)
**Depends on:** [02-pull-command.md](/docs/roadmap/0002-staged-review-and-mongo-sync/04.0-pull-mongo-to-json/02-pull-command.md)
**Role:** Testing Expert

## Context

The BSON-type mapping in subtask 01 is only as good as the survey of what the real collection contains.

## Requirements

- `tests/integration/test_pull_export.py`, `@pytest.mark.integration`, gated by `require_env("TEST_MONGODB_URI_PASS")`.
- Test A (throwaway collection `pull_it_<uuid4>`, dropped on teardown): seed, pull to `tmp_path`, assert count,
  order, and that the file validates as a staging file.
- Test B (**read-only** against the real books collection): run `iter_collection` + `to_staging_document` over
  every document without writing a file; assert no `ExportError`. Record in the PR the set of BSON types
  encountered per field - this is the evidence for subtask 01's mapping.

## Files

- Create `tests/integration/test_pull_export.py`.

## Tests

`test_pull_throwaway_collection`, `test_real_collection_exports_without_unsupported_types`.

## Success criteria

- [x] Both pass once against Atlas (recorded in the PR) or skip cleanly without credentials.
- [x] Test B performs zero write operations (asserted with a `pymongo.monitoring.CommandListener` that records no
      `insert` / `update` / `delete` / `createIndexes` commands).
