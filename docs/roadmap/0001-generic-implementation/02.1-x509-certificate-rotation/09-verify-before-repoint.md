# 09 - Verify before repoint

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/02.1-x509-certificate-rotation/README.md)
**Status:** ⬜ Not started
**Depends on:** [04-settings-json-repointing.md](/docs/roadmap/0001-generic-implementation/02.1-x509-certificate-rotation/04-settings-json-repointing.md), [08-live-x509-rediagnosis.md](/docs/roadmap/0001-generic-implementation/02.1-x509-certificate-rotation/08-live-x509-rediagnosis.md), [Task 02.0 / 05](/docs/roadmap/0001-generic-implementation/02.0-mongodb-auth-configuration/05-single-mongo-client-factory.md)
**Role:** Security Auditor → Python Expert

## Context

`rotate_certificate()` writes the new PEM and **immediately** repoints `settings.json` at it. Atlas can take a short
while to propagate a new user certificate. If the new cert doesn't authenticate yet, or never will, the app is left
pointing at a broken identity while the old, working cert is still on disk.

## Requirements

- New step between `atomic_write(output, cert)` and `update_settings_after_rotation(output)`:
  `verify_certificate(cert_path, uri, *, attempts=5, delay_s=6.0) -> None`.
  - Builds a client with `backends/mongo/client.build_mongo_client(uri, str(cert_path), TLS_CA_FILE)` (task 02.0
    subtask 05) and `ping`s it. Retries on `MongoConnectionError` up to `attempts` times with a fixed delay; `sleep`
    is injectable for tests.
  - The URI comes from the **current** resolver output (`resolve_mongo_connection()[0]`). If no URI is configured,
    verification is skipped and a status line says so.
- On final failure, **do not repoint**. Leave the new PEM on disk, return a `WARNING:` status line with the new
  path and the error code/codeName, and exit `1`. The old configuration keeps working.
- CLI: `--no-verify` (`store_true`) skips the step, for offline or first-time setups. `--verify-attempts N` (`int`,
  default `5`).
- Import direction: `cert_rotation.py` → `client.py` is fine, since both live in `backends/mongo/`.

## Files

- Modify `src/bookscraper/backends/mongo/cert_rotation.py`, `src/bookscraper/cli.py`,
  `src/bookscraper/commands/rotate_cert.py`.
- Tests: `tests/mock/backends/mongo/test_cert_rotation.py`, `tests/mock/commands/test_rotate_cert.py`,
  `tests/unit/test_cli.py`.

## Tests

- `test_verify_success_then_repoints_settings`
- `test_verify_retries_then_succeeds`
- `test_verify_final_failure_leaves_settings_untouched_and_exits_one`
- `test_verify_skipped_when_no_uri_configured`
- `test_no_verify_flag_skips_ping`
- `test_verify_failure_message_has_code_but_no_host`

## Success criteria

- [ ] No code path repoints `settings.json` at a cert that failed verification.
