# 05 - Save-boundary validation

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/03.1-canonical-book-data-model/README.md)
**Status:** ⬜ Not started
**Depends on:** [04-per-site-normalizers.md](/docs/roadmap/0001-generic-implementation/03.1-canonical-book-data-model/04-per-site-normalizers.md)
**Role:** Python Expert

## Requirements

- `models.validate_document(doc: dict) -> list[str]` returns human-readable problems (empty list = valid):
  required keys present, types right, `site` in the literal set, `hash` is 64 lowercase hex chars, ISBNs pass
  `isbn.is_valid_*` when non-null, `publication_date` matches `^\d{4}(-\d{2}(-\d{2})?)?$` when non-null,
  `schema_version == SCHEMA_VERSION`.
- Both backends' `save_books()` validate each document first. Invalid documents are **not** written: they count
  as `SaveSummary.errors`, and each one is logged once with its URL and the problem list (never the full document).
- The validator is the single definition of "valid". The migration (subtask 06) and the parity tests (task 01.0
  subtask 08) reuse it.

## Files

- Modify `src/bookscraper/models.py`, `src/bookscraper/backends/storage.py` (or the two backend modules).
- Modify `tests/unit/test_models.py`, `tests/mock/backends/test_storage.py`,
  `tests/mock/backends/test_backend_parity.py`.

## Tests

- `test_validate_document_accepts_canonical_record`
- `test_validate_document_reports_each_missing_required_key`
- `test_validate_document_rejects_bad_hash_and_bad_isbn`
- `test_invalid_document_not_saved_and_counted_as_error` (parametrized over both backends)

## Success criteria

- [ ] No code path can write a document that fails `validate_document()`.
