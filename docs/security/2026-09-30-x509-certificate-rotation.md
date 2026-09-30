# Threat Model - X.509 certificate rotation (task 02.1, subtasks 06-10) - 2026-09-30

Pre-implementation threat model for branch `milestone/0001-generic-implementation`, task 02.1
subtasks 06 (certificate expiry inspection), 07 (old-certificate retention/pruning), 08 (live
X.509 re-diagnosis), 09 (verify before repoint), 10 (rotation runbook). Written before coding so
coder briefings can cite the numbered `ROT-*` requirements below.

This model **reuses** the redaction contract established for task 02.0 in
`docs/security/2026-09-30-mongodb-auth-configuration.md` rather than re-deriving it:

- **SEC-3** - `client.ping()` re-raises every connection/auth failure as `MongoConnectionError`
  whose message is limited to exception class name + pymongo `code`/`codeName`.
- **SEC-4** - no connection/auth failure sink interpolates raw pymongo `{e}` or uses `exc_info=True`.
- **SEC-8** - no `MongoConnectionError` message or factory log line ever includes the URI, host, or
  credential.

Verify-before-repoint (09) is required to go through `client.build_mongo_client` / `client.ping`
precisely so it inherits SEC-3/4/8 for free; anything that re-implements the ping outside the
factory re-opens the CWE-532/CWE-209 exposure that 02.0 closed.

## Assets & trust boundaries

- **Assets:** the combined key+cert PEM files `~/.bookscrapper/X509-cert-*.pem` - **each holds a
  live private key**; read access == DB credentials, and the older ones stay valid until their own
  expiry (standing attack surface). Also: `~/.bookscrapper/settings.json` (may hold a literal
  connection string with SCRAM password); the resolved `MONGODB_URI`; `TLS_CA_FILE`; the Atlas
  Admin API token/response; the per-run logs under `~/.bookscrapper/logs/` (root logger, up to 10
  retained); the saved integration-run report under `docs/reviews/`; the runbook under
  `docs/runbooks/`.
- **Trust boundaries:** (1) PEM bytes on disk -> `cryptography` parser -> `datetime` -> log/console
  (06); (2) `CONFIG_DIR.glob` filesystem listing -> `Path.unlink()` deletion decision (07); (3)
  Atlas TLS handshake / pymongo exception objects (may embed host, URI, cert path) -> verify status
  line -> log/console (09); (4) live pytest stdout (may embed host/user/URI) -> committed
  `docs/reviews/*.md` (08); (5) new third-party package `cryptography` -> supply chain (06); (6)
  human-authored runbook -> committed docs (10).

## Findings

### [HIGH] Private-key material can leak through PEM-parse error handling and logs (CWE-532, CWE-312)
- Vector / evidence: subtask 06 adds `read_certificate_expiry(path)` which opens a file that is a
  concatenated **private key + certificate**. Any `except ... as e` that interpolates the file
  bytes, or any debug log of the read buffer, would write the private key into
  `~/.bookscrapper/logs/*.log`. `cert_rotation.py` today never reads cert contents; this is the
  first code path that does.
- Impact: standing private-key disclosure to anyone with read access to the log dir; full DB
  identity compromise.
- Mitigation: ROT-1, ROT-2, ROT-3 - parse only the first `CERTIFICATE` PEM block (never the key
  block), raise `RotationError` naming only the *file path*, and never place file contents or key
  bytes in any exception/log/console line. Blocking for subtask 06.

### [HIGH] Pruning can delete the active (working) private key / cert - self-inflicted lockout (CWE-doS-ish, availability)
- Vector / evidence: subtask 07's `prune_old_certificates` deletes `X509-cert-*.pem` files. If the
  `protected` set is computed from a path string that does not byte-match the glob's `Path` spelling
  (symlink vs real path, `~` vs absolute, trailing normalization), the currently-referenced cert is
  treated as unprotected and unlinked, breaking all future auth while Atlas still trusts it.
- Impact: loss of the only working identity; DB unreachable until manual recovery.
- Mitigation: ROT-6, ROT-7, ROT-8 - canonicalize both candidates and `protected` before comparison
  (`Path.resolve()` / `os.path.samefile`), and **skip pruning entirely** if the active cert cannot
  be resolved (`ConfigError`). Never prune on uncertainty. Blocking for subtask 07.

### [HIGH] Verify-before-repoint must not re-open the pymongo exception leak, and must not repoint on failure
- Vector / evidence: subtask 09 pings Atlas with the new cert. If it builds its own `MongoClient`
  or logs the raw `PyMongoError`/Atlas response, it leaks host+URI+credential (the exact 02.0
  regression). If a failed verification still calls `update_settings_after_rotation`, the app is
  left pointing at a broken identity while the old working cert survives on disk.
