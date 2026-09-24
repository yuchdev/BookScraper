# 02 - Legacy env-var fallback

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/02.0-mongodb-auth-configuration/README.md)
**Status:** ✅ Complete
**Depends on:** [01-settings-json-resolver.md](/docs/roadmap/0001-generic-implementation/02.0-mongodb-auth-configuration/01-settings-json-resolver.md)
**Role:** Python Expert

## Requirements

- With no `settings.json`, `_legacy_resolve()` returns `(MONGODB_URI or None, TLS_CERT_FILE or
  get_default_tls_cert_file())`.
- `get_default_tls_cert_file()` returns the newest `CONFIG_DIR/X509-cert-*.pem` by `st_mtime`, or `None`. It is a
  **glob, never a pinned filename** (a pinned name strands the app after rotation).
- `TLS_CA_FILE` is independent of the resolver and read only by `database.py`.

## Tests

`tests/unit/backends/mongo/test_config.py`: `test_env_uri_no_cert`, `test_cert_glob_used_when_no_tls_cert_env`,
`test_explicit_tls_cert_env_wins_over_glob`, `test_no_certs_returns_none`, `test_returns_newest_by_mtime`,
`test_ignores_non_matching_files`. `tests/unit/test_integration_helpers.py` guards the glob in the test helper.

## Success criteria

- [x] A freshly rotated cert is picked up with no config change when `settings.json` is absent.
