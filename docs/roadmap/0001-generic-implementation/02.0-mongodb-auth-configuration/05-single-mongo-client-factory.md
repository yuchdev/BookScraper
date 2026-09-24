# 05 - Single MongoClient factory

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/02.0-mongodb-auth-configuration/README.md)
**Status:** ⬜ Not started
**Depends on:** [04-honest-connection-logging.md](/docs/roadmap/0001-generic-implementation/02.0-mongodb-auth-configuration/04-honest-connection-logging.md)
**Role:** Security Auditor → Python Expert

## Context

`MongoClient` is constructed in two places that have already drifted:

| Aspect                     | `database._initialize_mongodb_connection` | `book_utils.check_mongodb_connection`  |
|----------------------------|-------------------------------------------|----------------------------------------|
| `TLS_CA_FILE`              | honored                                   | **ignored**                            |
| Missing cert file          | critical + abort                          | **silently connects without the cert** |
| `serverSelectionTimeoutMS` | **unset (pymongo default 30 s)**          | 5000                                   |
| Called by the app          | yes (`resolve_store_backend`)             | **no - tests only**                    |

A diagnosis done through one path doesn't carry over to the other. That is exactly the "client or server?"
ambiguity behind the unverified Atlas error-8000 report.

## Requirements

- New module `src/bookscraper/backends/mongo/client.py`:

  ```python
  DEFAULT_SERVER_SELECTION_TIMEOUT_MS = 10_000

  class MongoConnectionError(Exception): ...

  def build_mongo_client(
      uri: str,
      cert_file: Optional[str],
      ca_file: Optional[str],
      timeout_ms: int = DEFAULT_SERVER_SELECTION_TIMEOUT_MS,
  ) -> MongoClient: ...

  def ping(client: MongoClient) -> None:  # raises MongoConnectionError with a redacted message
  ```

  - A non-`None` `cert_file` that doesn't exist → `MongoConnectionError` (never silently drop the cert).
  - Always `server_api=ServerApi("1")`, `tlsAllowInvalidCertificates=False`, `serverSelectionTimeoutMS=timeout_ms`.
  - `tls=True` + `tlsCertificateKeyFile` only when `cert_file` is given; `tlsCAFile` only when `ca_file` is given.
  - Calls `_warn_if_x509_mechanism_missing`. Move it and the two `uri_*` helpers into `client.py`, and re-export
    them from `database.py` so existing imports and tests keep working.
- `MONGODB_SERVER_SELECTION_TIMEOUT_MS` env var overrides the default (documented in CLAUDE.md).
- `_initialize_mongodb_connection()` uses `build_mongo_client` + `ping`.
- `book_utils.check_mongodb_connection()` becomes a thin wrapper: resolve → `build_mongo_client` → `ping` → close.
  Keep it (the `doctor` command in task 05.0 subtask 07 uses it) and keep the function-local import.
- pymongo exception messages can embed hostnames. `ping()` re-raises as `MongoConnectionError` whose message keeps
  only the exception class name and pymongo's `code` / `codeName` when present (e.g. `OperationFailure 8000
  AtlasError`). That's enough to diagnose, with nothing else leaking.

## Files

- Create `src/bookscraper/backends/mongo/client.py`.
- Modify `src/bookscraper/backends/mongo/database.py`, `src/bookscraper/book_utils.py`.
- Create `tests/mock/backends/mongo/test_client.py`; modify `tests/mock/test_book_utils.py`,
  `tests/mock/backends/mongo/test_database.py`.
- Modify `CLAUDE.md` (*Authentication configuration*, *Architecture*).

## Tests

- `test_build_client_with_cert_sets_tls_and_key_file`
- `test_build_client_without_cert_omits_tls_kwargs`
- `test_build_client_passes_ca_file_only_when_set`
- `test_build_client_missing_cert_file_raises`
- `test_timeout_default_and_env_override`
- `test_ping_wraps_error_keeping_code_name_only`
- `test_check_mongodb_connection_honors_tls_ca_file`
- `test_database_and_preflight_build_identical_client_kwargs` (same inputs → same `MongoClient` kwargs on both paths)

## Success criteria

- [ ] `grep -rn "MongoClient(" src/` returns exactly one hit (`client.py`).
- [ ] A security-auditor note in `docs/security/` covers error-message redaction.