- Impact: credential disclosure in logs AND a broken production auth config.
- Mitigation: ROT-11, ROT-12, ROT-13 - verify only via `client.build_mongo_client`+`client.ping`
  (inherits SEC-3/4/8); on final failure return a `WARNING:` status line carrying only
  code/codeName (no host, no URI, no Atlas body), do **not** repoint, exit 1. Blocking for
  subtask 09.

### [MEDIUM] Pruning could follow a symlink or escape CONFIG_DIR
- Vector / evidence: subtask 07 globs `CONFIG_DIR/X509-cert-*.pem`. A matched entry that is a
  symlink, or a `--output` cert written elsewhere, must never cause deletion of a file outside
  `CONFIG_DIR`. `Path.unlink()` on a symlink removes the link (safe), but a `resolve()`-then-delete
  or a candidate list that reaches beyond the dir is not.
- Impact: deletion of an unrelated file the symlink targets; deletion of a user-supplied `--output`
  cert.
- Mitigation: ROT-9, ROT-10 - operate strictly on the glob within `CONFIG_DIR`, unlink the matched
  path itself (do not resolve-then-delete a target outside the dir), and never treat a `--output`
  path outside `CONFIG_DIR` as a prune candidate.

### [MEDIUM] The committed integration-run report can leak real hosts/users/URIs (CWE-532, CWE-200)
- Vector / evidence: subtask 08 saves `uv run pytest -m integration -rxXs -v` output to
  `docs/reviews/<date>-x509-integration-run.md`. Integration tests connect to real Atlas; on
  failure pytest prints assertion context and captured logs that can include the cluster host,
  DB user, and `authSource=$external` URI - and this file is **committed to git**.
- Impact: permanent disclosure of the production cluster host/user in version control.
- Mitigation: ROT-14, ROT-15 - redact hosts -> `<cluster>` and DB user -> `<dbuser>` (and any URI)
  before committing; run `/secret-scan docs/reviews/` on the file; never paste a raw `MONGODB_URI`,
  `TEST_MONGODB_URI_*`, or Atlas token.

### [MEDIUM] New third-party dependency `cryptography` - supply-chain surface (CWE-1104, CWE-1395)
- Vector / evidence: subtask 06 requires `uv add cryptography` (confirmed **absent** from
  `uv.lock` today, not even transitively). It ships compiled/Rust components and pulls its own
  subtree; a compromised or vulnerable pin is a new ingestion surface.
- Impact: build/runtime supply-chain risk; a known-CVE version would ship with the app.
- Mitigation: ROT-4, ROT-5 - add it as a pinned direct dependency, commit the updated `uv.lock`,
  run `/dep-audit` and verify no open CVE against the resolved version before merge. Rejected
  alternatives (private `ssl._ssl._test_decode_cert`, shelling to `openssl`) are correctly avoided.

### [LOW] `--check` mode / cron exposure must stay Atlas-free and offline
- Vector / evidence: subtask 06 `--check` is meant for cron/CI and must not need `ATLAS_*` or
  contact Atlas; a check path that instantiated the Atlas client would put the Service Account
  secret on a scheduled, log-heavy path.
- Mitigation: ROT-16, ROT-17 - `--check` and the pre-flight `cert_expiry_warning` are pure local
  file reads, contact nothing, require no `ATLAS_*`, and the pre-flight helper is non-raising
  (parse failure is a warning, never a crash) and importable from `storage.py` without a cycle.

### [LOW] Runbook must contain placeholders only
- Vector / evidence: subtask 10 documents `ATLAS_*`, URIs, and `settings.json` examples.
- Mitigation: ROT-18 - no real host/user/password/project-id/cert filename/token; placeholders
  only; `/secret-scan docs/runbooks/` passes.

## Numbered requirements (coder-citable)

