# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

`bookscraper` is an asyncio + Playwright scraper that pulls book metadata (title, authors, ISBNs, publication date,
description, tags) from Amazon, Packtpub, Leanpub, and O'Reilly, and writes results to one of two interchangeable
storage backends — a MongoDB Atlas collection, or a local `books.json` file schema-identical to it (see **Storage
backend** below). It exposes one CLI (`bookscraper`) with four subcommands — two scraping workflows, MongoDB Atlas
X.509 certificate rotation (`rotate-cert`), and an AI-assisted selector-schema detection maintenance tool
(`detect-schema`) — see Architecture below.

## Setup

This project uses [uv](https://docs.astral.sh/uv/) for dependency management, virtual environments, and packaging —
there is no `requirements.txt`, `setup.py`, or `setup.cfg`; `pyproject.toml` (dependencies) + `uv.lock` (pinned,
resolved versions — commit it) are the single source of truth.

```bash
uv sync                        # creates .venv and installs dependencies + the bookscraper package itself (editable)
uv run playwright install chromium
```

`uv sync` replaces `python -m venv` + `pip install -r requirements.txt` + `pip install -e .` in one step. Run any
command inside the venv via `uv run <cmd>`, or activate it yourself with `source .venv/bin/activate` and drop the
`uv run` prefix.

MongoDB output needs an auth method configured — see **Authentication configuration** below for the deterministic
`~/.bookscrapper/settings.json` mechanism (preferred) and the legacy `.env`-only fallback it supersedes.
`~/.bookscrapper/` is intentionally minimal: `settings.json`, one or more `X509-cert-*.pem` files, and `logs/`
(run logs — see Logging below) are the only things that belong there. Nothing else does — see Authentication
configuration for where credentials that used to live there (`driver_string.txt`, a `test/` subdirectory) went.

## Authentication configuration

`src/bookscraper/backends/mongo/config.py` is the single place that decides how the app authenticates to MongoDB
Atlas — both the real connection and the pre-flight check `check_mongodb_connection()` (both in
`backends/mongo/database.py`) call `config.resolve_mongo_connection()` rather than reading `os.environ` themselves, so
there is exactly one resolution path to reason about, not two copies that could drift. The pre-flight check used to
live in `book_utils.py`, where it needed function-level imports to avoid an import cycle (`book_utils.py` sits at the
package root and is imported by `backends/__init__.py`'s own import chain); it moved to `database.py` so every import
is module-level (flakeforge X006). Don't move it back into `book_utils.py`.

**Primary mechanism — `~/.bookscrapper/settings.json` (deterministic, no fallback chain):** if this file exists, it
is the *sole* source of truth: its declared `auth_type` and each field's declared `source` are followed exactly,
with nothing else consulted. A missing env var or an unrecognized shape raises `config.ConfigError` immediately
(surfaced as a critical log + `print_log` error, connection aborted) rather than silently trying something else —
determinism means a bad config fails loudly, not that it guesses.

Every field is a `{"source": "env" | "literal", "value": "..."}` object: `"literal"` uses `value` as-is (a full
connection string, or for a cert, a filename resolved under `~/.bookscrapper/` if not absolute); `"env"` treats
`value` as an environment variable name to look up (so `.env`, loaded by the caller before resolution, works too).

Password (SCRAM) auth — one field:
```json
{
  "auth_type": "pass",
  "pass": {
    "uri": {"source": "literal", "value": "mongodb+srv://<user>:<pass>@<cluster>/<db>?retryWrites=true&w=majority"}
  }
}
```

X.509 (client-cert) auth — two independently-sourced fields, `uri` and `cert`:
```json
{
  "auth_type": "x509",
  "x509": {
    "uri": {"source": "env", "value": "MONGODB_URI"},
    "cert": {"source": "literal", "value": "X509-cert-20260921_041307.pem"}
  }
}
```
`uri.source` and `cert.source` are chosen independently, so all four combinations (literal/literal, literal/env,
env/literal, env/env) are valid and behave identically as long as they resolve to the same underlying values — see
`tests/integration/test_connection_settings.py`, which exercises all four plus both `pass` shapes.

**Optional `database` block (namespace):** either shape above may add a top-level `database` block naming the target
database/collection, resolved with the same `{"source", "value"}` mechanism as the auth fields:
```json
"database": {"name": {"source": "literal", "value": "bookscraper_db"},
             "collection": {"source": "literal", "value": "books"}}
```
`config.resolve_mongo_namespace()` (called by `database._initialize_mongodb_connection()`, never `os.environ`
directly) returns `(name, collection)`. When `settings.json` exists but omits the block, the defaults
`bookscraper_db` / `books` apply and `MONGODB_DB_NAME` / `MONGODB_COLLECTION_NAME` are **not** consulted (same
determinism as the auth resolver — settings.json is the sole source of truth). If the block is present it must define
*both* `name` and `collection`, or resolution fails loudly with `ConfigError`. Only in legacy mode (no settings.json)
do `MONGODB_DB_NAME` / `MONGODB_COLLECTION_NAME` apply, each falling back to the same default.

**Unknown top-level keys fail loudly.** The only keys allowed at the top level are `auth_type`, `pass`, `x509`, and
`database`; anything else (e.g. a typo like `"x059"` for `"x509"`) raises `ConfigError` listing the offending key
name(s) — never a field value, so a mistyped block still carrying a literal URI can't leak it into the error/log.

**Permission/ownership hardening (POSIX only, warnings — never aborts).** Because a `literal` URI can embed a
password, `load_settings()` stats the file and emits a one-per-process `print_log` warning (console + run log; naming the file and the
`chmod 600 ~/.bookscrapper/settings.json` fix) when it is group/other-readable (`mode & 0o077 != 0`), is a symlink
(whose target could be attacker-controlled), or is not owned by the current UID. These are advisory only so existing
setups keep working, and are skipped entirely on Windows (`os.name == "nt"`). Cert-path resolution logs only the
resolved path (never file contents) and never opens the file.

**Example file.** `docs/examples/settings.example.json` is a copy-ready x509 (env `uri` / literal `cert`) shape with
placeholders only — no real host, user, password, or cert filename — and passes `/secret-scan`.

**Legacy fallback (no settings.json):** for backward compatibility, `MONGODB_URI` / `TLS_CERT_FILE` env vars (via
`.env` in the repo root or the real environment) are read directly, with `TLS_CERT_FILE` — if unset — falling back
further to the newest `~/.bookscrapper/X509-cert-*.pem` by modification time (`config.get_default_tls_cert_file()`).
`TLS_CA_FILE` (server CA verification, `backends/mongo/database.py` only) is independent of all of this and always
comes straight from the environment:
```dotenv
MONGODB_URI="mongodb+srv://<user>:<pass>@<cluster>/<db>?retryWrites=true&w=majority"
TLS_CERT_FILE="/path/to/cert.pem"   # optional, for X.509 client-cert auth
TLS_CA_FILE="/path/to/ca.pem"       # optional, for server CA verification (database.py only)
```

**Single `MongoClient` factory (`backends/mongo/client.py`).** Both the real connection
(`database._initialize_mongodb_connection`, used by `resolve_store_backend`) and the pre-flight /
`doctor` check (`database.check_mongodb_connection`) build their client through
`client.build_mongo_client()` + `client.ping()` — so there is exactly one `MongoClient(...)`
construction site in `src/` and one set of TLS kwargs, and the two paths can never drift again
(`TLS_CA_FILE` honored on both, a missing client cert failing loudly on both instead of silently
connecting certless). Certificate validation is never disableable: `tlsAllowInvalidCertificates=False`
and `ServerApi("1")` are hard-coded, `tls=True`+`tlsCertificateKeyFile` is set only when a cert is
given and `tlsCAFile` only when a CA file is given. `client.ping()` re-raises every connection/auth
failure as a redacted `MongoConnectionError` carrying only the exception class name plus pymongo
`code`/`codeName` (e.g. `OperationFailure 8000 AtlasError`) — raw pymongo text (which can embed the
host list or the full connection string with password) never reaches a log or the console. The
server-selection timeout defaults to `client.DEFAULT_SERVER_SELECTION_TIMEOUT_MS` (10 000 ms) and is
overridable via the `MONGODB_SERVER_SELECTION_TIMEOUT_MS` env var (a blank/non-integer/non-positive
value warns and falls back to the default).

**TLS certificate rotation pipeline** (see operator runbook: [`docs/runbooks/x509-certificate-rotation.md`](docs/runbooks/x509-certificate-rotation.md))**:** `bookscraper rotate-cert` (`commands/rotate_cert.py`, backed by
`backends/mongo/cert_rotation.py`) issues a fresh Atlas-managed X.509 client certificate via the Atlas Admin API
(`POST .../databaseUsers/{user}/certs`, requires a Project-Owner-scoped Atlas API Service Account: `ATLAS_CLIENT_ID` /
`ATLAS_CLIENT_SECRET` / `ATLAS_PROJECT_ID` / `ATLAS_DB_USER` in `.env` or the environment) and atomically writes it
to `~/.bookscrapper/X509-cert-<UTC timestamp>.pem`. Validity defaults to 6 months (`--months` / `ATLAS_CERT_MONTHS`,
1-24) and the output path can be overridden (`--output` / `ATLAS_CERT_FILE`); flags beat env vars. Failures (a missing
`ATLAS_*` variable, an out-of-range `--months`, an Atlas HTTP error, an unreachable API) exit non-zero with a
`print_log` error and never touch `settings.json`; HTTP errors report only the status code, never the response body.
Its position in the auth system above depends on how the app is configured:
- **No `settings.json`:** nothing else to do — the legacy fallback's "newest `X509-cert-*.pem`" glob picks up the
  new file automatically on the next run.
- **`settings.json` with `x509.cert.source: "literal"`:** the command repoints `x509.cert.value` at the new
  filename itself (`update_settings_after_rotation()`, an atomic owner-only `0600` rewrite), so resolution stays
  fully deterministic — no stale reference to a since-rotated cert — without a manual edit.
- **`settings.json` with `x509.cert.source: "env"`:** the command only prints a reminder naming the env var and
  the new path; it deliberately never modifies your environment or `.env` on your behalf.
- **`auth_type: "pass"`, or no rotation applicable:** the command leaves `settings.json` untouched and says so.

**Verify before repoint:** after the new PEM is written but *before* `settings.json` is repointed, the command
verifies the new certificate actually authenticates to Atlas (`cert_rotation.verify_certificate()`). Verification
builds a client **only** via `backends/mongo/client.build_mongo_client(uri, new_cert, TLS_CA_FILE)` and pings via
`client.ping` — reusing the single client factory so it inherits the redaction contract SEC-3/4/8 (a failure surfaces
as a `MongoConnectionError` carrying only the pymongo `code`/`codeName`, never the URI, host, DB user, cert contents,
or Atlas response body). The URI is the **current** `config.resolve_mongo_connection()[0]`; if none is configured,
verification is skipped with a status line. Atlas can take a short while to propagate a freshly issued cert, so a
connection failure is retried up to `--verify-attempts N` (default `5`) times with a fixed delay, closing the client
after every attempt. On **final** failure the command does **not** repoint `settings.json` (the previously configured
cert keeps working), leaves the new PEM on disk, prints a `WARNING:` line naming the new path and the redacted error
code, and exits `1`. `--no-verify` skips the whole step (repointing without a ping) for offline or first-time setups.
If `config.resolve_mongo_connection()` itself raises `ConfigError`, rotation fails loudly (`RotationError`, exit `1`)
without repointing rather than pinging or guessing.

**Old-certificate retention (pruning):** each rotation leaves another `X509-cert-*.pem` (a live private key) in
`~/.bookscrapper/`, so after a successful repoint the command prunes superseded ones via
`cert_rotation.prune_old_certificates()`, keeping only the newest `--keep N` (default `2`, a custom argparse type that
rejects `0`/negatives so pruning can never delete every cert) plus the currently-active cert. The `protected` set is
{the cert just written} ∪ {the cert `config.resolve_mongo_connection()` resolves **after** repointing} ∪
{`TLS_CERT_FILE` if set}; candidates and protected paths are compared canonically (`Path.resolve()`) so a symlink-,
relative-, or `..`-spelled reference to the active cert is never deleted. Candidates come **only** from
`config.CONFIG_DIR.glob("X509-cert-*.pem")` — a `--output` cert written elsewhere is never pruned — and the matched
glob path itself is unlinked (a symlink candidate's target outside the dir is left alone); a failed unlink is a
`WARNING:` status line, not a crash. Pruning is **skipped entirely** (with a status line) if the active cert cannot be
resolved (`ConfigError`) — never prune on uncertainty — and never runs after a failed verification. `--no-prune`
disables pruning. `--no-verify` also suppresses pruning: without a positive authentication signal, old certs are kept
to avoid a self-inflicted lockout (pruning still runs on a verified success and on the "no URI configured" skip, where
there is no identity to lock out of).

After a successful (or skipped) verification the command also prints the **new** cert's expiry date (parsed back from
the just-written file via `cert_rotation.read_certificate_expiry()`); this line sits right after the
`Certificate written to <path>` line and before the verification/repoint status lines.

**Certificate expiry inspection (`--check`) and the pre-flight expiry warning:** certificates expire after 1-24
months, so `bookscraper rotate-cert --check [--warn-days N]` (default `N = 30`) reports the **currently active**
cert's expiry without rotating. It resolves the same cert the app connects with
(`config.resolve_mongo_connection()`'s cert), prints `path`, `expires: <ISO date>`, and `days remaining: <n>`, and
is a **pure local file read** — it never contacts Atlas and never needs the `ATLAS_*` variables, so it's safe as a
cron/CI health check. Its exit codes are: **`0`** if more than `N` days remain (or no x509 cert is configured — a
no-op), **`3`** if the cert expires within `N` days (so cron/CI can alert), **`1`** if the cert is expired or
unreadable. Exit code `3` is dispatched through `main.py` via `sys.exit(3)` in `commands/rotate_cert.py`, the same
`SystemExit`-propagation path as the existing `1`. Separately, `resolve_store_backend("mongo")` runs a non-raising
pre-flight helper `cert_rotation.cert_expiry_warning(cert_path, warn_days=30)` and `print_log`s any warning *before*
connecting; a parse failure there is a warning, never a crash. Both paths parse **only the first `CERTIFICATE` PEM
block** of the combined key+cert file (via the `cryptography` package, a direct dependency) — the private-key block
is never read or logged, and a missing file / absent-or-unparseable certificate raises `RotationError` naming only
the file path, never its contents.

**Status of the `certificate validation failed` (Atlas error 8000) report — UNVERIFIED, do not treat as settled.**
Earlier revisions of this file stated as fact that Atlas rejects this cluster's X.509 certificates, and that the
failure is server-side rather than a client- or resolver-side bug. That conclusion is not currently reproducible and
was never backed by a recorded run: it entered the docs during a restructuring commit (`59b5675`), and the tests
cited as its evidence do not demonstrate it. As of 2026-09-23, on this machine:
- `~/.bookscrapper/` contains only `logs/` — there is **no `settings.json` and no `X509-cert-*.pem`**, so no client
  certificate is ever presented to Atlas.
- `TEST_MONGODB_URI_PASS` / `TEST_MONGODB_URI_TLS` are unset, so all 8 `tests/integration/` tests **skip** at the
  first gate; none fail and none reach Atlas. The `xfail(strict=False)` markers on the `x509` legs are inert.

The root cause is therefore still open — re-diagnose from a real run before repeating the "server-side issue" claim.
Two things to know when credentials return: those `xfail` markers would silently absorb a genuine *client-side*
failure (the "silently staying green" outcome the Testing section warns against), and X.509 auth requires the URI to
carry `authMechanism=MONGODB-X509` (with `authSource=$external`), since pymongo never infers the mechanism from a
client certificate — `database.uri_selects_x509_auth()` now logs a diagnostic when a cert is paired with a URI that
omits it.

## Storage backend

`--store-backend {mongo|json}` is **required** on both subcommands and picks a single backend used for *both*
reads (duplicate checks) and writes (saving scraped books) — there is no dual-write and no implicit default, so
which store a run touched is always explicit from its command line. `src/bookscraper/backends/storage.py` defines
the contract (`StorageBackend.save_books()` / `.book_exists_by_hash()` / `.book_exists_by_asin()` /
`.leanpub_book_exists()` / `.close()`) and `resolve_store_backend(name)`, which runs a pre-flight check for the
requested backend (MongoDB ping, or local write-permission test) and exits the process if it's unusable — the same
"explicit, no ambiguous fallback" approach as `config.resolve_mongo_connection()` (see Authentication configuration
above). `backends/__init__.py` re-exports `StorageBackend`, `MongoBackend`, `JsonBackend`, and
`resolve_store_backend` for `from ..backends import ...` elsewhere in the package.

Both backends store one canonical document shape — `src/bookscraper/models.py`'s schema-2 `BookRecord`
(`@dataclass(frozen=True, slots=True)`, `SCHEMA_VERSION = 2`, stdlib only, no pydantic), whose `to_document()` is the
exact dict stored and `from_document()` its strict inverse. Its field table, a worked example per site, and the v1→v2
mapping live in [`docs/scraping/book-record-schema.md`](/docs/scraping/book-record-schema.md). As of task 03.1
subtask 01 the model and docs exist but scrapers and backends still read/write the v1 shapes; subtasks 04/05 wire
`BookRecord` into the scrape and save paths.

`save_books()` returns a frozen `SaveSummary` dataclass (defined in the leaf module `backends/save_summary.py`, so
both backends can import it at module level without a cycle, and re-exported by `storage.py`) — the
identical shape from both backends — with `inserted` / `duplicates` / `errors` counts and a `total` property. This
lets a caller tell a 100%-duplicate batch apart from a total failure (the old `None` return could not), and both
scraping subcommands log it and treat `summary.errors > 0` as the partial-failure signal. The mongo backend writes
in a single `insert_many(docs, ordered=False)` over per-document *copies* (so pymongo's in-place `_id` assignment
never mutates the caller's dicts), pre-validating each document with `bson.BSON.encode` so one bad document is one
error rather than an aborted batch, and returns `SaveSummary(0, 0, len(books))` immediately when the collection is
unavailable.

- **`mongo`** (`storage.MongoBackend`) — thin wrapper over `backends/mongo/database.py`'s existing MongoDB
  functions (`save_books_to_mongodb`, `check_book_exists_in_db`, `check_amazon_asin_exists_in_db`) plus
  `backends/mongo/deduplicate.py`'s `leanpub_prescrape_deduplicate`.
- **`json`** (`storage.JsonBackend`, backed by `backends/local/store.py`) — a local `books.json` at the repo root:
  one JSON array of book documents, schema-identical to what a MongoDB `insert_one` would receive. Unlike the CSV
  output it replaces (a disposable, per-run-overwritten dump that was never read back), `books.json` **persists
  and accumulates across runs**, and enforces the same uniqueness-by-`hash` semantics as MongoDB's unique index —
  a book saved in one run is still there, and still correctly detected as a duplicate, in the next. This is what
  makes the two backends behave identically from the caller's perspective, and what "local storage matches
  database content one-to-one" means in practice. Writes are atomic (temp file + `fsync` + `os.replace`, via
  `book_utils.atomic_write_bytes`) with a one-generation `books.json.bak` backup taken before each replace, so an
  interrupt mid-write never truncates the store; and a store that exists but isn't valid JSON is refused rather
  than overwritten — `save_books`/pre-flight raise `store.LocalStoreCorruptError` (surfaced by
  `resolve_store_backend('json')` as a `print_log` error + `sys.exit(1)` before any scraping) instead of silently
  discarding its books. `JsonBackend` also caches an in-memory `hash`/`asin`/`book_id`/`slug` index, built once and
  kept current by `save_books`, so repeated dedup lookups don't re-parse the file.
- Diagnostic reports that aren't database content — `failed_urls.csv` / `failed_books.csv` / `other_links.csv` —
  are unrelated to `--store-backend` and are always written as CSV best-effort by both subcommands, since MongoDB
  itself never stores failed/skipped attempts either.

**Migrating a store from v1 to v2 (`bookscraper migrate`).** Documents written before task 03.1 carry the per-site
*v1* shapes (`about_the_book`/`categories`/`last_published_at`/`leanpub.com`, raw `publication_date`, `"N/A"` ISBN
sentinels, no `schema_version`); the canonical schema-2 `BookRecord` (`models.py`, `docs/scraping/book-record-schema.md`)
is what every site now writes. `bookscraper migrate --store-backend {mongo|json} [--apply]` (`commands/migrate.py`,
backed by `backends/migration.py`) rewrites a store's v1 documents in place to v2. It reuses `resolve_store_backend`,
so the same pre-flight checks and explicit-backend rule apply, and it never touches book data on the *other* backend.
The v1→v2 field transform is `scraping/normalize.from_v1_document` — a stored v1 document is just another raw input
to the same per-site normalizers a fresh scrape uses, so a migrated record is byte-for-byte what a fresh scrape would
produce. `backends/migration.py` stays within the one-way import direction (`backends` never imports `scraping`) by
taking that transform as an injected `Callable[[dict], dict]`; `commands/migrate.py` (which may import both layers)
wires in the concrete function. Key guarantees: **dry-run by default** (prints a per-field change summary and writes
nothing; `--apply`
writes); **hash recomputation** — per the 2026-10-06 decision the migration recomputes `hash_book(title, authors,
publication_year)` for *every* document (author de-duplication is part of the hash input, so a document repeating a
name case-insensitively earns a new hash), rewrites changed hashes, and reports each one (old → new, with the
document id); a recomputed hash that **collides** with another document's hash is left untouched and reported for a
human (never silently overwritten or merged); **idempotent** (re-running `--apply` on v2 documents changes nothing);
and it **never deletes** a document (invalid-after-migration documents are reported and left verbatim). The `json`
backend goes through `store`'s atomic write with the one-generation `books.json.bak` backup; the `mongo` backend
applies guarded `ReplaceOne({"_id": id, "schema_version": {"$ne": 2}}, new_doc)` in `bulk_write` batches of 500,
never a bulk collection replace. A `migration-report-<timestamp>.json` (counts per change type, `changed_hashes`,
`hash_collisions`, `invalid_documents`, `field_changes`) is written to `~/.bookscrapper/logs/` in both modes; the
console/log output carries only ids/hashes/counts (no credential, URI, or document body — a collision's title appears
only inside the report file). Exit `1` on a corrupt local store; otherwise `0`. It takes no scraping flags and never
writes `parameters.py`.

## Running

```bash
# `uv sync` (see Setup above) already registers the `bookscraper` console script inside .venv.

# scrape-urls: scrapes a fixed list of URLs from a CSV (works for all 4 sites via Playwright).
uv run bookscraper scrape-urls -f content/urls.csv --store-backend mongo
uv run bookscraper scrape-urls -f content/urls.csv --store-backend json

# search: searches sites for books matching queries defined in parameters.py, then scrapes details.
uv run bookscraper search --store-backend mongo
uv run bookscraper search --store-backend json --max-search-pages 5

# rotate-cert: issues a new Atlas X.509 client certificate (needs ATLAS_* credentials, see Authentication
# configuration). Unlike the two above it takes no --store-backend and never touches the book data.
uv run bookscraper rotate-cert
uv run bookscraper rotate-cert --months 12 --output ~/.bookscrapper/X509-cert-manual.pem
# By default the new cert is pinged against Atlas before settings.json is repointed; on failure
# settings.json is left untouched and the command exits 1. Tune retries or skip the check entirely:
uv run bookscraper rotate-cert --verify-attempts 8
uv run bookscraper rotate-cert --no-verify              # offline / first-time setup: repoint without pinging
# After a successful rotation, superseded X509-cert-*.pem files are pruned, keeping only the newest
# --keep N (default 2) plus the active cert. Retain more, or keep them all:
uv run bookscraper rotate-cert --keep 5
uv run bookscraper rotate-cert --no-prune               # keep every certificate on disk

# rotate-cert --check: report the active cert's expiry only (no Atlas contact, no ATLAS_* needed).
# Exit 0 if >N days remain (or no x509 cert configured), 3 if within N days, 1 if expired/unreadable.
uv run bookscraper rotate-cert --check
uv run bookscraper rotate-cert --check --warn-days 14

# detect-schema: AI-assisted CSS-selector proposals for a site's detail page (dev-maintenance tool).
# Takes no --store-backend and never touches book data; needs ANTHROPIC_API_KEY. See AI-assisted schema
# detection below.
uv run bookscraper detect-schema --site amazon --url "https://www.amazon.com/dp/1098131029"
uv run bookscraper detect-schema --site leanpub --url "https://leanpub.com/some-book" --output schema-report.txt
uv run bookscraper detect-schema --site amazon --url "https://www.amazon.com/dp/1098131029" --model claude-sonnet-5 --max-input-tokens 30000

# migrate: rewrite stored v1 book documents to the canonical schema-2 shape, recomputing each
# document's hash. Dry-run by default (prints a per-field change summary and writes NOTHING to the
# store); pass --apply to write. Idempotent (a second --apply changes nothing) and never deletes a
# document; a recomputed hash that collides with another document is left untouched and reported for
# a human. A migration-report-<timestamp>.json is written to ~/.bookscrapper/logs/ either way.
uv run bookscraper migrate --store-backend json            # dry run (report only)
uv run bookscraper migrate --store-backend json --apply    # write the migrated books.json
uv run bookscraper migrate --store-backend mongo --apply   # write the migrated Atlas collection

# Without `uv run` (inside an activated .venv), or via `python -m` directly:
python -m bookscraper scrape-urls -f content/urls.csv --store-backend json
python -m bookscraper search --store-backend json
python -m bookscraper rotate-cert
python -m bookscraper detect-schema --site oreilly --url "https://www.oreilly.com/library/view/x/9780123456789/"
python -m bookscraper migrate --store-backend json --apply
```

`--store-backend` is required — omitting it is an argparse error, not an interactive prompt (there is no more
`(C)/(M)/(B)/(E)` choice: see Storage backend above for why the choice is always explicit).

## AI-assisted schema detection

`bookscraper detect-schema` (`commands/detect_schema.py`, backed by `scraping/schema_detection.py`) automates the
otherwise-manual loop of figuring out a site's CSS selectors when its markup drifts — see
`docs/scraping/schema-detection.md` (full manual-and-automated writeup) and
`docs/adr/0002-ai-assisted-schema-detection.md` (design rationale). It takes `--site
{amazon,packtpub,leanpub,oreilly}`, `--page-type {detail,search}` (default `detail`), one or more repeatable `--url`
sample pages, an optional `--output`, `--model` and `--max-input-tokens`, `--format {text,json}` (default `text`) and
`--check-current-only` (see JSON report & drift exit codes below), and `--log-severity` like the other subcommands; it
does **not** take `--store-backend`.

The pipeline: load each URL with Playwright (reusing `scrape_details.route_handler` document-only blocking) → prune
each page to an LLM-sized HTML snapshot → ask Claude (official `anthropic` SDK, `ANTHROPIC_API_KEY` read straight
from the environment/`.env`) to propose a CSS selector + rationale per existing `site_constants[site]` field
→ re-query every proposal against **each** sample page's live DOM, flagging any field that matches on some sample
URLs but not others → emit a diff-style report to stdout (and to `--output` if given), followed by a one-line usage
summary.

`--page-type` picks the field set and validation. `detail` (the default) proposes selectors for the detail-page
fields (`detail_selector_fields`, excluding every `SEARCH_*` key) and validates each page-level. `search` proposes
selectors for the `SEARCH_*` fields (`search_selector_fields`, i.e. all `SEARCH_*` keys minus the non-selector
constants in `parameters.NON_SELECTOR_SEARCH_KEYS` — `SEARCH_BASE_URL`/`SEARCH_BASE_API`/`SEARCH_MAXIMUM_PAGES`/
`SEARCH_ASIN_ATTRIBUTE`; note `SEARCH_NO_RESULTS_TEXT`/`SEARCH_NO_BOOKS_FOUND` ARE kept, they select the no-results
marker element). The search prompt variant explains the **card-relative** semantics: the card container
(`SEARCH_BOOK_CARD`/`SEARCH_RESULT_ITEM_SELECTOR`) and the pagination/no-results selectors are page-level, every other
`SEARCH_*` field is evaluated inside each card. Search validation enumerates the cards per sample and reports, per
card-relative selector, the card count and the fraction of cards it matched — under 80% is flagged *partial*.
`parameters.NON_SELECTOR_SEARCH_KEYS` is the single source of truth this classification shares with
`scripts/capture_fixtures.py` so the two can't drift.

Model, budget and cost controls (subtask 04.0/05): the default model is **`claude-sonnet-5`** (precedence `--model`
flag > `ANTHROPIC_MODEL` env > default; an empty ID is rejected). Sonnet 5 runs adaptive thinking by default and
thinking tokens count toward `max_tokens`, so `DEFAULT_MAX_TOKENS` is `16000` (not 2048) and no sampling params or
`budget_tokens` are sent; a `max_tokens` or `refusal` stop reason raises rather than being parsed. Each run sends
**one request per sample URL**, sharing a cacheable static instruction block (site + field list + output contract)
while the per-page snapshot is the uncached user message — note the static block is below Sonnet 5's 1024-token
minimum cacheable prefix, so the marker is harmless but will not actually cache. A per-request **pre-flight token
budget** (`MAX_INPUT_TOKENS` = 40000, overridable via `--max-input-tokens`) counts tokens with the SDK's
`count_tokens`; an over-budget prompt is re-pruned toward a 10000-character HTML floor and abandoned
(`SchemaDetectionError`, nothing sent) if it still will not fit. Hard cost ceilings: **≤ 5 sample URLs per run**
(rejected before any network/API call) and **one retry** on a transient error (the client's `max_retries` matched to
that, never stacked). The closing usage line reports model, summed input / cache-read / cache-creation / output
tokens and the request count — **no prices, and never the API key**; SDK errors surface only the exception class and
HTTP status, never the response body.

Two hard rules mirror the project's existing conventions: it **never writes `parameters.py`** (propose, never
auto-apply — a human applies changes by hand, the same "automation proposes, a human/deterministic step decides"
restraint as the config/rotation paths above), and it **fails loudly** — a missing `ANTHROPIC_API_KEY`, a page-load
failure, or a malformed/non-JSON AI response each raise `SchemaDetectionError`, surfaced as a `print_log` error and
a non-zero exit, never partial output. `anthropic` is a direct dependency in `pyproject.toml`; the test suite always
mocks the client and never makes a real API call.

**Offline modes (automation is OFFLINE-ONLY; CI makes no live Anthropic call).** The whole pipeline can run with
**zero network and no API key**, which is how the golden tests run in CI:
- `--html-file PATH` (repeatable, mutually exclusive with `--url`) uses a local HTML file as a sample page. It is
  read with a 400 KB size cap (aligned with the fixture-corpus per-file budget) as UTF-8 — missing/oversized/non-UTF-8
  fails loudly — and loaded via `page.set_content()` with **every** request aborted (default-deny, stricter than
  `route_handler`: even document/subresource requests are blocked). The pruned snapshot comes from the file bytes, so
  it (and the replay key below) is deterministic and Chromium-build-independent.
- `--replay PATH` serves the model step from a recorded JSON file of `{sha256(prompt): raw_response}`; in replay mode
  **no Anthropic client is constructed and `ANTHROPIC_API_KEY` is not required**, the `count_tokens` pre-flight is
  skipped, and the usage line reports zero tokens / N replayed. A prompt missing from the file is a hard error (no
  silent fall-through to a live call). Loader ignores `_`-prefixed keys.
- `--record PATH` writes a live run's responses (keyed by `sha256(prompt)`) for a maintainer to curate into a replay
  file; ignored in replay mode, never contains the key.

The committed replay recordings `tests/fixtures/schema_detection/<site>.replay.json` are **hand-authored** (plausible
model JSON following the exact `validate_ai_response` output contract, carrying a `_provenance` note), with keys
generated from the real prompt builders by `scripts/generate_schema_replays.py` (never hand-computed); the
`.report.txt` goldens beside them are regenerated with `pytest --update-goldens` (rewrite-then-fail). These are
derived artifacts, not captured pages, so `tests/unit/test_fixture_corpus.py` exempts the `schema_detection/` subtree
from the `.meta.json`/sha256 integrity checks. The golden/pruner-regression tests
(`tests/mock/scraping/html/test_schema_detection_goldens.py`) carry the `chromium` marker and skip cleanly without a
browser.

**JSON report & drift exit codes (subtask 04.0/08).** Every run — text or JSON, AI or check-only — **also re-queries
each current `site_constants` selector** against the samples (`current_matches`) and classifies it with a per-field
`status` via `compute_field_statuses()`, so the report shows which existing selectors have genuinely broken, and
`detect_schema()` returns `(report, usage_line, exit_code)`. The user-ratified drift rule (2026-10-02): `status` is
`ok` (matched somewhere), `broken` (non-empty, non-optional selector matching no sample — for an `*_ALT` fallback,
only when its primary `X` (suffix stripped) is ALSO broken), `fallback_unused` (an `*_ALT` whose primary is healthy),
or `absent_optional` (a key in `parameters.OPTIONAL_SELECTOR_KEYS` — `DETAILS_BUTTON`/`READ_MORE_LINK`/
`ASIN_DETAIL_PAGE_SELECTOR`/`SEARCH_NEXT_PAGE_SELECTOR`/`SEARCH_NO_RESULTS_TEXT`/`SEARCH_NO_BOOKS_FOUND` — or a field
the site leaves unset). `summary.current_broken` counts **only** `broken`. Exit codes: `0` nothing `broken`; `4`
**drift** — at least one `broken` field; `1` any error (`SchemaDetectionError`). `OPTIONAL_SELECTOR_KEYS` is kept
minimal: anything the scraper NEEDS to extract a record (title, authors, ISBN-13, publication date, description, the
search card + its title/detail-link) stays gating. `commands/detect_schema.py`
propagates exit `4` via `sys.exit(4)` the same way `rotate-cert --check` propagates `3` (`main.py` lets it bubble
through `asyncio.run`). `--format {text,json}` (default `text`) picks the report: `json` emits a machine-readable
document (see `format_json_report`) and keeps **stdout a single parseable JSON object** for a CI drift job, routing
every human status line to stderr. `--check-current-only` (AIS-9 hard gate) skips the AI step **entirely** — no model
resolved, no `ANTHROPIC_API_KEY` read, no Anthropic client constructed, no `count_tokens` pre-flight — and evaluates
only the current selectors; paired with `--html-file` it makes **zero** network calls, the basis for the OFFLINE-ONLY
scheduled drift job (status.md 2026-10-02). In its JSON each field's `proposed`/`rationale`/`changed` and the
top-level `model`/`summary.changed` are `null` and `matches` is `{}`. Security: the JSON carries no key and no page
HTML (AIS-8/AIS-12) — only bounded match counts, ≤80-char DOM snippets, and a rationale truncated to 200 chars; and
`sanitize_untrusted()` is the single place that strips ESC/ANSI and C0/C1/DEL control characters from **every**
untrusted string (rationale, proposed selector, DOM snippets, sample identifiers) in **both** the text and JSON
reports (AIS-13).

**Prompt-injection hardening (subtask 04.0/09; threat model `docs/security/2026-10-02-ai-schema-detection.md`).**
The pruned page HTML is untrusted input spliced into the prompt, so: the snapshot is fenced in
`<page_html>...</page_html>` and the instruction block declares its contents untrusted *data* whose own instructions
must be ignored, with any literal `<page_html>`/`</page_html>` inside it (any case/whitespace) angle-bracket-escaped so
the fence can't be broken out of (AIS-1, same class as `sanitize.py`'s V-3). Cross-sample DOM validation stays
mandatory and a proposed selector's **only** sink is `page.locator(sel).count()`/`.text_content()` (or
`card.locator(...)`) — never eval/shell/`page.evaluate`/file-write (AIS-2/AIS-4). Every AI-proposed selector is run
through `reject_selector()` in `validate_ai_response` **before** it is queried, and a failing one is **downgraded to a
null selector with rationale `rejected: <rule>`** (never raised): rules reject >300 chars, the universal
`*`/`html`/`body`/`:root`, `javascript:`/`<`/newline (AIS-5 — `<` rejects markup, but a single `>` CSS child
combinator is ALLOWED; only `>>` is rejected), plus Playwright engine prefixes
(`xpath=`/`text=`/`id=`/`data-testid=`/`css=`/`react=`/`vue=`/generic `<ident>=`/colon-qualified `internal:…=`), any
`internal:` engine anywhere, `>>` chaining, pseudo-engines (`:has-text`/`:has(`/`:visible`/`:nth-match`/`:text(`/
`:light(`) and a `/regex/` form appearing anywhere OUTSIDE a quoted span (so `[href="/a/b/"]` is not a false
positive) (AIS-6; the engine-prefix/`internal:`/regex-anywhere widening is as-built follow-up PI-1). Every
`count()`/`text_content()`/`.all()` runs under an explicit `SELECTOR_EVAL_TIMEOUT_S` wall-clock bound via
`asyncio.wait_for`, so a pathological selector degrades to a non-match instead of hanging (AIS-7); search validation
also caps the page-controlled card count at `MAX_CARDS_PER_SAMPLE` (50), computing the match fraction over the
evaluated cards. `sanitize_untrusted()` additionally strips U+2028/U+2029 (PI-3) and `_FENCE_TAG_RE` neutralizes
attributed fence tags like `</page_html foo="x">` (PI-2). Both reports end with a fixed untrusted-content review
reminder — a footer line in the text report and a `notice` key in the JSON (AIS-15).
**AIS-6 trade-off:** these rules reject AI *proposals* only — including the project's own `:has-text()` (amazon/
packtpub/oreilly) selectors, which therefore show as `rejected` in a run's proposals (the `>` child combinator used by
the amazon search selectors is NOT rejected — see the amended `<`/`>` rule) — but **never** the current
`site_constants` selectors the drift check re-queries, so `--check-current-only` and the `current_matches`/`status`
columns still evaluate `:has-text` selectors verbatim and report them `ok`/`broken` normally. A human applies a
rejected selector by hand.

