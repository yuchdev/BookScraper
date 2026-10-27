# Migrate store from v1 to v2

**Command:** `bookscraper migrate --store-backend {mongo|json} [--apply]`

**When to use:** Documents written before task 03.1 carry per-site *v1* shapes
(`about_the_book`/`categories`/`last_published_at`/`leanpub.com`, raw `publication_date`,
`"N/A"` ISBN sentinels, no `schema_version`). The canonical schema-2 `BookRecord` (see
[`docs/scraping/book-record-schema.md`](../scraping/book-record-schema.md)) is what every site
now writes. Run this command once to bring an existing store up to schema-2.

---

## Usage

```bash
# Dry-run (default): print a per-field change summary, write nothing.
uv run bookscraper migrate --store-backend json

# Apply: rewrite v1 documents in place.
uv run bookscraper migrate --store-backend json --apply

# Mongo backend:
uv run bookscraper migrate --store-backend mongo --apply
```

`migrate` accepts no scraping flags and never writes `parameters.py`.

---

## Key guarantees

- **Dry-run by default.** `--apply` is required to write anything; without it only a per-field
  summary is printed and no store is modified.
- **Hash recomputation.** The migration recomputes `hash_book(title, authors, publication_year)`
  for *every* document. Author de-duplication is part of the hash input (a document repeating a
  name case-insensitively earns a new hash); changed hashes are reported (old → new, with the
  document id). A recomputed hash that **collides** with another document's hash is left untouched
  and reported for a human — never silently overwritten or merged.
- **Idempotent.** Re-running `--apply` on v2 documents changes nothing.
- **Never deletes.** Invalid-after-migration documents are reported and left verbatim.

---

## Transform logic

The v1→v2 field transform is `scraping/normalize.from_v1_document` — a stored v1 document is
treated as raw input to the same per-site normalizers a fresh scrape uses, so a migrated record
is byte-for-byte what a fresh scrape would produce.

`backends/migration.py` stays within the one-way import direction (`backends` never imports
`scraping`) by taking that transform as an injected `Callable[[dict], dict]`;
`commands/migrate.py` (which may import both layers) wires in the concrete function.

The command reuses `resolve_store_backend`, so the same pre-flight checks and
explicit-backend rule apply, and it never touches book data on the *other* backend.

---

## Backend-specific write behavior

- **`json`:** goes through `store`'s atomic write (`temp file + fsync + os.replace`) with the
  one-generation `books.json.bak` backup.
- **`mongo`:** applies guarded `ReplaceOne({"_id": id, "schema_version": {"$ne": 2}}, new_doc)`
  in `bulk_write` batches of 500 — never a bulk collection replace. The guard ensures a
  concurrent migration run cannot clobber a document another run already migrated.

---

## Migration report

A `migration-report-<timestamp>.json` is written to `~/.bookscrapper/logs/` in both dry-run and
`--apply` mode. It contains:

- Counts per change type
- `changed_hashes` list (document id, old hash, new hash)
- `hash_collisions` list (document ids)
- `invalid_documents` list (document ids)
- `field_changes` summary

The console/log output carries only ids/hashes/counts — no credential, URI, or document body. A
collision's title appears only inside the report file.

---

## Exit codes

| Code | Meaning |
|------|---------|
| `0`  | Migration completed (or dry-run completed) |
| `1`  | Corrupt local store (`LocalStoreCorruptError`) |
