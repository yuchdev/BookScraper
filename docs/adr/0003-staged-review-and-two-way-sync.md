# 0003 - Staged Review & Two-Way MongoDB Sync

> **Status:** Implemented
>
> **Date:** 2026-11-19
>
> **Supersedes:** _(none - partially supersedes the "interchangeable storage backends" design of roadmap
> milestone 0001, task 01.0, which was never recorded as an ADR)_
>
> **Superseded by:** _(none)_

## Context

Today every scraping run picks exactly one store with the required `--store-backend {mongo|json}` flag
(`backends/storage.py`'s `StorageBackend`, `resolve_store_backend()`). The two backends are deliberately
**interchangeable**: `JsonBackend` writes a repo-root `books.json` that mirrors the MongoDB collection one-to-one,
with the same `hash` uniqueness and the same `asin` / `book_id` / `slug` lookups. That design made "which store did
this run touch?" explicit, but it treats MongoDB and JSON as **alternatives**, and that leaves two gaps:

1. **No review point before the system of record.** With `--store-backend mongo`, whatever `scrape_book()` returns
   (including a wrong-selector `N/A` ISBN, a truncated description, or a mis-parsed author list; see ADR
   [0002](/docs/adr/0002-ai-assisted-schema-detection.md) on how silently selectors degrade) goes straight into
   Atlas. The only way to correct it is to edit documents one by one in Atlas UI or Compass.
2. **No way back out.** `books.json` can only be filled by scraping; there is no supported path from the
   database to a file. Bulk correction, offline inspection, and backups all need ad hoc `mongoexport` runs whose
   output (Extended JSON) is not in the project's own document format.

The forces to balance:

- **MongoDB should be the single system of record.** Two stores that each claim to be authoritative will drift.
- **A human must be able to review and edit before anything reaches Atlas**, in a plain editor, using the same JSON
  format the tool already writes (`book_utils.dumps_documents()`).
- **Data must flow both ways** with no loss: export → edit → re-import must change exactly what was edited.
- **The project's established conventions:** explicit write targets on the command line, *fail loudly, never guess*
  (`config.resolve_mongo_connection()`, `load_input_urls()`), and *automation proposes, a human decides*
  (`rotate-cert`, `detect-schema`).
- **A hand-edited file that is headed for the database is untrusted input.** Field names such as `$where` or `a.b`
  must never reach `insert_one` / `replace_one`.

## Decision

Replace "Mongo **or** JSON" with a **two-step, human-in-the-loop pipeline**, plus a reverse path:

```
scrape-urls / search ──► staging/books.json ──► [human review & edit] ──► push ──► MongoDB Atlas
                                                                            ▲          │
                                  [edit] ◄── staging/books_mongo.json ◄── pull ◄───────┘
```

1. **Step 1 - scrape to staging.** `scrape-urls` and `search` lose `--store-backend` and write **only** to a
   staging file (`--staging-file`, default `staging/books.json`, in a gitignored `staging/` directory). New results
   are appended; existing documents (and a reviewer's edits to them) are never modified or reordered. Duplicate
   checks read the staging file. `--check-mongo` additionally consults MongoDB **read-only**, so books already in the
   database are not scraped again. Without that flag, step 1 needs no Mongo credentials at all.
2. **Pause - review.** A human edits the staging file. Optional per-document review metadata lives in a reserved
   `_staging` object (`status`: `pending` | `approved` | `rejected`, plus `scraped_at`, `source`, `note`).
3. **Step 2 - `push`.** A new `bookscraper push` subcommand applies the file to MongoDB in three phases:
   **validate** the whole file → **plan** (a pure function classifying each document as *insert*, *duplicate*,
   *update*, *unchanged*, or *skipped*) → **write** (one `bulk_write(ordered=False)`). `--dry-run` stops after
   planning. A machine-readable `push_report.json` records every document's outcome.
4. **Reverse - `pull`.** A new `bookscraper pull -o FILE` subcommand exports the **entire** collection to a file in the
   same staging format, which can be edited and pushed back.

The rules that make this safe and deterministic:

- **Identity across the round trip is `_id`, not `hash`.** `hash` is SHA-256 of normalized title + authors + year,
  so the very edits a reviewer makes change it. A pulled document therefore carries MongoDB's `_id` as a plain
  24-hex string (never Extended JSON `{"$oid": ...}`), and a freshly scraped document has none. On `push`, a
  document with no `_id` is an *insert*, or a *duplicate* if its `hash` already exists. A document with an `_id` is
  compared with the live document and becomes *unchanged* or *update*. An `_id` that no longer exists in MongoDB is
  an error, never a silent re-insert.
- **`hash` is recomputed at push time** from the edited fields, so a title fix cannot leave a stale hash behind
  that defeats the unique index. If an update's new hash collides with a *different* document's hash, that is a plan
  error.
- **Updates are opt-in.** If the plan contains any *update* and `--allow-updates` is absent, `push` refuses the
  whole run before the first write (exit 1). Inserts never need a flag. Updates use `ReplaceOne`, not `$set`, so a
  field the reviewer deleted is really removed.
- **`push` never deletes.** Removing a document from a pulled file does nothing in MongoDB. Deletion, if it is ever
  needed, gets its own explicit command and its own ADR.
- **Review default: pushed unless rejected.** Documents with no `_staging.status`, or with `pending` or `approved`,
  are pushed. `rejected` documents never are. `--approved-only` is the stricter opt-in that pushes only `approved`.
  `_staging` is stripped before any write and never reaches MongoDB.
- **Validate everything first, then write.** A missing required field, a malformed `_id`, an unknown review status,
  an oversized document (more than 16 MB), or any key that starts with `$` or `_` (other than the two reserved keys),
  or contains `.`, at any depth, rejects the **entire** file. All problems are listed in one pass. Write errors that
  happen after validation are recorded per document, don't roll back other documents, and give exit code 3.
- **`push` never modifies the staging file.** Re-running it is idempotent: already-inserted books plan as
  *duplicate*, already-applied edits as *unchanged*. To get `_id`s for newly inserted books, `pull`.
- **`pull` is read-only and never clobbers.** It refuses an existing output file unless `--force` is given. It writes
  atomically (temp file + `os.replace`, the `cert_rotation.py` pattern) and sorts by `_id` so diffs are stable. It
  converts BSON types only by explicit, tested rules (`ObjectId` → hex, `datetime` → ISO-8601 `Z`). Any other BSON
  type aborts with the offending field path rather than being stringified lossily.
- **No deprecation shim.** `--store-backend` is removed outright; passing it is argparse's normal exit-2 error. The
  tool has a single operator and no external callers. An existing repo-root `books.json` gets a one-time hint to
  move it into `staging/`, never an automatic move.

Package layout follows CLAUDE.md's one-way import rule. `backends/local/staging.py` handles the format, validation,
atomic I/O, and the in-memory dedup index, with no `pymongo` import. `backends/mongo/push_plan.py` is pure, and
`push_writer.py` and `export.py` are the only modules that do Mongo I/O for these flows. `commands/push.py` and
`commands/pull.py` are the new CLI workflows. `StorageBackend` shrinks to a read-only `DedupSource` with no write
method.

## Alternatives Considered

| Alternative | Pros | Cons | Reason rejected |
|-------------|------|------|-----------------|
| Keep `--store-backend`, add a third `staged` value | Smallest CLI diff; existing tests mostly survive | Keeps `mongo` and `json` as "alternative stores", so the drift and no-review problems remain for the other two values; three modes to document and test | Fixes one path and leaves the two problematic ones in place |
| Scrape straight to MongoDB with a `reviewed: false` flag; review in Atlas UI / Compass | No new file format; one store | Unreviewed data sits in the system of record and every reader must filter on the flag; review happens one document at a time in a GUI; no offline or bulk editing; no export path | Defeats "review *before* the system of record" |
| `mongoexport` / `mongoimport` for the reverse path | Zero code | Extended JSON (`{"$oid": ...}`, `{"$date": ...}`) differs from the project's format; `mongoimport --mode upsert` bypasses the validation, hash recompute, and opt-in-update rules; credentials handled outside `config.py` | Breaks the single-format and fail-loudly rules |
| Match edited documents by `hash` (no `_id` in files) | Keeps CLAUDE.md's "`_id` never appears in `books.json`" rule | Any edit to title, authors, or year changes the hash, so the edited book is inserted as a *new* duplicate | Silently corrupts on exactly the edits review exists for |
| `push` deletes documents missing from a pulled file (full sync) | True mirror semantics | A truncated or partially edited export wipes data; a scrape staging file (which is never a full mirror) would be catastrophic if mistaken for one | Destructive default with no recovery; deletion deserves its own explicit command |
| Require explicit `approved` for every document by default | Strongest review guarantee | Adds a mandatory manual step to every scrape even when nothing needs fixing | Owner chose "pushed unless rejected"; strict mode stays available as `--approved-only` |
| Command names `submit` / `pull`, or a `mongo` group (`bookscraper mongo push`) | `submit` reads as "for review"; a group namespaces Mongo commands | `submit`/`pull` is not a symmetric pair; a group adds a nesting level for two commands | Owner chose the symmetric `push` / `pull` |

## Consequences

### Positive

- Every byte that reaches Atlas from a scrape has been through a file a human could inspect, and through a dry-run
  plan that shows exactly what will change.
- MongoDB is the only system of record. The staging file is explicitly a work area, so the two can't "drift".
- Bulk correction becomes a normal workflow: `pull` → edit in any editor → `push --dry-run` → `push --allow-updates`.
- Step 1 can run with no database credentials, which suits offline scraping and keeps credentials off machines that
  only scrape.
- The pure planner makes `--dry-run` exactly as trustworthy as a real run (same code), and is fully unit-testable.
- Bulk writes replace today's one-`insert_one`-per-book loop.

### Negative

- **Breaking CLI change:** every documented command line that uses `--store-backend` changes, along with the tests
  for it (0001 task 01.0 subtasks 04 and 08).
- **Reverses a documented rule:** CLAUDE.md's "`_id` never appears in `books.json`" no longer holds for pulled
  files. CLAUDE.md must be rewritten, not patched (0002 task 05.0).
- **Two steps instead of one** for the common "just scrape and save" case. The scrape summary prints the exact
  `push` command to soften this.
- Without `--check-mongo`, step 1 may re-scrape books that are already in MongoDB but no longer in the staging file.
  `push` classifies them correctly as duplicates, so the cost is time and politeness budget, not correctness.
- A pulled file is a point-in-time snapshot. If MongoDB changes between `pull` and `push`, `--allow-updates` replaces
  the newer document with the edited snapshot (last writer wins). Optimistic concurrency (for example comparing a
  stored `updated_at`) is deferred until a second writer exists.
- The `datetime` round trip needs a shared list of datetime-typed fields (`DATETIME_FIELDS`). A new datetime field
  added without updating it will fail loudly at `pull`, not corrupt silently.

## Validation / Rollout

Implemented as roadmap milestone [0002](/docs/roadmap/0002-staged-review-and-mongo-sync/plan.md), in the order
01.0 → (03.0 ∥ 04.0) → 02.0 → 05.0. 02.0 (the breaking CLI change) lands together with, or right after, 03.0, so
there is never a build in which scraped books cannot reach MongoDB at all.

The decision is working when:

- A raising fake collection shows that `scrape-urls` / `search` make **zero** write calls (zero calls of any kind
  without `--check-mongo`), and that `push --dry-run` makes zero writes.
- `pull` → `push --dry-run` on an untouched export plans 100% *unchanged*, against a mocked collection (0002 04.0/03)
  and once against Atlas (0002 04.0/04).
- The end-to-end mock test (0002 05.0/02) passes: scrape → edit title + reject one → push → pull → push is a no-op.
- A `security-auditor` threat model for the staging file (`docs/security/staging-file-threat-model.md`) exists before
  the validator and writer are implemented, with no open CRITICAL finding.
- Accepted by the project owner on 2026-11-19; implemented by milestone 0002 the same day (first live Atlas run of
  the integration tests still to be recorded - see the milestone's status.md).

## Links

- **Roadmap milestone:** [docs/roadmap/0002-staged-review-and-mongo-sync/plan.md](/docs/roadmap/0002-staged-review-and-mongo-sync/plan.md)
- **Roadmap subtask:** [01.0/01 - ADR 0003](/docs/roadmap/0002-staged-review-and-mongo-sync/01.0-staging-contract-and-adr/01-adr-staged-review-pipeline.md)
- **Partially superseded design:** [milestone 0001, task 01.0 - Storage backend abstraction](/docs/roadmap/0001-generic-implementation/01.0-storage-backend-abstraction/README.md)
- **Related ADR:** [0002 - AI-assisted schema detection](/docs/adr/0002-ai-assisted-schema-detection.md) (why scraped fields can be silently wrong)
- **Supporting specs (to be written):** `docs/dev/staging-file-format.md` (0002 01.0/02),
  `docs/security/staging-file-threat-model.md` (0002 01.0/04)
