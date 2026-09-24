# 06 - Schema version & data migration

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/03.1-canonical-book-data-model/README.md)
**Status:** ⬜ Not started
**Depends on:** [04-per-site-normalizers.md](/docs/roadmap/0001-generic-implementation/03.1-canonical-book-data-model/04-per-site-normalizers.md)
**Role:** Python Expert → Security Auditor (Mongo write path)

## Requirements

- `scripts/migrate_books.py` (out of the coverage scope like other `scripts/`, but with its own tests), exposed as
  `bookscraper migrate --store-backend {mongo|json} [--apply]`. It is a new subcommand in `cli.py`, dispatched
  via `commands/migrate.py`, so it reuses `resolve_store_backend`.
  - **Dry-run by default.** It prints a per-field change summary and writes nothing without `--apply`.
  - v1 → v2 mapping: `about_the_book`→`description`; `categories`→`tags`; `last_published_at`→`publication_date`
    (through `normalize_date`); raw `publication_date` → normalized; `"N/A"` ISBNs → `None` or normalized;
    `site: "leanpub.com"` → `"leanpub"`; Leanpub API `url` → human URL; add `schema_version: 2`, `scraped_at`
    (unknown → `"1970-01-01T00:00:00Z"`, documented as a sentinel); drop keys not in the schema.
  - **Hash guard:** recompute the hash for each migrated document. If it differs from the stored one, do **not**
    change `hash`. List the document in a `hash_drift` section of the report and leave that document at
    `schema_version: 1`, for a human to decide.
  - `json` backend: goes through the atomic write from task 01.0 subtask 07, with `books.json.bak` created first.
  - `mongo` backend: `bulk_write` of `ReplaceOne({"_id": id, "schema_version": {"$ne": 2}}, new_doc)` in
    batches of 500. It is idempotent and safe to re-run.
- Report: `migration-report-<timestamp>.json` in the run log directory, with counts per change type and
  `hash_drift` entries (`_id`/`hash`/`title` only).

## Files

- Create `src/bookscraper/commands/migrate.py`; modify `src/bookscraper/cli.py`, `src/bookscraper/main.py`.
- Create `src/bookscraper/backends/migration.py` (pure v1→v2 transform + per-backend apply).
- Create `tests/unit/backends/test_migration.py`, `tests/mock/commands/test_migrate.py`.
- Modify CLAUDE.md (*Running*, *Storage backend*).

## Tests

- `test_transform_maps_every_legacy_field`
- `test_transform_is_idempotent_on_v2_documents`
- `test_hash_drift_document_left_at_v1_and_reported`
- `test_dry_run_writes_nothing` (both backends)
- `test_apply_json_creates_backup_first`
- `test_apply_mongo_uses_guarded_replace_one_batches`
- `test_cli_migrate_requires_store_backend`

## Success criteria

- [ ] Running `migrate --apply` twice produces zero changes the second time.
- [ ] After migration, every v2 document passes `validate_document()`.
