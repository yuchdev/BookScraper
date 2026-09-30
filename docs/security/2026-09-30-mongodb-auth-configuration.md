# Threat Model - MongoDB auth configuration (task 02.0, subtasks 04/05/06) - 2026-09-30

Pre-implementation threat model for branch `milestone/0001-generic-implementation`, task 02.0
subtasks 04 (honest connection logging), 05 (single `MongoClient` factory), 06 (settings.json
validation & hardening). Written before coding so coder briefings can cite the numbered
requirements below.

## Scope

- `src/bookscraper/backends/mongo/config.py` - the deterministic resolver + new permission/shape hardening (06).
- `src/bookscraper/backends/mongo/database.py` - `_initialize_mongodb_connection`, `get_mongo_collection` (04, 05).
- `src/bookscraper/backends/mongo/client.py` - **new** unified `build_mongo_client` / `ping` factory (05).
- `src/bookscraper/book_utils.py::check_mongodb_connection` - collapse into the factory (05).
- `docs/examples/settings.example.json` - **new** example (06).

Out of scope: `cert_rotation.py`, the scraping pipeline, storage backends.

## Assets & trust boundaries

- **Assets:** `MONGODB_URI` with an inline SCRAM username:password (literal in `settings.json` or via env);
  the X.509 client cert/key PEM (`TLS_CERT_FILE` / `x509.cert`) - filesystem read access to it == DB
  credentials; the `TLS_CA_FILE` bundle; `~/.bookscrapper/settings.json`; the per-run log files under
  `~/.bookscrapper/logs/` (up to 10 retained, root logger, every module propagates there).
- **Trust boundaries:** (1) `settings.json` / env / `.env` -> resolver -> `MongoClient` kwargs;
  (2) pymongo/OS exception objects (may embed host, URI, credentials) -> logger + `print_log` (console +
  persistent file); (3) the local filesystem permissions/ownership of `settings.json` and the PEM;
  (4) a `literal` cert path string -> `Path` resolution -> filesystem.

## Findings

### [HIGH] Credential/URI leakage through pymongo exception text into persistent logs (CWE-532, CWE-209)
- Vector / evidence: `database.py:172-190` (`f"Failed to connect...: {e}"`, `print_log(... Details: {e})`,
  and the `except Exception ... exc_info=True`) and `book_utils.py:223-236` both interpolate raw pymongo
  exceptions. `ServerSelectionTimeoutError` / `OperationFailure` / `ConfigurationError` messages routinely
  embed the resolved host list and, on some auth/parse errors, the full connection string including the
  password. These land in `~/.bookscrapper/logs/*.log` (retained across up to 10 runs) and on the console.
- Impact: standing credential disclosure to anyone with read access to the log dir.
- Mitigation: subtask 05's `ping()` must re-raise as `MongoConnectionError` carrying **only** the exception
  class name plus pymongo `code`/`codeName` when present (SEC-3). Every call site that today prints `{e}`
  must be routed through the redacted `MongoConnectionError` (SEC-4). This is the primary security purpose
  of the factory work; if any path still emits raw `{e}` or `exc_info=True` on a connection exception, this
  finding is unmitigated.

### [HIGH] `check_mongodb_connection` silently drops the client cert when the file is missing
- Vector / evidence: `book_utils.py:207-218` - `if tls_cert_file and os.path.exists(tls_cert_file)` falls
  through to a **certless** `MongoClient` when the configured cert path does not exist, so a broken X.509
  setup can appear reachable (or fail with a misleading cause) instead of failing on the missing identity.
  It also ignores `TLS_CA_FILE`, diverging from the real path and defeating server-CA pinning during a
  pre-flight check.
- Impact: misdiagnosis of the client-vs-server auth question (the open Atlas-8000 ambiguity) and loss of CA
  pinning on the diagnostic path.
- Mitigation: SEC-6, SEC-7 - a non-`None` cert that does not exist must raise `MongoConnectionError`;
  `TLS_CA_FILE` must be honored identically on both paths.

### [MEDIUM] settings.json permission check does not cover symlinks or foreign ownership
- Vector / evidence: subtask 06 spec only checks `mode & 0o077`. A `settings.json` that is a symlink to an
  attacker-controlled/world-writable target, or a file owned by another UID, passes a mode check yet is not
  trustworthy; `load_settings` (`config.py:67-75`) reads it verbatim.
- Impact: an attacker who can plant/relink the file controls the resolved connection string (redirect to a
  hostile cluster) or the cert path.
- Mitigation: SEC-9 - additionally warn when the file is a symlink or not owned by the current UID
  (`os.stat` st_uid vs `os.getuid()`), POSIX-only, once per process. Warning (not abort) to match the
  spec's non-breaking stance.

### [MEDIUM] `literal` cert path allows traversal / absolute-path escape out of CONFIG_DIR
- Vector / evidence: `config.py:59-64` `_resolve_cert_field` uses an absolute path as-is and joins any
  relative value under `CONFIG_DIR` with no containment check, so `"../../etc/…"` or `"/etc/…"` resolves
  outside `~/.bookscrapper/`. Source is the owner-controlled `settings.json`, so this is defense-in-depth,
  not a direct external vector.
