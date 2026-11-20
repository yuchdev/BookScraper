# 06 - Schema version & data migration

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/03.1-canonical-book-data-model/README.md)
**Status:** ⬜ Not started
**Depends on:** [04-per-site-normalizers.md](/docs/roadmap/0001-generic-implementation/03.1-canonical-book-data-model/04-per-site-normalizers.md)
**Role:** Python Expert → Security Auditor (Mongo write path)

## Requirements

- `scripts/migrate_books.py` (out of the coverage scope like other `scripts/`, but with its own tests), exposed as
  `bookscraper migrate --staging-file FILE [--apply]`. It is a new subcommand in `cli.py`, dispatched via
  `commands/migrate.py`. `--store-backend` no longer exists; the command operates on a staging-format file (e.g. a
  `pull` export). MongoDB changes reach the database via `push --allow-updates` after migration.

  > **Architecture note (judgment call):** Milestone 0002 eliminated the write path from `scrape-urls`/`search`
  > and made all Mongo writes go through `push`. The `migrate` command follows the same discipline: it transforms
  > a staging-format file in place (the `json` path), then the operator runs `push --allow-updates` to apply the
  > changes to Mongo. This is the only subtask in this milestone where the removal of `--store-backend` forced a
  > mechanism change rather than just a flag rename.

  - **Dry-run by default.** It prints a per-field change summary and writes nothing without `--apply`.
  - v1 → v2 mapping: `about_the_book`→`description`; `categories`→`tags`; `last_published_at`→`publication_date`
    (through `normalize_date`); raw `publication_date` → normalized; `"N/A"` ISBNs → `None` or normalized;
    `site: "leanpub.com"` → `"leanpub"`; Leanpub API `url` → human URL; add `schema_version: 2`, `scraped_at`
    (unknown → `"1970-01-01T00:00:00Z"`, documented as a sentinel); drop keys not in the schema.
  - **Hash guard:** recompute the hash for each migrated document. If it differs from the stored one, do **not**
    change `hash`. List the document in a `hash_drift` section of the report and leave that document at
    `schema_version: 1`, for a human to decide.
  - File path: goes through the atomic write from `staging.write_staging()` (the `backends/local/staging.py`
    replacement for the deleted `store.py` / `books.json.bak` pattern).
  - For Mongo: run `bookscraper pull -o FILE` first to get a staging-format copy of the collection, run
    `migrate --staging-file FILE --apply`, then `bookscraper push --allow-updates --staging-file FILE` to apply.
- Report: `migration-report-<timestamp>.json` in the run log directory, with counts per change type and
  `hash_drift` entries (`_id`/`hash`/`title` only).

## Files

- Create `src/bookscraper/commands/migrate.py`; modify `src/bookscraper/cli.py`, `src/bookscraper/main.py`.
- Create `src/bookscraper/backends/migration.py` (pure v1→v2 transform + file apply via `staging.py`).
- Create `tests/unit/backends/test_migration.py`, `tests/mock/commands/test_migrate.py`.
- Modify CLAUDE.md (*Running*, *Storage backend*).

## Tests

- `test_transform_maps_every_legacy_field`
- `test_transform_is_idempotent_on_v2_documents`
- `test_hash_drift_document_left_at_v1_and_reported`
- `test_dry_run_writes_nothing`
- `test_apply_staging_file_uses_atomic_write`
- `test_apply_mongo_workflow_is_pull_migrate_push` (documents the three-step workflow; no live Mongo needed)
- `test_cli_migrate_requires_staging_file`

## Success criteria

- [ ] Running `migrate --apply` twice on the same file produces zero changes the second time.
- [ ] After migration, every v2 document passes `validate_document()`.
