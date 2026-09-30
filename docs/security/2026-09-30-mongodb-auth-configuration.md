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

## Verification - subtask 05 (2026-09-30)

Post-implementation review of the single `MongoClient` factory (new
`src/bookscraper/backends/mongo/client.py`; call-site changes in `database.py` and
`book_utils.py`; tests in `tests/mock/backends/mongo/test_client.py`). Verified against the
as-built code, not the design.

### Redaction contract as built

There is exactly one `MongoClient(...)` construction site in `src/`
(`client.py:190`, inside `build_mongo_client`). Every connection/auth failure leaves the
factory as a `MongoConnectionError` whose message is produced by `_redact_pymongo_error`
(`client.py:198-219`) and is limited to: the originating exception **class name**, plus the
pymongo `code` (int) and `details["codeName"]` (str) **when present** - e.g.
`OperationFailure 8000 AtlasError`. It never contains the URI, host list, userinfo, password,
or raw pymongo message text. The original exception is suppressed with `from None` on both the
constructor wrap (`client.py:191-195`) and `ping()` (`client.py:233-234`), so raw text cannot
leak through `__cause__`/`__context__` tracebacks either.

An error message may therefore contain, at most: (a) an exception class name and numeric/name
error code; or (b) on the missing-cert path (`client.py:174-175`), the **resolved cert file
path** (not its contents) - permitted by SEC-10. No message emitted by this factory carries a
URI, credential, or host.

Call-site discipline: both connection paths interpolate only the already-redacted
`MongoConnectionError` (`database.py:109-113`, `book_utils.py:232-236`), and both defensive
`except Exception` branches log **only** `type(e).__name__` with no `exc_info`
(`database.py:118-126`, `book_utils.py:238-246`). The remaining `exc_info=True` / raw `{e}`
sites in `database.py` (save/query/index helpers) are on an already-established collection, not
the connection/auth path, and are out of SEC-3/4 scope.

### Per-SEC status

- **SEC-3 - PASS.** `ping()` (`client.py:222-234`) re-raises as redacted `MongoConnectionError`
  (class + code/codeName only). Covered by `test_ping_wraps_error_keeping_code_name_only`,
  `test_ping_wraps_error_without_code`, `test_ping_error_does_not_leak_host_in_message_or_logs`.
- **SEC-4 - PASS.** No connection/auth failure path interpolates raw `{e}` or uses `exc_info`;
  all go through the redacted error, and the defensive branches log class name only.
- **SEC-5 - PASS.** `build_mongo_client` hard-codes `server_api=ServerApi("1")` and
  `tlsAllowInvalidCertificates=False` (`client.py:177-181`); `tlsAllowInvalidHostnames` is never
  set (pymongo default False). The signature exposes no `**kwargs` passthrough and no toggle;
  explicit kwargs take precedence over URI options, and a hostile URI carrying `tlsInsecure=true`
  conflicts with the explicit kwarg and raises (caught, redacted, connection fails) rather than
  relaxing validation. `tls=True`+`tlsCertificateKeyFile` set only with a cert, `tlsCAFile` only
  with a CA file. Covered by the `test_build_client_*` kwargs assertions.
- **SEC-6 - PASS.** A non-`None` `cert_file` that does not exist raises `MongoConnectionError`
  before any client is built (`client.py:174-175`); the cert is never silently dropped on either
  path. Covered by `test_build_client_missing_cert_file_raises`.
- **SEC-7 - PASS.** `check_mongodb_connection` reads `TLS_CA_FILE` (`book_utils.py:204`) and calls
  the same `build_mongo_client(uri, cert, ca, timeout_ms=...)` as
  `_initialize_mongodb_connection` (`database.py:91-97`), yielding identical kwargs; one
  `MongoClient(` in `src/`. Covered by `test_database_and_preflight_build_identical_client_kwargs`.
- **SEC-8 - PASS.** No `MongoConnectionError` message or factory log line includes the URI. The
  only URI-derived facts logged are the booleans from `uri_selects_x509_auth` /
  `uri_has_embedded_credentials` used by `_warn_if_x509_mechanism_missing`, which logs the cert
  path but never the URI.

### HIGH findings

- **HIGH #1 (credential/URI leakage via pymongo exception text) - CLOSED.** Every connection/auth
  failure path (constructor wrap, `ping`, both `MongoConnectionError` handlers, both defensive
  `except Exception` branches) is redacted to class name + code/codeName, or class name only. No
  raw `{e}` or `exc_info` remains on the connection path. CWE-532 / CWE-209 exposure removed.
- **HIGH #2 (`check_mongodb_connection` dropping a missing cert / ignoring `TLS_CA_FILE`) -
  CLOSED.** The pre-flight now routes through the shared factory, which raises on a missing cert
  (SEC-6) and honors `TLS_CA_FILE` (SEC-7); certless silent fallback is gone. Certificate
  validation is not disableable through any code path (SEC-5).

