# 01 - Push planner (pure)

**Parent task:** [README.md](/docs/roadmap/0002-staged-review-and-mongo-sync/03.0-push-to-mongo/README.md)
**Status:** ✅ Complete
**Depends on:** [01.0/04 edit validation & hash recompute](/docs/roadmap/0002-staged-review-and-mongo-sync/01.0-staging-contract-and-adr/04-edit-validation-and-hash-recompute.md)
**Role:** Python Expert

## Context

Keeping classification pure makes `--dry-run` trivially honest (it runs the exact same code as a real push) and
lets every edge case be unit-tested without a database.

## Requirements

- `src/bookscraper/backends/mongo/push_plan.py`:
  - `Action = Literal["insert", "duplicate", "update", "unchanged", "skipped_rejected", "skipped_unapproved"]`.
  - `PlannedItem(index, action, title, hash, hash_changed, object_id: Optional[str], changed_fields: list[str])`.
  - `build_push_plan(prepared_docs, *, existing_hashes: set[str], existing_by_id: dict[str, dict],
    approved_only: bool) -> PushPlan`.
- Rules:
  - `_staging.status == "rejected"` → `skipped_rejected`; with `approved_only`, anything not `approved` →
    `skipped_unapproved`.
  - No `_id`: `hash` in `existing_hashes` **or** already seen earlier in this file → `duplicate`; else `insert`.
  - `_id` present and in `existing_by_id`: deep-equal after stripping `_staging` and normalizing `_id` →
    `unchanged`; else `update`, with top-level `changed_fields` listed.
  - `_id` present but not in `existing_by_id` → a **plan error** (the caller refuses the run): the document was
    deleted in Mongo or the `_id` was hand-edited.
  - An `update` whose recomputed `hash` collides with a *different* existing document's hash → plan error (it
    would violate the unique index).
- `PushPlan` exposes counts per action, `errors`, and `requires_allow_updates: bool`.
- `format_plan(plan) -> str` for `--dry-run`: counts, then one line per non-`unchanged` item (index, action, title,
  changed fields, `hash changed` marker).

## Files

- Create `src/bookscraper/backends/mongo/push_plan.py`.
- Create `tests/unit/backends/mongo/test_push_plan.py`.

## Tests

`test_new_document_inserts`, `test_existing_hash_is_duplicate`, `test_in_file_duplicate_counted_once`,
`test_pulled_untouched_is_unchanged`, `test_pulled_edited_is_update_with_changed_fields`,
`test_unknown_object_id_is_plan_error`, `test_update_hash_collision_is_plan_error`, `test_rejected_skipped`,
`test_approved_only_skips_pending`, `test_requires_allow_updates_flag`, `test_format_plan_lists_non_unchanged`.

## Success criteria

- [x] Module imports nothing that does I/O (`pymongo` types only, if any).
- [x] 100% line and branch coverage.
