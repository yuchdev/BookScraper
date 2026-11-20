# 01 - Retire `--store-backend` tests & fixtures

**Parent task:** [README.md](/docs/roadmap/0002-staged-review-and-mongo-sync/05.0-migration-tests-and-docs/README.md)
**Status:** ✅ Complete
**Depends on:** [02.0/02 `DedupSource` refactor](/docs/roadmap/0002-staged-review-and-mongo-sync/02.0-scrape-to-staging/02-dedup-source-refactor.md)
**Role:** Testing Expert

## Context

Tasks 02.0-04.0 each update the tests they touch, but leftovers are likely: fixtures named for the old store,
parametrizations over `["mongo", "json"]`, and assertions on `save_books` calls in command tests.

## Requirements

- `grep -rn "store_backend\|StorageBackend\|JsonBackend\|MongoBackend\|resolve_store_backend\|LOCAL_STORE_FILE"
  tests/ src/` returns nothing (or only intentional references in docstrings explaining the migration).
- Rename `isolated_local_store` → `isolated_staging_file` in `tests/conftest.py` (keep the monkeypatching
  guarantee: no test touches the real `staging/` directory); update all users.
- Delete tests whose subject no longer exists; for each deleted test, name its replacement (or "behavior removed")
  in the PR description.
- Run `/document-tests` on every new or changed test file.

## Files

- Modify `tests/conftest.py` and any `tests/**` file the grep finds.

## Tests

Whole suite: `uv run pytest -m "not integration" --cov=bookscraper --cov-report=term-missing`.

## Success criteria

- [x] Grep above is clean.
- [x] Total coverage ≥90%; no module that was ≥90% before drops below it.