- Impact: low (trusted-config surface), but a foreign-owned/symlinked settings.json (see above) upgrades it
  to pointing pymongo at an arbitrary readable file used as a client key.
- Mitigation: SEC-10 - keep absolute-vs-CONFIG_DIR resolution as designed, never log cert file *contents*,
  only the resolved path; existence check must not read the file. No behavior change required beyond
  documenting the trust assumption; escalates only if SEC-9 is skipped.

### [MEDIUM] Unknown-key / shape validation must not echo secret values (CWE-209)
- Vector / evidence: subtask 06 adds unknown-top-level-key rejection and 04/06 add shape errors. If an error
  message interpolates the *value* of a field (e.g. a mistyped block still containing a literal URI) it
  leaks the secret into the loud `ConfigError` -> log -> console path. Today `_resolve_field` correctly
  echoes only the env-var **name** (`config.py:54`) and never the literal value - this must be preserved.
- Mitigation: SEC-11 - unknown-key and shape errors list only key *names* / `auth_type` tokens, never field
  values; the example file (SEC-12) must contain placeholders only and pass `/secret-scan`.

### [LOW] TLS validation must never be disableable through the factory
- Vector / evidence: new `client.py` centralizes `MongoClient` construction.
- Mitigation: SEC-5 - `tlsAllowInvalidCertificates=False` is hard-coded and no parameter, env var, or URI
  option is allowed to flip it (nor `tlsAllowInvalidHostnames`); `tlsCAFile` only when `ca_file` is given,
  `tls=True`+`tlsCertificateKeyFile` only when `cert_file` is given; always `ServerApi("1")`.

### [LOW] Success line must never name host/URI (04)
- Vector / evidence: `get_mongo_collection` (`database.py:204`) prints success unconditionally today.
- Mitigation: SEC-1, SEC-2 - success only when the collection is non-`None`, message names
  `bookscraper_db.books` only.

## Numbered requirements (coder-citable)

- **SEC-1** (04): `get_mongo_collection` emits a `success` status line only when `_mongo_collection is not None`; a failed init emits zero `success`-status lines.
- **SEC-2** (04): The success message names only the database/collection (`bookscraper_db.books`); it never contains the host, URI, or any cert path.
- **SEC-3** (05): `ping()` re-raises connection/auth failures as `MongoConnectionError` whose message is limited to the exception class name plus pymongo `code`/`codeName` when present; no host, URI, credentials, or raw pymongo text.
- **SEC-4** (05): No logging or `print_log` call on any connection/auth failure path interpolates a raw pymongo exception (`{e}`) or uses `exc_info=True` for it; all such sinks go through the redacted `MongoConnectionError`.
- **SEC-5** (05): `build_mongo_client` hard-codes `tlsAllowInvalidCertificates=False` and `ServerApi("1")`, exposes no way to disable cert/hostname validation, sets `tls=True`+`tlsCertificateKeyFile` only when a cert is given and `tlsCAFile` only when a CA file is given.
- **SEC-6** (05): A non-`None` `cert_file` that does not exist raises `MongoConnectionError`; the cert is never silently dropped on any path (fixes `check_mongodb_connection`).
- **SEC-7** (05): `check_mongodb_connection` honors `TLS_CA_FILE` and builds identical `MongoClient` kwargs to `_initialize_mongodb_connection` for the same inputs (single factory, one `MongoClient(` hit in `src/`).
- **SEC-8** (05): `MongoConnectionError` messages and factory logging never include the connection URI; log only `uri_has_embedded_credentials()`-style booleans / mechanism facts, never the URI string.
- **SEC-9** (06): `load_settings` on POSIX warns once per process when `mode & 0o077 != 0`, when the file is a symlink, or when it is not owned by the current UID; names the file and the `chmod 600` fix; skipped on `os.name == "nt"`. Warning, not abort.
- **SEC-10** (06): Cert-path resolution keeps absolute-as-is / relative-under-`CONFIG_DIR` behavior; only the resolved path (never file contents) is ever logged, and the existence check does not open/read the file.
- **SEC-11** (06): Unknown-top-level-key and malformed-shape `ConfigError` messages list only key names / `auth_type` tokens and never interpolate a resolved or literal field value (preserve `_resolve_field`'s env-var-name-only behavior).
- **SEC-12** (06): `docs/examples/settings.example.json` contains placeholders only, no real host/user/password/cert filename, and passes `/secret-scan docs/examples/`.

## Verdict: PASS_WITH_FOLLOWUP

No CRITICAL. The design of subtasks 04/05/06 is sound and, if SEC-1..SEC-12 are all met, closes the
standing credential-in-logs exposure. Merge gate condition: **SEC-3 and SEC-4 are blocking for subtask 05**
- if either is unmet, the two HIGH exception-leakage findings remain live (CWE-532) and this must be
treated as BLOCK. SEC-9 (symlink/ownership) is an additive hardening beyond the written subtask spec; if
descoped, record it as an accepted follow-up rather than dropping it silently. Hand fixes to
`python-expert` and the corresponding regression tests (redaction, missing-cert-raises, permission-warning,
unknown-key) to `testing-expert`.
