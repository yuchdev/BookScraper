# 02 - End-to-end two-step mock test

**Parent task:** [README.md](/docs/roadmap/0002-staged-review-and-mongo-sync/05.0-migration-tests-and-docs/README.md)
**Status:** ✅ Complete
**Depends on:** [01-retire-store-backend-tests.md](/docs/roadmap/0002-staged-review-and-mongo-sync/05.0-migration-tests-and-docs/01-retire-store-backend-tests.md)
**Role:** Testing Expert

## Context

Each step has its own tests; nothing yet proves the steps compose through the real CLI entry point with the real
file in between - which is the whole point of the milestone.

## Requirements

- One mock-tier test driving `main.main()` with patched `sys.argv` for each step, sharing one `tmp_path` staging
  file and one in-memory collection:
  1. `scrape-urls -f <input> --staging-file <tmp>/books.json` with Playwright faked
     (`tests/mock/scraping/playwright_fakes.py`) → file has N `pending` documents; collection untouched.
  2. Test code edits the file like a reviewer: fix one title, mark one `rejected`.
  3. `push --dry-run` → exit 0, zero writes, report shows N-1 inserts with one `hash_changed`.
  4. `push` → N-1 documents in the collection, the edited title stored, the rejected one absent.
  5. `pull -o <tmp>/export.json` → `push --staging-file <tmp>/export.json --dry-run` → all `unchanged`.
- A second test: re-run step 1 after step 2 and assert the reviewer's edits survived (02.0/04).

## Files

- Create `tests/mock/test_two_step_pipeline.py`.

## Tests

`test_scrape_review_push_pull_cycle`, `test_rescrape_preserves_review_edits`.

## Success criteria

- [x] Both pass; runtime under 5 s (no real browser, no network).
