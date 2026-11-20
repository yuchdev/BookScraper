# Threat Model - Staging file -> push to MongoDB - 2026-11-19

Covers the `bookscraper push` / `pull` flow introduced in
[ADR 0003](/docs/adr/0003-staged-review-and-two-way-sync.md) (milestone
[0002](/docs/roadmap/0002-staged-review-and-mongo-sync/plan.md)). Written **before** the validator
(`backends/local/staging.py`) and writer (`backends/mongo/push_writer.py`) are implemented, per
subtasks 01.0/04 and 03.0/02. "Planned validator" = the rule set in those specs.

## Scope

`push` reads a hand-edited JSON array (default `staging/books.json`), validates the whole file, plans
per-document outcomes, then writes via one `bulk_write(ordered=False)` of `InsertOne(doc)` /
`ReplaceOne({"_id": ObjectId(id_str)}, doc)`. `pull` exports the whole collection to a JSON file. In
scope: the staging file, the export file, `push_report.json`, run logs, and the MongoDB Atlas write
path. Out of scope: scrape-time site ingestion (covered by `threat-model` in README.md) and Atlas
auth resolution (`config.resolve_mongo_connection()`, unchanged).

## Assets & trust boundaries

- **Untrusted input:** the staging/export JSON file - hand-edited, headed for the system of record.
  Trust boundary: file -> `validate_staging_documents()` -> `prepare_for_push()` -> `bulk_write`.
- **Assets:** the `bookscraper_db.books` corpus (system of record); `MONGODB_URI` with inline Atlas
  credentials (never in file/report/log output); scraped author names (personal data) and third-party
  descriptions in the export; `push_report.json`.
- **Threat actors:** the single operator making a hand edit that is wrong/hostile in shape; anyone who
  can write `staging/` (local account, a synced/shared dir, a malicious patch to a committed example);
  a second concurrent writer to Atlas.

## Findings

### [HIGH] NoSQL key injection via `$`/dotted/NUL keys nested inside lists (CWE-943)
- Vector: a replacement/insert doc such as `{"authors": [{"$where": "sleep(1000)"}]}` or
  `{"meta": [[{"a.b": 1}]]}`. The planned rule rejects forbidden keys "at any depth", but depth via
  **dict values only** would miss keys inside objects nested in arrays (`authors` is list-shaped and is
  exactly where list-of-object edits live). pymongo rejects top-level `$`-keys in a `ReplaceOne`
  replacement, but nested dotted/`$` keys are stored as literal field names and become query/update
  landmines later.
- Impact: operator-injected field names persisted into the system of record; stored-injection surface.
- Mitigation: planned key validator - **provided traversal descends into every list element and nested
  list, not only dict values** (REQUIRED RULE 1). Closed by the validator with that rule; needs the
  `test_dollar_key_rejected_inside_list` case added alongside `test_dollar_key_rejected_at_depth`.

### [HIGH] Per-item write errors / exceptions leaking document values into report & logs (CWE-209/532)
- Vector: `BulkWriteError.details["writeErrors"]` carries `errmsg` and `keyValue` echoing offending
  field values; pymongo connection errors logged with `exc_info=True` can carry the `MONGODB_URI`
  (credentials) - the pre-existing gap noted in README.md.
- Impact: credentials / untrusted data written verbatim into `push_report.json` and run logs.
- Mitigation: record each failed item as `(index, exception class, Mongo numeric code)` **only** - never
  `details`, `writeErrors[].errmsg`, or `keyValue`; log connection failures without `exc_info` and
  without the URI (REQUIRED RULE 3). Matches the project rule "error class + code only, never the body".

### [MEDIUM] Export file / `staging/` not gitignored -> whole-DB disclosure (CWE-538)
- Vector / evidence: `pull` writes the entire collection (author PII + descriptions) to
  `staging/books_mongo.json`; `.gitignore:1-8` ignores only repo-root `/books.json` and the three
  diagnostic reports - **there is no `staging/` entry today**.
- Impact: a pulled export (and `push_report.json`) could be committed and pushed to the remote.
- Mitigation: add `staging/` (and `push_report.json`) to `.gitignore` before `pull` ships (REQUIRED
  RULE 5a). The ADR assumes a gitignored `staging/`; that assumption must be made real.