- **ROT-1** (06): `read_certificate_expiry(path)` parses only the first `CERTIFICATE` PEM block via `cryptography` and returns a timezone-aware UTC `datetime` (`not_valid_after_utc`); it never parses, loads, or retains the private-key block.
- **ROT-2** (06): A missing file, absent certificate block, or unparseable block raises `RotationError` naming **only the file path** - never the file contents, the offending bytes, or any key material.
- **ROT-3** (06): No expiry-inspection code path (parse, `--check`, post-rotation expiry line, pre-flight warning) ever writes PEM/key bytes to a log line, exception message, or console; key material never leaves the parser.
- **ROT-4** (06): `cryptography` is added as a pinned **direct** dependency in `pyproject.toml` with `uv.lock` regenerated and committed (it is absent from `uv.lock` today).
- **ROT-5** (06): `/dep-audit` is run against the newly resolved `cryptography` (and its subtree) and shows no open CVE before merge; verify current advisories via WebSearch at implementation time.
- **ROT-6** (07): The `protected` set = {cert just written} ∪ {cert `resolve_mongo_connection()` resolves *after* repointing} ∪ {`TLS_CERT_FILE` if set}, each canonicalized (`Path.resolve()`), and no protected path is ever unlinked however old.
- **ROT-7** (07): Candidate/protected comparison is canonical (`resolve()` / `os.path.samefile`) so a symlink-vs-realpath or `~`-vs-absolute spelling mismatch cannot cause the active cert to be pruned.
- **ROT-8** (07): If the active cert cannot be resolved (`ConfigError` or any resolver failure), pruning is **skipped entirely** with a status line saying so - never prune on uncertainty.
- **ROT-9** (07): Prune candidates come solely from `CONFIG_DIR.glob("X509-cert-*.pem")`; nothing outside `CONFIG_DIR` and nothing not matching the glob is ever a candidate, so a `--output` cert elsewhere is never pruned.
- **ROT-10** (07): Deletion unlinks the matched glob path itself (`Path.unlink()`); it never `resolve()`s a symlink and deletes the target outside `CONFIG_DIR`. A failed unlink becomes a `WARNING:` status line, not an exception.
- **ROT-11** (09): `verify_certificate` builds its client **only** via `client.build_mongo_client(uri, str(new_cert_path), TLS_CA_FILE)` and pings via `client.ping`, inheriting SEC-3/4/8 (redacted `MongoConnectionError`); it never constructs its own `MongoClient` or logs a raw pymongo error.
- **ROT-12** (09): On final verification failure the command does **not** call `update_settings_after_rotation` (settings.json untouched), leaves the new PEM on disk, and exits 1; no code path repoints at an unverified cert.
- **ROT-13** (09): The verify failure status line contains only code/codeName (via `_redact_pymongo_error`) - never the Atlas response body, host, URI, DB user, or cert contents; pruning (07) runs only after verification succeeds.
- **ROT-14** (08): The committed `docs/reviews/<date>-x509-integration-run.md` has hosts redacted to `<cluster>`, DB user to `<dbuser>`, and any `MONGODB_URI` / `TEST_MONGODB_URI_*` / Atlas token removed; no real identifier survives.
- **ROT-15** (08): `/secret-scan docs/reviews/` passes on the saved report before it is committed.
- **ROT-16** (06): `rotate-cert --check` and the `resolve_store_backend` pre-flight `cert_expiry_warning` perform local file reads only - they never contact Atlas and never require `ATLAS_*`; documented exit codes: `0` far-from-expiry / no-x509, `3` within warn window, `1` expired/unreadable.
- **ROT-17** (06): `cert_expiry_warning(cert_path, warn_days)` is non-raising (a parse failure returns a warning string, never crashes the connection path) and is importable from `storage.py` without an import cycle.
- **ROT-18** (10): `docs/runbooks/x509-certificate-rotation.md` contains placeholders only (no real host/user/password/project-id/cert filename/token) and passes `/secret-scan docs/runbooks/`.

## Verdict: PASS_WITH_FOLLOWUP

No CRITICAL. The subtask designs are sound and, as specced, keep private keys, URIs, hosts, and
Atlas bodies out of every sink. Merge-gate conditions (any unmet ⇒ treat as BLOCK for that
subtask):

- **Subtask 06 BLOCKS on ROT-1/2/3** (private-key leakage, HIGH) and requires ROT-4/ROT-5
  (dependency + CVE audit) before the new package lands.
- **Subtask 07 BLOCKS on ROT-6/7/8** (deleting the active identity, HIGH); ROT-9/10 are MEDIUM
  hardening.
- **Subtask 09 BLOCKS on ROT-11/12/13** (exception-leak regression + repoint-on-failure, HIGH) -
  this is the SEC-3/4/8 contract carried forward; do not re-implement the ping outside
  `client.py`.
- Subtasks 08 (ROT-14/15) and 10 (ROT-18) are MEDIUM/LOW doc-redaction gates enforced by
  `/secret-scan`.

Hand fixes to `python-expert` and regression tests (key-never-logged, prune-protects-active,
verify-leaves-settings-untouched, redacted-report) to `testing-expert`.

## Verification - task 02.1 close (2026-09-30)

As-built review of `git diff eb90685..05530f0`. Verdict: **PASS**. No CRITICAL/HIGH residuals.
Subtask 08 is blocked (no Atlas credentials); ROT-14/15 are **N/A** this round (no
`docs/reviews/*-x509-integration-run.md` was committed - confirmed absent).

Per-ROT status:

- **ROT-1 PASS** - `read_certificate_expiry` (cert_rotation.py:460-470) extracts only the first
  `CERTIFICATE` block via `_CERTIFICATE_PEM_PATTERN` and parses `match.group(0)`; the private-key
  block is never handed to `x509.load_pem_x509_certificate`. Returns `not_valid_after_utc` (tz-aware).
