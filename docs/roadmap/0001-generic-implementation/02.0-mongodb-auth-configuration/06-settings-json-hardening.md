# 06 - settings.json validation & hardening

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/02.0-mongodb-auth-configuration/README.md)
**Status:** ⬜ Not started
**Depends on:** [01-settings-json-resolver.md](/docs/roadmap/0001-generic-implementation/02.0-mongodb-auth-configuration/01-settings-json-resolver.md)
**Role:** Security Auditor → Python Expert

## Context

`settings.json` can hold a literal connection string with an embedded password. `cert_rotation` rewrites it with
mode `0600`, but nothing checks the permissions of a hand-created file. The database and collection names come from
the undocumented env vars `MONGODB_DB_NAME` / `MONGODB_COLLECTION_NAME`. They are the one part of the Mongo config
that `settings.json` can't express, which breaks its "sole source of truth" promise. There is also no example
file, so users copy the shape out of CLAUDE.md by hand.

## Requirements

- **Permission check:** on POSIX, `load_settings()` stats the file. If `mode & 0o077 != 0` (group or other can read
  it), log a warning **once per process** naming the file and the fix (`chmod 600 ~/.bookscrapper/settings.json`).
  It's a warning, not an error, so existing setups keep working. Skip the check on Windows (`os.name == "nt"`).
- **Optional `database` block:**
  ```json
  "database": {"name": {"source": "literal", "value": "bookscraper_db"},
               "collection": {"source": "literal", "value": "books"}}
  ```
  Resolved with `_resolve_field`. If `settings.json` exists but has no such block, the defaults `bookscraper_db` /
  `books` apply, and `MONGODB_DB_NAME` / `MONGODB_COLLECTION_NAME` are **not** consulted (determinism). In legacy
  mode those env vars keep working.
  - New public `resolve_mongo_namespace(path=None) -> tuple[str, str]`.
  - `database._initialize_mongodb_connection()` calls it instead of reading `os.environ` directly.
- **Unknown top-level keys** (anything outside `auth_type`, `pass`, `x509`, `database`) → `ConfigError` listing
  them. A typo like `"x059"` must fail loudly.
- **Example file** `docs/examples/settings.example.json`: the x509 env/literal shape with placeholders only. It must
  pass `/secret-scan`.

## Files

- Modify `src/bookscraper/backends/mongo/config.py`, `src/bookscraper/backends/mongo/database.py`.
- Create `docs/examples/settings.example.json`.
- Modify `tests/unit/backends/mongo/test_config.py`, `tests/mock/backends/mongo/test_database.py`.
- Modify `CLAUDE.md` (*Authentication configuration*: `database` block, permission warning, example link).

## Tests

- `test_world_readable_settings_warns_once`
- `test_owner_only_settings_does_not_warn`
- `test_permission_check_skipped_on_windows`
- `test_database_block_resolves_name_and_collection`
- `test_settings_without_database_block_uses_defaults_and_ignores_env`
- `test_legacy_mode_honors_db_name_env_vars`
- `test_unknown_top_level_key_raises`
- `test_example_settings_file_is_valid_for_resolver` (load the example with its env vars set; resolves cleanly)

## Success criteria

- [ ] `grep -n "MONGODB_DB_NAME" src/bookscraper/backends/mongo/database.py` returns nothing.
- [ ] `/secret-scan docs/examples/` is clean.
