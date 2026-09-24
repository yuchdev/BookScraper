# 07 - Old-certificate retention

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/02.1-x509-certificate-rotation/README.md)
**Status:** ⬜ Not started
**Depends on:** [05-rotate-cert-command.md](/docs/roadmap/0001-generic-implementation/02.1-x509-certificate-rotation/05-rotate-cert-command.md)
**Role:** Security Auditor → Python Expert

## Context

Every rotation adds another `X509-cert-*.pem` to `~/.bookscrapper/`, each one holding a live private key, and
nothing ever removes them. Old keys stay valid until their own expiry, so each one is attack surface. CLAUDE.md
also wants that directory kept minimal.

## Requirements

- `cert_rotation.prune_old_certificates(keep: int, protected: set[Path]) -> list[str]` (returns status lines):
  - Candidates: `CONFIG_DIR.glob("X509-cert-*.pem")`, sorted newest-first by `st_mtime`.
  - Keeps the newest `keep` files, **plus every path in `protected`** however old it is.
  - Deletes the rest with `Path.unlink()`. A failed unlink becomes a `WARNING:` line, not an exception.
  - Never touches files outside `CONFIG_DIR` or files that don't match the glob, so a user-supplied `--output`
    elsewhere is never pruned.
- `protected` = {the cert just written} ∪ {the cert `config.resolve_mongo_connection()` resolves *after*
  repointing} ∪ {`TLS_CERT_FILE`, if set}. If the active cert can't be resolved (e.g. `ConfigError`), **skip
  pruning entirely** and say so. Never prune on uncertainty.
- CLI: `rotate-cert --keep N` (`int`, default `2`, `dest="keep"`, minimum `1`; argparse rejects `0` through a custom
  type). `--no-prune` (`store_true`) disables pruning.
- Pruning runs only **after** a successful rotation and, once subtask 09 lands, a successful verification.

## Files

- Modify `src/bookscraper/backends/mongo/cert_rotation.py`, `src/bookscraper/cli.py`,
  `src/bookscraper/commands/rotate_cert.py`.
- Tests: `tests/unit/backends/mongo/test_cert_rotation.py`, `tests/mock/commands/test_rotate_cert.py`,
  `tests/unit/test_cli.py`.

## Tests

- `test_prune_keeps_newest_n`
- `test_prune_never_deletes_protected_even_if_oldest`
- `test_prune_ignores_files_outside_glob_and_dir`
- `test_prune_unlink_failure_is_warning`
- `test_prune_skipped_when_active_cert_unresolvable`
- `test_no_prune_flag_disables_pruning`
- `test_keep_zero_rejected_by_cli`
- `test_prune_not_run_when_rotation_fails`

## Success criteria

- [ ] After ten rotations with default flags, exactly two cert files remain, one of which is the active cert.
