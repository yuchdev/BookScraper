# 03 - Round-trip fidelity tests

**Parent task:** [README.md](/docs/roadmap/0002-staged-review-and-mongo-sync/04.0-pull-mongo-to-json/README.md)
**Status:** ✅ Complete
**Depends on:** [02-pull-command.md](/docs/roadmap/0002-staged-review-and-mongo-sync/04.0-pull-mongo-to-json/02-pull-command.md), [03.0/03 `push` command](/docs/roadmap/0002-staged-review-and-mongo-sync/03.0-push-to-mongo/03-push-command.md)
**Role:** Testing Expert

## Context

The two-way promise is only real if `pull` and `push` are inverses. This replaces 0001 01.0/08 (backend parity
contract tests), whose "two interchangeable backends" premise no longer exists.

## Requirements

- One mock-tier module that seeds an in-memory collection (`mongomock` or the hand-rolled fake from 0001 01.0/08)
  with a representative corpus - all four sites, every field type actually stored, unicode titles, empty `tags`,
  a `datetime` field - then:
  1. `pull` → `push --dry-run` → plan is 100% `unchanged`.
  2. Edit one field in the export → plan has exactly one `update` with that field in `changed_fields`.
  3. Delete one field → `update`; after `push --allow-updates` the field is gone in the collection.
  4. Append a freshly scraped document → exactly one `insert`.
  5. Delete a document from the export → no deletion in Mongo (documented non-feature).
- Property-style check: for every seeded document, `prepare_for_push(to_staging_document(doc))` equals the
  original (minus `_staging`) after `_id`/`datetime` normalization.

## Files

- Create `tests/mock/test_pull_push_roundtrip.py`.

## Tests

`test_pull_then_push_is_noop`, `test_single_edit_is_single_update`, `test_field_removal_propagates`,
`test_new_document_inserted`, `test_removed_document_not_deleted`, `test_document_level_round_trip_identity`.

## Success criteria

- [x] Deliberately breaking the `datetime` mapping on either side fails at least one test (checked manually, noted
      in the PR).