## Testing

`tests/` is a real pytest suite in three tiers, mirroring `src/bookscraper`'s subpackages:

- **`tests/unit/`** — pure logic, no mocks: `cli.py`'s `build_parser()`, `book_utils.py`'s
  `hash_book`/`extract_year_from_date`/`check_local_write_permission`, `backends/mongo/config.py`'s
  resolver (all six `settings.json` shapes plus the legacy fallback, purely as resolution logic — no
  network), `backends/mongo/cert_rotation.py`'s months validation / atomic write / `settings.json`
  repointing plus `read_certificate_expiry` / `cert_expiry_warning` (throwaway self-signed certs generated
  in-test via `cryptography`, including a private-key-never-logged leak test — real file I/O via `tmp_path`),
  `backends/local/store.py` (real file I/O via `tmp_path`), `scraping/parameters.py`
  data sanity checks, and `scraping/schema_detection.py`'s pure logic (HTML pruning/truncation, AI-response
  shape validation, diff-report formatting).
- **`tests/mock/`** — the rest of `src/bookscraper`, with every external dependency mocked
  (`pymongo.MongoClient`, `httpx.AsyncClient`, Playwright's `Browser`/`Page`/`Locator` via
  `tests/mock/scraping/playwright_fakes.py`): `backends/mongo/database.py`, `backends/mongo/deduplicate.py`,
  `backends/mongo/cert_rotation.py`'s Atlas API calls (`httpx.post` mocked), `backends/storage.py`, `scraping/scrape_details.py`, `scraping/search_utils.py`,
  `scraping/schema_detection.py` (Playwright faked, `anthropic` client mocked — never a real API call),
  `commands/scrape_urls.py`, `commands/search.py`, `commands/rotate_cert.py`, `commands/detect_schema.py`, and `main.py`.
  `tests/mock/scraping/html/` is the **offline fixture-serving harness** (task 06.0): its `conftest.py` turns the
  committed `tests/fixtures/` corpus (real captured/hand-built HTML + Leanpub JSON, mapped by each fixture's
  `.meta.json` `url`) into a default-deny offline environment for the *unmodified* scraper. `corpus_router(browser,
  mapping)` wraps a real headless Chromium so every page serves the mapped corpus bytes and **aborts + records +
  fails the test** on any unmapped request (threat model FIX-8), with corpus routes installed *after* the production
  `route_handler` so they take precedence and an unmapped document is never `route.continue_()`d to the network
  (FIX-9); `httpx_corpus(mapping)` does the same for the Leanpub httpx path by injecting an `httpx.MockTransport`
  (no production change — a same-signature `httpx.AsyncClient` factory). The Chromium-backed tests carry the
  `chromium` marker (registered in `pyproject.toml`, **included in the default run** — offline and fast) and pick
  their browser via the `BOOKSCRAPER_CHROMIUM_PATH` env var (a system Chromium, for macOS 13 where Playwright's
  bundled Chromium won't launch), falling back to the bundled browser and **skipping cleanly** (never failing) when
  no Chromium is usable, so CI can install one (task 06.2). On top of that harness,
  `tests/mock/scraping/html/test_detail_goldens.py` is the **detail-page golden (characterization) tier**
  (task 06.0, subtask 04): it runs the unmodified `scrape_book()` / `get_leanpub_book_details()` against every
  non-edge-case fixture and compares the result field-by-field against a committed
  `tests/fixtures/**/<name>.expected.json`. Goldens record what the code does **today** (including known-wrong
  output — e.g. packtpub/oreilly currently return `(None, "FAILED")` from a `KeyError` on the missing `BASE_URL`,
  and amazon records a raw `publication_date`, un-normalized ISBNs, and no `tags`); each such value carries a
  `"_known_issues"` list naming the 03.0/03.1 subtask that will fix it (that key is ignored by the comparison).
  It is split into `test_detail_golden` (the Playwright sites, `chromium`-marked) and `test_detail_golden_leanpub`
  (the Leanpub JSON path via `httpx_corpus` only, unmarked so it runs offline without Chromium).
  Regenerate with `uv run pytest --update-goldens` (registered in `tests/conftest.py`), which rewrites the
  `.expected.json` files and then **fails** the run with `"goldens updated - review the diff"` so a regeneration is
  never silently green.
- **`tests/integration/`** — hits real MongoDB Atlas, tagged `@pytest.mark.integration` and excluded
  from the default run (`addopts = -m "not integration"` in `pyproject.toml`); run explicitly with
  `uv run pytest -m integration`. `test_connection_password.py` / `test_connection_tls.py` verify
  SCRAM/X.509 connectivity directly; `test_connection_settings.py` integration-tests the real
  `config.resolve_mongo_connection()` resolver (see Authentication configuration above) across all
  six valid `settings.json` shapes using a temporary settings file per scenario (the real
  `~/.bookscrapper/settings.json` is never touched). Expected outcome: both `pass` shapes resolve and
  connect; all four `x509` shapes resolve to correct values but the live connection currently fails
  with Atlas's known `certificate validation failed` error (see Authentication configuration) — those
  legs are `@pytest.mark.xfail(strict=False)`, so they report as expected/non-blocking today and
  would surface as `XPASS` the day that's fixed on the Atlas side, rather than silently staying green
  forever or hard-failing the suite.

**Coverage target:** `uv run pytest -m "not integration" --cov=bookscraper --cov-report=term-missing --cov-fail-under=90`
enforces ≥90% line coverage on `src/bookscraper` (`scripts/*.py` is out of scope). The two
Playwright-DOM-heavy functions — `scrape_details.py`'s `scrape_book()` and `search_utils.py`'s
`get_search_results_via_playwright()` — get happy-path-plus-primary-error-branch coverage rather
than exhaustive per-selector coverage; everything else targets ~100%.

The real-HTML corpus (`tests/fixtures/`, task 06.0) also drives **golden** tests through the
offline harness: `tests/mock/scraping/html/test_detail_goldens.py` (detail pages) and
`test_search_goldens.py` (search pages + the Leanpub search API). The search goldens run the
unmodified `get_search_results_via_playwright` against **live-captured** Amazon search snapshots
(Amazon search is not walled) in real headless Chromium (`@pytest.mark.chromium`, skips without a
usable browser) and `get_leanpub_search_results_via_api` over `httpx_corpus` (unmarked, offline).
Both goldens live in `tests/mock/scraping/html/goldens/` (outside `tests/fixtures/`, so diffable
and not subject to the corpus orphan check) and carry `_known_issues` notes linking live
selector/pagination drift to subtask 03.3; regenerate with `--update-goldens`.

When a target changes its markup, `scripts/capture_fixtures.py --refresh --site <site>` re-captures
that site's fixtures into a temp dir and reports selector/golden drift (`--apply` is the sole in-tree
write path, re-running sanitize + `assert_clean` + budgets; it refuses to overwrite a hand-built
fixture with a walled page). Its pure comparison helper `compare_selector_matches` is unit-tested in
`tests/unit/scraping/test_fixture_drift.py`. The full runbook is
[`docs/test/html-fixtures.md`](docs/test/html-fixtures.md).

