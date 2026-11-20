# 04 - Edit validation & hash recomputation

**Parent task:** [README.md](/docs/roadmap/0002-staged-review-and-mongo-sync/01.0-staging-contract-and-adr/README.md)
**Status:** ✅ Complete
**Depends on:** [02-staging-document-contract.md](/docs/roadmap/0002-staged-review-and-mongo-sync/01.0-staging-contract-and-adr/02-staging-document-contract.md), [03-staging-file-io.md](/docs/roadmap/0002-staged-review-and-mongo-sync/01.0-staging-contract-and-adr/03-staging-file-io.md)
**Role:** Security Auditor → Python Expert

## Context

`hash` is `SHA-256(normalized title + authors + year)`. A reviewer who fixes a title typo invalidates the stored
hash; if `push` trusted it, Mongo's unique index would no longer catch the real duplicate. Separately, a
hand-edited file is untrusted input headed for the database: a key like `$where` or `a.b` must never reach
`insert_one` / `update_one`.

## Requirements

- `validate_staging_documents(documents) -> list[ValidationProblem]` (pure): checks every rule in the contract
  (required fields and types, `_id` format, `_staging.status` value, forbidden key patterns at any depth). Returns
  **all** problems (index, field path, message) rather than stopping at the first, so a reviewer fixes the file in
  one pass.
- `prepare_for_push(document) -> PreparedDocument` (pure): deep-copies, strips `_staging`, parses `_id` into an
  `ObjectId`-ready string (conversion itself happens in `backends/mongo/`), recomputes `hash` via
  `book_utils.hash_book`, and records `hash_changed: bool` plus the previous value.
- Security pass before coding: a short threat model in `docs/security/` covering operator injection via keys,
  oversized documents (Mongo's 16 MB limit - reject above it with a clear message), and type confusion
  (`_id` as an object, `authors` as a dict).

## Files

- Modify `src/bookscraper/backends/local/staging.py` (validator + prepare).
- Create `docs/security/staging-file-threat-model.md`.
- Create `tests/unit/backends/local/test_staging_validation.py`.

## Tests

`test_valid_new_and_pulled_documents_pass`, `test_missing_required_field_reported`,
`test_all_problems_reported_not_just_first`, `test_dollar_key_rejected_at_depth`, `test_dotted_key_rejected`,
`test_bad_object_id_rejected`, `test_unknown_review_status_rejected`, `test_oversized_document_rejected`,
`test_staging_meta_stripped`, `test_hash_recomputed_after_title_edit`, `test_unchanged_document_keeps_hash`.

## Success criteria

- [x] `security-auditor` threat model exists before implementation and has no open CRITICAL finding.
- [x] Every rule in `docs/dev/staging-file-format.md` has at least one rejecting test.
