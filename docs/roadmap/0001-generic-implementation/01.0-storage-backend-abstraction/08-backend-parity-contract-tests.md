# 08 - Backend parity contract tests

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/01.0-storage-backend-abstraction/README.md)
**Status:** ⬜ Not started
**Depends on:** [05-mongo-save-hardening.md](/docs/roadmap/0001-generic-implementation/01.0-storage-backend-abstraction/05-mongo-save-hardening.md), [07-json-store-atomic-write-and-cache.md](/docs/roadmap/0001-generic-implementation/01.0-storage-backend-abstraction/07-json-store-atomic-write-and-cache.md)
**Role:** Testing Expert

## Context

"Local storage matches database content one-to-one" is the central promise of the `json` backend. Today each
backend is tested in isolation against its own expectations, so nothing fails if the two drift apart, say if one
starts treating an empty `slug` as a match.

## Requirements

- One parametrized test module that runs the **same** scenario list against both `JsonBackend` (real file under
  `tmp_path`) and `MongoBackend` (backed by an in-memory collection).
- In-memory collection: add `mongomock` to the `dev` dependency group, pinned via `uv add --dev mongomock`, and
  run `/dep-audit`. If the license or CVE audit rejects it, fall back to a hand-rolled `FakeCollection` in
  `tests/mock/backends/fake_collection.py` implementing `insert_many`, `find_one`, `create_index`,
  `index_information` with unique-`hash` enforcement.
- A fixture `backend(request)` parametrized over `["json", "mongo"]`.
- Scenarios (each one test function, each parametrized over both backends):
  - save → `book_exists_by_hash` true; unknown hash false; empty hash false.
  - save same `hash` twice (within one batch and across two calls) → `SaveSummary.duplicates == 1`.
  - book without `hash` → counted as error, not stored.
  - `book_exists_by_asin` true / false / empty.
  - `leanpub_book_exists` by id only, by slug only, neither → false.
  - **Round-trip equality:** after saving the same batch to both, the stored documents (minus Mongo's `_id`) are
    equal as sets of canonical-JSON strings.

## Files

- Create `tests/mock/backends/test_backend_parity.py`.
- Possibly create `tests/mock/backends/fake_collection.py`.
- Modify `pyproject.toml` / `uv.lock` (dev group) if `mongomock` is adopted.

## Tests

`test_saved_book_found_by_hash`, `test_duplicate_hash_counted_once`, `test_book_without_hash_is_error`,
`test_asin_lookup`, `test_leanpub_lookup_by_id_or_slug`, `test_stored_documents_identical_across_backends`,
each parametrized `[json, mongo]`.

## Success criteria

- [ ] Every scenario passes for both parameters.
- [ ] Deliberately breaking parity (e.g. make `store.leanpub_book_exists` ignore `slug`) fails at least one test
      (checked manually, noted in the PR).
