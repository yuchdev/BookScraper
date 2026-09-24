# 06 - Certificate expiry inspection

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/02.1-x509-certificate-rotation/README.md)
**Status:** ⬜ Not started
**Depends on:** [05-rotate-cert-command.md](/docs/roadmap/0001-generic-implementation/02.1-x509-certificate-rotation/05-rotate-cert-command.md)
**Role:** Security Auditor → Python Expert

## Context

Certificates expire after 1-24 months, and nothing warns before they do. Today the first sign is a scrape run failing
its Mongo pre-flight. Rotation is only useful if someone knows when to run it.

## Requirements

- **Parser:** `cert_rotation.read_certificate_expiry(path: Path) -> datetime` (timezone-aware UTC). Reads the
  first `CERTIFICATE` PEM block out of the combined key+cert file and returns its `not_valid_after_utc`.
  - Use the `cryptography` package as a **direct** dependency (`uv add cryptography`), then run `/dep-audit`. It is
    not in `uv.lock` today. Rejected alternatives: the private `ssl._ssl._test_decode_cert` (unstable API), and
    shelling out to `openssl` (not present on every Windows host).
  - A missing file, no certificate block, or an unparseable block → `RotationError` naming the file, never its
    contents.
- **Check mode:** `bookscraper rotate-cert --check [--warn-days N]` (default `N = 30`):
  - Resolves the **currently active** cert the same way the app does (`config.resolve_mongo_connection()`'s cert);
    if there is none, there's nothing to check.
  - Prints `path`, `expires: <ISO date>`, `days remaining: <n>`.
  - Exit codes: `0` if more than N days remain; `3` if within N days (lets cron/CI alert); `1` if expired or
    unreadable; `0` plus a note when no x509 cert is configured.
  - Never contacts Atlas and never needs the `ATLAS_*` variables.
- **Pre-flight warning:** `resolve_store_backend("mongo")` calls a non-raising helper
  `cert_expiry_warning(cert_path, warn_days=30) -> Optional[str]` and `print_log`s any warning before connecting. A
  parse failure there is a warning, not a crash. The helper must be importable from `storage.py` without an import
  cycle.
- After a successful rotation, the command also prints the **new** cert's expiry date.

## Files

- Modify `pyproject.toml`, `uv.lock` - add `cryptography`.
- Modify `src/bookscraper/backends/mongo/cert_rotation.py` - `read_certificate_expiry`, `cert_expiry_warning`.
- Modify `src/bookscraper/cli.py` - `--check` (`store_true`, `dest="check"`), `--warn-days` (`int`, default `30`,
  `dest="warn_days"`).
- Modify `src/bookscraper/commands/rotate_cert.py` - check-mode branch; post-rotation expiry line.
- Modify `src/bookscraper/backends/storage.py` - pre-flight warning.
- Tests: `tests/unit/backends/mongo/test_cert_rotation.py`, `tests/mock/commands/test_rotate_cert.py`,
  `tests/unit/test_cli.py`, `tests/mock/backends/test_storage.py`.
- Test fixture: generate a throwaway self-signed cert **at test time** with `cryptography`, using a fixed
  `not_valid_after`. Never commit a key.

## Tests

- `test_read_expiry_from_combined_key_and_cert_pem`
- `test_read_expiry_missing_file_raises_rotation_error`
- `test_read_expiry_pem_without_certificate_block_raises`
- `test_check_mode_exit_zero_when_far_from_expiry`
- `test_check_mode_exit_three_within_warn_window`
- `test_check_mode_exit_one_when_expired`
- `test_check_mode_without_x509_config_is_noop_zero`
- `test_check_mode_never_requires_atlas_env`
- `test_preflight_prints_warning_for_soon_expiring_cert`
- `test_preflight_parse_failure_is_warning_not_crash`
- `test_cli_rotate_cert_check_flags`

## Success criteria

- [ ] `bookscraper rotate-cert --check` works as a cron health check, with documented exit codes.
- [ ] Key material never ends up in a log line (security-auditor sign-off).
