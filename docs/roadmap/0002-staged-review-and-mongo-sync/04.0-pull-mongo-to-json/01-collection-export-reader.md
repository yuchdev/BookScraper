# 01 - Collection export reader

**Parent task:** [README.md](/docs/roadmap/0002-staged-review-and-mongo-sync/04.0-pull-mongo-to-json/README.md)
**Status:** ✅ Complete
**Depends on:** [01.0/02 staging document contract](/docs/roadmap/0002-staged-review-and-mongo-sync/01.0-staging-contract-and-adr/02-staging-document-contract.md)
**Role:** Python Expert

## Context

`dumps_documents()` stringifies non-JSON-native values, which is fine for reports but ambiguous for a round trip:
an `ObjectId`, a `datetime`, and a `Decimal128` all become plain strings, and `push` must turn the right ones
back. The export must define that mapping explicitly.

## Requirements

- `src/bookscraper/backends/mongo/export.py`:
  - `iter_collection(collection, *, batch_size=500) -> Iterator[dict]` - `find({}).sort("_id", 1)` with
    `batch_size`; no projection (export everything).
  - `to_staging_document(raw: dict) -> dict` - `_id` → 24-hex string; `datetime` → ISO-8601 UTC with `Z`;
    any other BSON-only type (`Decimal128`, `Binary`, nested `ObjectId`, …) → raise `ExportError` naming the field
    path, rather than silently stringifying. Survey the real collection's types first (04.0/04 records it) and
    extend the mapping only for types that actually occur.
  - Stamp `_staging = {"status": "pending", "source": "pull"}`; omit `scraped_at`.
- Mirror the mapping on the push side: 01.0/04's `prepare_for_push` (or 03.0/02's writer) converts the same
  ISO strings back to `datetime` **only for fields whose original type was `datetime`** - record those field names in
  one shared constant (`DATETIME_FIELDS`) rather than guessing from string shape.

## Files

- Create `src/bookscraper/backends/mongo/export.py`.
- Create `tests/unit/backends/mongo/test_export.py` (pure mapping) and `tests/mock/backends/mongo/test_export.py`
  (cursor iteration with a fake collection).

## Tests

`test_object_id_to_hex`, `test_datetime_to_iso_z`, `test_unsupported_bson_type_raises_with_path`,
`test_staging_meta_stamped`, `test_iterates_sorted_by_id_in_batches`, `test_empty_collection_yields_nothing`.

## Success criteria

- [x] No BSON value is converted lossily without an explicit, tested rule.