### [MEDIUM] `_id` retargeting - an edit overwrites a different document (CWE-639)
- Vector: a valid-but-wrong 24-hex `_id` makes `ReplaceOne({"_id": that}, doc)` overwrite an unrelated
  document (replace, not `$set`, so the victim's fields are wiped). Format validation does not detect
  mis-ownership.
- Impact: silent clobber of an arbitrary document under `--allow-updates`; last-writer-wins.
- Mitigation (accepted trust model): `--allow-updates` gates all replaces; `--dry-run` plans without
  writing; an `_id` absent from Mongo is a plan error, never a re-insert. FOLLOW-UP: have the dry-run
  plan and report list each update as `_id -> title` so the operator can spot a mis-targeted edit.

### [LOW] `_id` type confusion (`{"$oid": ...}`, int, object) (CWE-843)
- Vector: `_id` as an int/object or Extended JSON `{"$oid": ...}` reaching `ObjectId(id_str)`.
- Impact: crash, or a wrong-type lookup, mid-run.
- Mitigation: validator must check `isinstance(_id, str)` **before** the 24-lowercase-hex regex and
  reject non-strings as a type error (REQUIRED RULE 2); `ObjectId()` is only ever handed a pre-validated
  24-hex string, only in `push_writer.py`. `$oid` is independently caught by the `$`-key rule.

### [LOW] Type confusion in `title` / `authors` (CWE-20)
- Vector: `title` as int/null/list, `authors` as a dict or list-of-objects feeding `hash_book`.
- Impact: crash or a meaningless recomputed `hash` that defeats the unique-index dedup.
- Mitigation: planned required-field checks - `title`/`site` non-empty strings, `authors` a list of
  strings (or string) matching `hash_book`; reject dicts and non-string list members. This also removes
  the list-of-objects vehicle for Finding 1.

### [LOW] Oversized document / memory on large file (CWE-400/789)
- Vector: a document over Mongo's 16 MB BSON limit, or a multi-GB staging file loaded whole by
  `json.load` before any per-doc check.
- Impact: write rejection surprise; memory exhaustion before validation.
- Mitigation: planned per-doc 16 MB reject with a clear message - **measure BSON-encoded size
  (`bson.encode`) or a conservative margin, not `len(json)`** (REQUIRED RULE 4); add a sane file-byte
  ceiling checked before `json.load`. Local single-operator keeps residual risk low.

### [LOW] Path / symlink handling for staging, export, and report files (CWE-59/367)
- Vector: destination path is a symlink or lives in an attacker-writable `staging/`; TOCTOU between the
  `pull --force` existence check and the write.
- Impact: write to an unintended location, or stale-perms exposure of DB contents.
- Mitigation: reuse `cert_rotation.atomic_write` exactly - temp file in the **destination directory**,
  `chmod 0o600`, `os.replace` (replaces a symlink at the destination rather than following it), `fsync`
  (REQUIRED RULE 5b). `pull` refuses an existing output without `--force`.

### [LOW/INFO] Replay & idempotency - a safe property, not a gap
- Re-running `push` on the same file is a no-op: no-`_id` docs replan as *duplicate* (hash already
  present), `_id` docs as *unchanged*. `hash` is **recomputed at push time** from edited fields, so a
  stale hash cannot defeat the unique index, and the writer calls `ensure_unique_index_on_hash` first.
  No action; keep the unique index assertion before any write.

### [LOW] Concurrency - last-writer-wins between pull and push (accepted in ADR)
- Vector: Mongo changes between `pull` and `push`; `--allow-updates` replaces the newer doc with the
  edited snapshot (lost update, CWE-362). Concurrent pushers: a `DuplicateKeyError` from another writer
  is classified *duplicate*, not *error*.
- Mitigation: explicitly accepted by the ADR for a single operator. FOLLOW-UP: add optimistic
  concurrency (compare a stored `updated_at`) if a second writer ever exists.

## Required validator / writer rules (preconditions for this PASS)

1. Key traversal rejects `$`-prefixed, `.`-containing, and NUL-containing keys **inside list elements
   and nested lists**, not only dict values; add `test_dollar_key_rejected_inside_list`.
2. `_id` validated as `isinstance str` **before** the 24-lowercase-hex regex; `ObjectId()` only ever
   receives a pre-validated hex string, only in `push_writer.py`.
3. Report and logs record per-item failures as `(index, exception class, Mongo code)` only - never
   `BulkWriteError.details` / `writeErrors[].errmsg` / `keyValue`; no `exc_info`/URI on connection errors.
4. 16 MB limit measured on BSON-encoded size (or a safe margin), plus a file-byte ceiling before
   `json.load`.
5. (a) `staging/` and `push_report.json` added to `.gitignore` before `pull` ships; (b) export and
   report writes use the `cert_rotation.atomic_write` pattern (temp in dest dir, `0o600`, `os.replace`).

## Verdict: PASS_WITH_FOLLOWUP

No open CRITICAL finding. The two HIGH findings (nested-list key injection; error/credential leakage
into report and logs) are fully closed by the planned validator and writer **only if** Required Rules 1
and 3 are implemented as written; with those, nothing reaches `bulk_write` or a report that the operator
did not deliberately place there. Required Rules 2, 4, and 5 close the LOW/MEDIUM findings. The `_id`
retargeting and pull/push concurrency residuals are accepted by ADR 0003 for the single-operator model,
with the two FOLLOW-UPs noted. Fixes to `python-expert`; rejecting tests to `testing-expert`.