- **ROT-2 PASS** - every failure (OSError, no block, ValueError) raises `RotationError` with the
  path only (cert_rotation.py:455-468); OSError and ValueError use `from None`.
- **ROT-3 PASS** - no expiry path (`read_certificate_expiry`, `--check` _run_check, post-rotation
  expiry line, `cert_expiry_warning`) interpolates PEM/key bytes or the raw exception into any log,
  message, or console line.
- **ROT-4 PASS** - `cryptography>=50.0.1` pinned direct dep (pyproject.toml:14); `uv.lock`
  regenerated (resolves 50.0.1, +cffi 2.1.1, +pycparser 3.0) and committed.
- **ROT-5 PASS** - `cryptography` 50.0.1 has no open CVEs (dep-audit 2026-09-30). The 2026 advisories
  CVE-2026-39892 (<=46.0.6), CVE-2026-69249 (<49.0.0), CVE-2026-34073 (<46.0.6) are all fixed before
  50.0.1; CVE-2026-69249 is chain-verification DoS, not reachable by single-cert parse anyway.
- **ROT-6 PASS** - `_prune_after_rotation` (cert_rotation.py:379-392) builds protected = {new_cert} ∪
  {resolver cert *after* repoint} ∪ {TLS_CERT_FILE, expanduser}. Resolution runs after
  `update_settings_after_rotation`.
- **ROT-7 PASS** - `prune_old_certificates` (cert_rotation.py:420-423) canonicalizes both candidate
  (`candidate.resolve()`) and every protected path (`p.resolve()`) before the membership test;
  symlink/relative/`..` spellings of the active cert are still recognized as protected.
- **ROT-8 PASS** - a `config.ConfigError` from the post-repoint resolve short-circuits with a skip
  status line (cert_rotation.py:382-386); pruning never runs on uncertainty.
- **ROT-9 PASS** - candidates come solely from `config.CONFIG_DIR.glob("X509-cert-*.pem")`
  (cert_rotation.py:415-419); an `--output` cert outside CONFIG_DIR is never a candidate.
- **ROT-10 PASS** - deletion is `candidate.unlink()` on the matched glob path itself
  (cert_rotation.py:426), never resolve-then-delete; a failed unlink is a `WARNING:` line naming
  only the path + exception class, not an exception.
- **ROT-11 PASS** - `verify_certificate` (cert_rotation.py:230-244) builds only via
  `build_mongo_client(uri, str(cert_path), ca_file)` and `ping`; inherits SEC-3/4/8 redaction. No
  hand-rolled `MongoClient`, no raw pymongo error logged. Client closed in `finally` every attempt.
- **ROT-12 PASS** - on final `MongoConnectionError` (cert_rotation.py:344-353) the command returns
  `verified=False` without calling `update_settings_after_rotation` and without pruning; the new PEM
  stays on disk; `commands/rotate_cert.py:105-108` exits 1.
- **ROT-13 PASS** - the failure WARNING interpolates only the redacted `MongoConnectionError`
  (`_redact_pymongo_error` = class name + code/codeName; client.py:198-219) - no host/URI/DB
  user/Atlas body. Pruning runs strictly on the verified-success (or no-URI) branches, never on failure.
- **ROT-14 N/A** - subtask 08 blocked (no Atlas creds); no integration-run report committed.
- **ROT-15 N/A** - same; nothing to secret-scan this round.
- **ROT-16 PASS** - `--check` (`_run_check`) and the `resolve_store_backend` pre-flight
  (`_warn_if_cert_expiring` -> `cert_expiry_warning`) do local file reads only, contact no Atlas,
  need no `ATLAS_*`. Exit codes 0 / 3 / 1 as specified; `--keep` rejects <1 at parse time
  (`_positive_int`, cli.py) so pruning can never delete every cert.
- **ROT-17 PASS** - `cert_expiry_warning` (cert_rotation.py:489-504) is non-raising (catches
  `RotationError`, returns a path-only warning string); imported at storage.py:28 with no cycle.
- **ROT-18 PASS** - `docs/runbooks/x509-certificate-rotation.md` (410 lines) uses placeholders
  (`<...>`, 38 occurrences) throughout; secret scan found no real host/user/password/project-id/cert
  filename/token (the only `cloud.mongodb.com` reference is the public Atlas API base URL).

Residual risk (LOW, non-blocking): `--warn-days` and `--verify-attempts` use plain `type=int` (no
lower bound). A negative `--verify-attempts` is absorbed by `max(1, attempts)`; a negative
`--warn-days` only shifts the `--check` exit-3 threshold. Neither reaches a secret sink or the prune
path. No action required.