**Shared fixtures** (`tests/conftest.py`, used by `unit/` and `mock/`): `isolated_config` and
`isolated_local_store` monkeypatch `backends/mongo/config.py`'s `CONFIG_DIR`/`SETTINGS_FILE` and
`backends/local/store.py`'s `LOCAL_STORE_FILE` to `tmp_path`, so a test can never read or write the
real `~/.bookscrapper/settings.json` (it holds real credentials) or the repo-root `books.json`.
`no_real_env` strips MongoDB env vars for a test's duration so a prior `load_dotenv()` can't leak
real values in. `tests/conftest.py` also registers the `--update-goldens` option (and matching
`update_goldens` fixture) used by the detail-page golden tier above, and enables the `pytester`
plugin (used by `test_update_goldens_option_rewrites_and_fails` to prove the rewrite-then-fail
contract in an isolated run that never touches the real goldens). `tests/integration/conftest.py`
additionally provides `require_env(name)`, which skips (not fails) a test when a required `.env`
credential is absent.

Test credentials (`tests/integration/` only) come from `TEST_MONGODB_URI_PASS` / `TEST_MONGODB_URI_TLS`,
set in `.env` (repo root, git-ignored) — env-var only, no fallback file
(`tests/integration/mongo_test_helpers.py`'s `get_mongodb_uri()` takes no fallback path), so a bug in
either the production resolver or a test can never cross-load the other's credentials. The TLS test
still reads the TLS *cert* from the production location (`TLS_CERT_FILE` / the newest
`X509-cert-*.pem`) rather than a test-only copy, since there is one real X.509 identity, not a
separate test one.

**Credential separation (test vs. production):** production auth lives in `~/.bookscrapper/settings.json` (see
Authentication configuration above). Test-only driver strings are `TEST_MONGODB_URI_PASS` / `TEST_MONGODB_URI_TLS`,
set in `.env` (repo root, git-ignored) — env-var only, no fallback file (`mongo_test_helpers.get_mongodb_uri()`
takes no fallback path), so a bug in either the production resolver or a test script can never cross-load the
other's credentials, and there's no separate `~/.bookscrapper/test/` subtree to keep in sync. The TLS test script
still reads the TLS *cert* from the production location (`TLS_CERT_FILE`, else the newest `X509-cert-*.pem` by mtime
via `mongo_test_helpers.get_default_tls_cert_file()`) rather than a test-only copy, since there is one real X.509
identity, not a separate test one. That resolution must stay a **glob for the newest file, never a pinned
filename** — it was pinned to a single manually-downloaded cert until 2026-09-23, which meant a rotation stranded
the tier on a name that no longer existed and it skipped instead of testing the current identity
(`tests/unit/test_integration_helpers.py` guards this).

## Architecture

`src/bookscraper/` is organized into four subpackages by responsibility, plus a small set of root-level entry
points and cross-cutting utilities. Import direction is one-way and there are no cycles:
`commands/` → `scraping/` + `backends/`; `scraping/` → `backends/` (for the `StorageBackend` type) + `book_utils.py`;
`backends/` → `book_utils.py`; nothing under `backends/` or `scraping/` ever imports from `commands/`.

Root-level entry points and shared utilities:
- **`cli.py`** — argparse only: builds the `bookscraper` parser and its four subparsers (`scrape-urls`, `search`,
  `rotate-cert`, `detect-schema`).
  Every flag sets an explicit `dest=` (e.g. `--input-file` → `input_file`) so a CLI flag can never silently drift
  from the attribute name the code reads.
- **`main.py`** — the real entry point: parses args via `cli.build_parser()` and dispatches to the matching
  `commands/*.py` module's `run(args)`. `main_sync()` wraps this in `asyncio.run(...)` and is what both the
  `bookscraper` console-script (registered in `pyproject.toml`'s `[project.scripts]`) and `__main__.py` call.
- **`__main__.py`** — enables `python -m bookscraper ...`; just calls `main.main_sync()`.
- **`book_utils.py`** — `print_log()` (color-coded console + plain-text file logging), `configure_logging(level)`
  (attaches a single `FileHandler` to the **root** logger, pointed at a fresh per-run file under
  `~/.bookscrapper/logs/` — see below), the local pre-flight check (`check_local_write_permission`),
  `extract_year_from_date` (handles several site-specific date formats), `hash_book`. Stays at the package root
  since every subpackage below imports from it and it has no backend/scraping-specific logic of its own.

**`commands/`** — the CLI workflows. The two scraping ones each call `resolve_store_backend()` (from `backends/`) once
up front; `rotate-cert` and `detect-schema` don't touch book storage at all:
- **`scrape_urls.py`** — the "I already have URLs" workflow: takes a CSV of book URLs, buckets them by site via
  `identify_website()`, and scrapes each in batches of 10 concurrent Playwright tasks via
  `scraping.scrape_details.scrape_book()`, passing it the resolved `StorageBackend` for inline duplicate checks.
- **`search.py`** — the "discover new books" workflow: for each site in `SITES_TO_SCRAPE` and each query in
  `SEARCH_QUERIES` (both in `scraping/parameters.py`), it searches for candidate books, deduplicates them against
  the resolved storage backend *before* doing the expensive detail scrape, then scrapes details for the survivors.
  Currently only Leanpub is wired end-to-end, entirely via its JSON API through `httpx` (no browser needed).
  `SITES_TO_SCRAPE` is currently `["leanpub"]`.
- **`rotate_cert.py`** — the "rotate the Atlas X.509 client certificate" workflow: loads `.env`, calls
  `backends.mongo.cert_rotation.rotate_certificate()` in a worker thread (it uses blocking `httpx`), reports the
  result through `print_log`, and `sys.exit(1)`s on a `RotationError`. See TLS certificate rotation pipeline above.
- **`detect_schema.py`** — the "propose selector-schema updates" workflow (dev maintenance, not scraping): loads
  sample detail-page URLs, asks Claude to propose CSS selectors, validates them against each page's live DOM, and
  prints a diff-style report. Takes `--site`, one or more repeatable `--url`, and an optional `--output`; no
  `--store-backend` (it never touches book storage) and it never writes `parameters.py`. `sys.exit(1)`s on a
  `SchemaDetectionError`. See AI-assisted schema detection above.

**`backends/`** — the storage-backend abstraction (see Storage backend above):
- **`storage.py`** — `StorageBackend` (the mongo/json contract), `MongoBackend`, `JsonBackend`, and
  `resolve_store_backend()`. `backends/__init__.py` re-exports all four.
- **`mongo/database.py`** / **`mongo/deduplicate.py`** — two layers of dedup for the `mongo` backend: (1)
  pre-scrape checks by `book_id`/`slug` (Leanpub) or `asin` (Amazon) to avoid re-scraping known books, and (2) a
  unique MongoDB index on a `hash` field (SHA-256 of normalized title+authors+year, computed by
  `book_utils.hash_book`) that rejects duplicate inserts at write time. `database.py` holds the MongoDB
  client/db/collection as module-level globals, lazily initialized by `get_mongo_collection()`; connection details
  come from `config.resolve_mongo_connection()`, not from reading env vars directly.
- **`mongo/config.py`** — `resolve_mongo_connection()`, the deterministic MongoDB auth resolver
  (`~/.bookscrapper/settings.json` when present, else the legacy env-var fallback) shared by `mongo/database.py`
  and `book_utils.py`. See Authentication configuration above.
- **`mongo/client.py`** — the single `MongoClient` factory: `build_mongo_client()` (identical TLS kwargs on every
  path, cert-validation never disableable, a missing client cert raising instead of silently dropping),
  `ping()` (re-raises as a redacted `MongoConnectionError` — class name + pymongo `code`/`codeName` only, never
  host/URI/credentials), and `resolve_server_selection_timeout_ms()` (honors `MONGODB_SERVER_SELECTION_TIMEOUT_MS`).
  The `uri_*` X.509 helpers and `_warn_if_x509_mechanism_missing` live here and are re-exported from `database.py`.
  Both `database._initialize_mongodb_connection` and `database.check_mongodb_connection` build through it, so
  `MongoClient(` appears exactly once in `src/`. See Single `MongoClient` factory above.
- **`mongo/cert_rotation.py`** — Atlas Admin API client for issuing a new X.509 client certificate
  (`rotate_certificate()`, raising `RotationError` on any failure), plus the atomic cert write and
  `update_settings_after_rotation()`, which repoints `settings.json` via `config`. Returns status lines instead of
  printing, so `commands/rotate_cert.py` owns all user-facing output. See TLS certificate rotation pipeline above.
- **`local/store.py`** — the `json` backend's implementation: `books.json` at the repo root, persistent and
  accumulating across runs, with the same `hash`-uniqueness and `asin`/`book_id`/`slug` lookup semantics as
  `mongo/database.py`'s MongoDB functions (`save_books`, `check_book_exists`, `check_amazon_asin_exists`,
  `leanpub_book_exists`).

**`scraping/`** — everything about talking to the four book sites:
- **`parameters.py`** — `site_constants`: per-site CSS selectors and API URLs (this is what to edit when a site
  changes its markup or a new site is added). Also `SEARCH_QUERIES`, `SITES_TO_SCRAPE`, `SCRAPE_FILTERS`
  (rating thresholds), `RATE_LIMITS`/`BOT_WALL` (task 03.2), `USER_AGENT_MODE`/`USER_AGENT_TEMPLATES` (05's native
  default vs. version-matched rotation), and `HEADLESS_BROWSER` (default `True` as of task 05.0/03 — headed mode
  is now an explicit `--no-headless` debugging choice; a scheduled/CI run must never try to open a window).
- **`scrape_details.py`** — per-site Playwright detail scraping (`scrape_book`) plus Leanpub's `httpx`-based
  `get_leanpub_book_details`. `route_handler` aborts all non-document requests (images/fonts/css) to speed up page
  loads.
- **`search_utils.py`** — collects search-result candidates per site: Leanpub via paginated API calls
  (`get_leanpub_search_results_via_api`), other sites via Playwright (partially implemented).
- **`schema_detection.py`** — the `detect-schema` engine (dev maintenance): loads sample detail pages with
  Playwright (reusing `scrape_details.route_handler`), prunes each to an LLM-sized HTML snapshot, asks Claude via
  the `anthropic` SDK to propose selectors for the existing `site_constants[site]` field names, re-queries every
  proposal against each page's live DOM (flagging selectors that match inconsistently across sample URLs), and
  formats a diff-style report. Reads `ANTHROPIC_API_KEY` directly from the environment (not via `mongo/config.py`'s
  `settings.json`, which is Mongo-auth-specific); fails loudly (`SchemaDetectionError`) on a missing key, a
  page-load failure, or a malformed AI response. Imports only from `scraping/` + `book_utils.py` — never
  `commands/` or `backends/`. See AI-assisted schema detection above.

## Logging

Every run writes to its own file: `~/.bookscrapper/logs/bookscraper_<YYYYMMDD_HHMMSS>_<pid>.log`, computed once as
`LOG_FILE_PATH` when `book_utils.py` is imported (so it's stable for the lifetime of the process even though
`configure_logging()` may be called more than once — see below). After creating a run's file, `_prune_old_logs()`
deletes the oldest files matching `bookscraper_*.log` beyond `MAX_LOG_FILES` (10), keyed on `st_mtime`, so the
directory never exceeds 10 run logs.

All application logging — regardless of which logger a module uses — lands in that file. Several modules create
their own logger via `logging.getLogger(__name__)` or an ad hoc name (`backends/mongo/database.py`,
`scraping/search_utils.py`, `backends/mongo/deduplicate.py`, `scraping/scrape_details.py`, `commands/search.py`)
rather than sharing `book_utils.py`'s `"bookscraper_app"`
logger, so `configure_logging()` attaches the file handler to the **root** logger instead of a specific named one —
every logger propagates there by default, so this is the only placement that reliably captures all of them.
`main.py` calls `configure_logging()` once at startup with the level from `--log-severity {debug|info|warning|error}`
(defined on `scrape-urls`, `search`, `rotate-cert`, and `detect-schema` in `cli.py`; default `info`). `book_utils.py` also calls
`configure_logging()` once at import time (default `info`) so the logger works for any code that imports it
without going through the CLI — this is safe to call twice per run since it only adds the `FileHandler` (and only
prunes old logs) the first time, keyed on `LOG_FILE_PATH` already being attached. Note: `print_log()`'s colored
console output writes to `sys.stdout` directly, bypassing the logging framework entirely — `--log-severity` only
filters what reaches the log file, not the console, which always prints everything passed to `print_log()`.

## Known inconsistencies to watch for
- Curated input URL lists (`content/urls.csv`, `content/urls_amazon.csv`) live under `content/` and are committed —
  they're hand-maintained candidate lists for `scrape-urls -f`, not run output. Actual run artifacts (`books.json`
  for `--store-backend json`; `failed_books.csv`, `failed_urls.csv`, `other_links.csv` diagnostics regardless of
  backend) are gitignored and written to the repo root by default; don't add new default output filenames without
  gitignoring them too. Unlike the diagnostics, `books.json` is meant to persist across runs — don't delete it
  casually, it's the entire point of the `json` backend (see Storage backend above).
- `init_template.py` and `release_package.py` are leftover from a generic cookiecutter-style Python module template
  (renaming a template project, building/publishing wheels, tagging GitHub releases). They aren't part of the
  scraping workflow and reference a generic `python_module` template name in places. They also both read/write
  `setup.cfg` directly (via `ConfigParser`), which no longer exists after the uv migration — running either script
  as-is will fail. Not fixed, since neither script is part of the actual scraping workflow.