### Residual risk

- **LOW / defense-in-depth (unchanged from MEDIUM findings, out of subtask-05 scope):** SEC-9
  (settings.json symlink/foreign-ownership warning) and SEC-10 (literal cert-path containment)
  belong to subtask 06 and are not verified here.
- **LOW (accepted):** the missing-cert `MongoConnectionError` and the X.509-mechanism warnings
  include the resolved cert **file path** (never contents) in logs - permitted by SEC-10 and
  useful for diagnosis; a path is not a credential.
- **INFORMATIONAL:** `_redact_pymongo_error` accepts `BaseException` and reads `code`/`details`
  defensively (non-dict `details` ignored, `test_redact_ignores_non_dict_details`); should a
  future pymongo release ever place secret text in a top-level `.code`/`.codeName`, the redactor
  would surface it, but no current pymongo error does.

### Verdict: PASS

## Verification - task 02.0 close (2026-09-30)

Post-implementation review of subtask 04 (honest connection logging, SEC-1/2) and subtask 06
(settings.json hardening, SEC-9..12), verified against the as-built code (`config.py`,
`database.py`, `docs/examples/settings.example.json`). Complements the subtask-05 section above.

### Per-SEC status

- **SEC-1 - PASS.** `get_mongo_collection` (`database.py:161-166`) emits the `success` line only
  inside `if _mongo_collection is not None:`; a failed init returns `None, None` (having logged the
  specific cause) and emits zero success lines.
- **SEC-2 - PASS.** The success message (`database.py:164`) is
  `Connection to MongoDB Atlas successful (<db>.<collection>)`, built from the resolved
  `_mongo_db_name`/`_mongo_collection_name` (`database.py:45-49`, set from
  `config.resolve_mongo_namespace()` at `database.py:71`), never the host, URI, or cert path. The
  namespace values are db/collection identifiers, not secrets.
- **SEC-9 - PASS (incl. ratified symlink/foreign-owner extension).**
  `_check_settings_file_security` (`config.py:97-131`) warns on `st_mode & 0o077`, on
  `is_symlink()`, and on `st_uid != os.getuid()`; each `_warn_once`-deduplicated per
  `<path>:<condition>` (`config.py:85-94`), routed through `print_log` so it reaches console + log;
  skipped on `os.name == "nt"`; warning only, never abort - existing setups keep working.
- **SEC-10 - PASS.** `_resolve_cert_field` (`config.py:76-82`) is unchanged: absolute path as-is,
  relative resolved under `CONFIG_DIR`; it never opens or reads the cert. The missing-cert existence
  check lives in `client.build_mongo_client` (SEC-6) and raises without reading contents.
- **SEC-11 - PASS.** `_validate_top_level_keys` (`config.py:134-149`) lists only unknown key *names*
  and the allowed-key set - never a field value. The `database`-block error (`config.py:236`) names
  no values; `resolve_mongo_connection`'s `auth_type` error and `_resolve_field`'s `source`/env-var
  errors (`config.py:63,71,73`) echo only structural tokens / the env-var *name*, never a literal or
  resolved secret.
- **SEC-12 - PASS.** `docs/examples/settings.example.json` is placeholders only: `uri` via env-var
  *name* `MONGODB_URI`, a placeholder cert filename, and the default `bookscraper_db`/`books`
  namespace. No host, username, password, or real cert filename.

### Whole-task diff sanity check (post subtask-05)

No new raw pymongo `{e}` interpolation or `exc_info=True` was introduced on any
connection/auth path. `_initialize_mongodb_connection`'s `except Exception` now logs
`type(e).__name__` only (`database.py:119`). The `ConfigError` handler (`database.py:74-76`,
pre-existing) interpolates a `config.ConfigError`, whose messages are secret-free by SEC-11
(a `JSONDecodeError`-derived message carries only line/column position, not document content).

### Residual risk

- **LOW / self-inflicted (informational).** `resolve_mongo_namespace` resolves the optional
  `database` block via the generic `{source,value}` mechanism, and the resolved db/collection names
  flow into the success log line (SEC-2). An operator who (mis)places a full credentialed URI into a
  `database.name` literal would see that value logged. This is owner-controlled config at the same
  trust level as the `uri` field, not a new external vector; the connection would simply fail against
  the bogus namespace. No change required.
- **LOW / accepted (TOCTOU).** `load_settings` runs `_check_settings_file_security`
  (`is_symlink` via lstat, then `stat` following the link) and afterwards `read_text` follows the
  symlink again - the security-checked path and the read path are resolved independently, and a
  symlink *is* followed on read after the warning. This is acceptable because SEC-9 is advisory-only
  and gates no allow/deny decision: there is no control for a TOCTOU swap to bypass. Matches the
  ratified warn-don't-abort stance. A dangling symlink is treated as "no settings" (legacy fallback)
  since `.exists()` follows it - benign.

### Verdict: PASS
