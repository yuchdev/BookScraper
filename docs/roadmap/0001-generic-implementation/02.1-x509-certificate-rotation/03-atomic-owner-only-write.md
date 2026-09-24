# 03 - Atomic owner-only write

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/02.1-x509-certificate-rotation/README.md)
**Status:** ✅ Complete
**Role:** Security Auditor → Python Expert

## Requirements

- `atomic_write(path, data: bytes)`: create parent dirs; `NamedTemporaryFile(dir=path.parent,
  prefix=f".{path.name}.", delete=False)`; write, flush, `os.fsync`; then `chmod 0o600` **before** `replace`, so
  the final path is never world-readable, even for an instant.
- Used for the PEM (which contains the private key) and for the `settings.json` rewrite.

## Tests

`tests/unit/backends/mongo/test_cert_rotation.py`: `test_atomic_write_creates_parents_and_owner_only_file`,
`test_atomic_write_replaces_existing_file_and_leaves_no_temp_files`.

## Success criteria

- [x] The cert file's mode is exactly `0o600` after rotation.
