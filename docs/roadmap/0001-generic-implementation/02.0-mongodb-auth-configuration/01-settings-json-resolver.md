# 01 - Deterministic settings.json resolver

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/02.0-mongodb-auth-configuration/README.md)
**Status:** ✅ Complete
**Role:** Security Auditor → Python Expert

## Requirements

- `config.py` constants `CONFIG_DIR = ~/.bookscrapper`, `SETTINGS_FILE = CONFIG_DIR / "settings.json"`, exception
  `ConfigError`.
- `load_settings(path=None) -> Optional[dict]`: missing file → `None`; malformed JSON → `ConfigError`.
- `_resolve_field(spec, field_name) -> str`: `spec` must be a dict with `source ∈ {"env","literal"}` and a `value`.
  `literal` returns it as-is; `env` looks it up in `os.environ` and raises `ConfigError` if it is unset.
- `_resolve_cert_field(spec, field_name) -> str`: as above, then a bare filename resolves under `CONFIG_DIR`, and an
  absolute path is returned untouched.
- `resolve_mongo_connection(path=None) -> (uri, cert_or_None)`:
  - `auth_type: "pass"` → `(pass.uri, None)`.
  - `auth_type: "x509"` → `(x509.uri, x509.cert)`. All four env/literal combinations are valid.
  - Missing or unknown `auth_type`, or a missing matching block → `ConfigError`.

## Files

- `src/bookscraper/backends/mongo/config.py`.

## Tests

`tests/unit/backends/mongo/test_config.py`: `test_literal_returns_value_as_is`, `test_env_returns_looked_up_value`,
`test_env_unset_raises`, `test_unrecognized_source_raises`, `test_bare_filename_resolved_under_config_dir`,
`test_absolute_path_returned_untouched`, `test_pass_literal_uri`, `test_pass_env_uri`,
`test_all_four_x509_source_combos_resolve_identically`, `test_missing_auth_type_raises`,
`test_unrecognized_auth_type_raises`, `test_pass_without_pass_block_raises`, `test_x509_without_x509_block_raises`.
`tests/integration/test_connection_settings.py` covers all six shapes against Atlas.

## Success criteria

- [x] `database.py` and `book_utils.py` both obtain credentials only through `resolve_mongo_connection()`.
