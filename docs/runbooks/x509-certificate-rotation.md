# X.509 Certificate Rotation Runbook

**Covers:** `bookscraper rotate-cert` — issuing, verifying, and activating a fresh Atlas-managed
X.509 client certificate; expiry inspection; scheduled alerting; and recovery procedures.

**See also:**
- [Authentication configuration](../../CLAUDE.md#authentication-configuration) in `CLAUDE.md`
  (settings.json schema and all six valid auth combinations)
- [Authentication configuration — TLS certificate rotation pipeline](../../CLAUDE.md#authentication-configuration)
  in `CLAUDE.md`
- [`docs/examples/settings.example.json`](../examples/settings.example.json) — annotated
  `settings.json` reference

---

## Contents

1. [Prerequisites](#prerequisites)
2. [First-time setup](#first-time-setup)
3. [Routine rotation](#routine-rotation)
4. [Scheduling](#scheduling)
5. [Recovery](#recovery)
6. [Troubleshooting matrix](#troubleshooting-matrix)

---

## Prerequisites

### Atlas API Service Account

`rotate-cert` needs a **Project Owner**-scoped Atlas API Service Account to call
`POST .../databaseUsers/{user}/certs`. Follow the MongoDB Atlas documentation to create one:

- [Create a Service Account](https://www.mongodb.com/docs/atlas/iam/service-accounts/) — select
  **Project Owner** as the role on the target project.

The account yields four values. Put them in `.env` at the repo root (git-ignored) or export
them in your environment:

```dotenv
ATLAS_CLIENT_ID=<service-account-client-id>
ATLAS_CLIENT_SECRET=<service-account-client-secret>
ATLAS_PROJECT_ID=<project-id>
ATLAS_DB_USER=<dbuser>
```

> The DB user (`ATLAS_DB_USER`) must be configured in Atlas as an **Atlas-managed X.509** user
> (x509Type `MANAGED`). BYOC (bring-your-own-CA) users are not supported by this command.

Optional overrides (also settable as flags):

```dotenv
ATLAS_CERT_MONTHS=6      # validity in months, 1-24; default 6
ATLAS_CERT_FILE=         # output path; default: timestamped file under ~/.bookscrapper/
```

### MongoDB connection

Before rotating you must have a working MongoDB URI. For X.509 auth the URI **must** include:

```
?authMechanism=MONGODB-X509&authSource=$external
```

pymongo never infers the auth mechanism from a client certificate. Without these parameters the
connection will fail or be silently unauthenticated. The app logs a critical diagnostic (via
`client._warn_if_x509_mechanism_missing`) if a cert is configured but the URI omits them.

---

## First-time setup

### 1. Enable Atlas-managed X.509 for the DB user

In the Atlas UI go to **Database Access → Database Users**, find (or create) the user, set
**Authentication Method → Certificate**, and choose **Atlas-managed** (not BYOC).

### 2. Configure the URI

The connection string from Atlas includes `authMechanism=MONGODB-X509&authSource=$external`.
Verify yours contains both parameters before continuing.

### 3. Write `~/.bookscrapper/settings.json`

Use `docs/examples/settings.example.json` as a template. A minimal X.509 configuration:

```json
{
  "auth_type": "x509",
  "x509": {
    "uri": {"source": "env", "value": "MONGODB_URI"},
    "cert": {"source": "literal", "value": "X509-cert-example.pem"}
  }
}
```

Replace `X509-cert-example.pem` with the filename that rotation will write (or omit it for now
and use `--no-verify` on the first run — see [Routine rotation](#routine-rotation)). Restrict
permissions:

```bash
chmod 600 ~/.bookscrapper/settings.json
```

The file must be a regular (non-symlink), owner-only file. The app warns if it is
group/other-readable, is a symlink, or is owned by another user.

### 4. Run rotation for the first time

```bash
cd /path/to/BookScraper
uv run bookscraper rotate-cert --no-verify
```

`--no-verify` skips pinging Atlas with the new cert — useful when `settings.json` does not yet
reference a cert and there is nothing to verify against. The new PEM is written to
`~/.bookscrapper/X509-cert-<timestamp>.pem`. Update `settings.json`'s `x509.cert.value` to that
filename, then run a plain `rotate-cert` (with verification) to confirm it works:

```bash
uv run bookscraper rotate-cert --check
```

Exit `0` means the cert is valid. Proceed to [Routine rotation](#routine-rotation).

---

## Routine rotation

### Basic command

```bash
cd /path/to/BookScraper
uv run bookscraper rotate-cert
```

**What happens:**

1. Loads `.env` (so `ATLAS_*` and `MONGODB_URI` are available).
2. Acquires an OAuth2 access token from the Atlas API using `ATLAS_CLIENT_ID` /
   `ATLAS_CLIENT_SECRET`.
3. Calls `POST .../groups/<project-id>/databaseUsers/<dbuser>/certs` to issue a new
   Atlas-managed certificate valid for 6 months (or `--months N`).
4. Writes the combined key+cert PEM to
   `~/.bookscrapper/X509-cert-<YYYYMMDD_HHMMSS>.pem` (or `--output`), owner-only (`0600`).
5. Prints the new cert's expiry date.
6. **Verifies** the new cert authenticates to Atlas (pings up to 5 times, 6 s apart).
   Atlas can take a short while to propagate a freshly issued certificate.
7. On a successful ping: repoints `settings.json` (see table below), then prunes superseded
   `X509-cert-*.pem` files (keeps the newest `--keep N`, default 2, plus the active cert).
8. On a failed ping: leaves `settings.json` untouched (previous cert keeps working), leaves the
   new PEM on disk, prints a `WARNING:` line with the redacted error code, and exits `1`.

### settings.json repointing behaviour

| `settings.json` state            | Action after successful verification              |
|----------------------------------|---------------------------------------------------|
| Absent                           | Untouched; legacy glob picks up new cert on next run |
| Malformed / unreadable           | Untouched; `WARNING:` line                        |
| `auth_type != "x509"`            | Untouched; informational note                     |
| `x509.cert.source == "literal"`  | `x509.cert.value` set to new filename; atomic `0600` rewrite |
| `x509.cert.source == "env"`      | Untouched; reminder names the env var and new path |
| Any other `x509.cert` shape      | Untouched; "unrecognized shape" note              |

> When `x509.cert.source == "env"`, update the named environment variable (or `.env`) yourself
> to the new path. The command deliberately never modifies your environment or `.env`.

### Common flag combinations

```bash
# 12-month cert instead of the default 6:
uv run bookscraper rotate-cert --months 12

# Write to an explicit path (not auto-pruned — outside ~/.bookscrapper/ candidates):
uv run bookscraper rotate-cert --output ~/.bookscrapper/X509-cert-manual.pem

# Retry verification 8 times instead of 5 (e.g. on a slow-propagating Atlas region):
uv run bookscraper rotate-cert --verify-attempts 8

# Skip verification (offline / first-time / re-keying on a secondary machine):
uv run bookscraper rotate-cert --no-verify
# Note: --no-verify also suppresses pruning — old certs are kept to avoid lockout.

# Keep 5 certs instead of the default 2 (useful on shared deployments):
uv run bookscraper rotate-cert --keep 5

# Keep all certs (no pruning):
uv run bookscraper rotate-cert --no-prune

# Check expiry only (no Atlas contact, no ATLAS_* needed):
uv run bookscraper rotate-cert --check
uv run bookscraper rotate-cert --check --warn-days 14
```

### Exit codes

| Code | Meaning |
|------|---------|
| `0`  | Rotation succeeded (or `--check` with more than `--warn-days` remaining, or no cert configured) |
| `1`  | Rotation failed, or `--check` found the cert expired / unreadable |
| `3`  | `--check` only: cert expires within `--warn-days` — rotate soon |

---

## Scheduling

### Expiry check via cron

Run a weekly expiry check. Alert when exit code is `3` (within the warning window) or `1`
(expired/unreadable). The check is a local file read only — it contacts nothing and needs no
`ATLAS_*` variables.

```cron
# Weekly expiry check — alert when exit 3 (warn window) or 1 (expired)
0 9 * * 1  cd /path/to/BookScraper && uv run bookscraper rotate-cert --check --warn-days 30 \
           || /usr/local/bin/sendmail -t <<'EOF'
To: ops@example.invalid
Subject: bookscraper X.509 cert expiry alert (exit $?)
Run: bookscraper rotate-cert
EOF
```

Or with a simpler notifier:

```bash
# In /etc/cron.d/bookscraper or a user crontab:
0 9 * * 1  cd /path/to/BookScraper && uv run bookscraper rotate-cert --check --warn-days 30; \
           CODE=$?; [ "$CODE" -ne 0 ] && echo "rotate-cert --check exited $CODE" | mail -s "bookscraper cert alert" ops@example.invalid
```

### Expiry check via launchd (macOS)

Save as `~/Library/LaunchAgents/net.example.invalid.bookscraper-cert-check.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>net.example.invalid.bookscraper-cert-check</string>

    <key>ProgramArguments</key>
    <array>
        <!-- uv needs the project root; WorkingDirectory sets it -->
        <string>/usr/local/bin/uv</string>
        <string>run</string>
        <string>bookscraper</string>
        <string>rotate-cert</string>
        <string>--check</string>
        <string>--warn-days</string>
        <string>30</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/path/to/BookScraper</string>

    <!-- Run every Monday at 09:00 -->
    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key>
        <integer>1</integer>
        <key>Hour</key>
        <integer>9</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>/tmp/bookscraper-cert-check.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/bookscraper-cert-check.err</string>
</dict>
</plist>
```

Load it:

```bash
launchctl load ~/Library/LaunchAgents/net.example.invalid.bookscraper-cert-check.plist
```

> `uv run` must be executed from the project directory (or pass `--project /path/to/BookScraper`)
> so it finds the correct `pyproject.toml` and `.venv`. The `WorkingDirectory` key above handles
> this for launchd. For cron, `cd /path/to/BookScraper &&` before `uv run` achieves the same.

To alert on exit `3`/`1`, wrap the program in a shell script that checks `$?` and sends a
notification, then point `ProgramArguments` at that script.

---

## Recovery

### Verification failed after rotation

When `rotate-cert` exits `1` with `WARNING: the new certificate at ... failed verification`:

1. The new PEM is on disk but `settings.json` has **not** been repointed. The previous cert
   keeps working.
2. Check [Troubleshooting matrix](#troubleshooting-matrix) for the error code in the warning.
3. If the cause is Atlas propagation lag, retry:

   ```bash
   uv run bookscraper rotate-cert --verify-attempts 10
   ```

4. If Atlas is unreachable or you know the cert is valid, repoint without verification:

   ```bash
   uv run bookscraper rotate-cert --no-verify
   ```

   **Note:** `--no-verify` also suppresses pruning — old certs are kept until the next verified
   rotation succeeds.

5. If the new cert is genuinely invalid (wrong DB user, wrong project), issue a fresh one:

   ```bash
   uv run bookscraper rotate-cert
   ```

### Accidental deletion of the active cert

If `~/.bookscrapper/X509-cert-<timestamp>.pem` is deleted and the app can no longer connect:

1. Issue a fresh cert:

   ```bash
   uv run bookscraper rotate-cert --no-verify
   ```

   `--no-verify` is required here because the previously configured cert (now missing) can no
   longer authenticate. After rotation, update `settings.json` manually if needed (or the
   `literal` repointing does it automatically).

2. Verify the new cert works:

   ```bash
   uv run bookscraper rotate-cert --check
   ```

### Expired cert

The pre-flight expiry warning (`resolve_store_backend("mongo")`) logs a warning before
connecting. `--check` reports the expiry date. To renew:

```bash
uv run bookscraper rotate-cert
```

If the cert is so expired that Atlas rejects the verification ping, use `--no-verify`:

```bash
uv run bookscraper rotate-cert --no-verify
```

Then immediately verify the new cert can connect:

```bash
uv run bookscraper rotate-cert --check
```

### Leaked private key

1. **Revoke in the Atlas UI:** Database Access → the DB user → Certificates → revoke the
   compromised cert.
2. Issue a fresh cert:

   ```bash
   uv run bookscraper rotate-cert
   ```

3. Confirm the compromised file is removed. Pruning keeps the newest `--keep` (default 2) plus
   the active cert. To forcibly remove all old files in one rotation:

   ```bash
   uv run bookscraper rotate-cert --keep 1
   ```

   Or list and manually delete:

   ```bash
   ls -lt ~/.bookscrapper/X509-cert-*.pem
   rm ~/.bookscrapper/X509-cert-<old-timestamp>.pem
   ```

4. Rotate any other credential that touched the same machine (API keys, SCRAM passwords).

---

## Troubleshooting matrix

| Symptom | Likely cause | Check | Fix |
|---------|-------------|-------|-----|
| `ATLAS_CLIENT_ID not set` (or any `ATLAS_*`) | Missing `.env` entry or env var | `echo $ATLAS_CLIENT_ID` | Add the variable to `.env` or export it; see [Prerequisites](#prerequisites) |
| `Atlas API request to ... failed with HTTP 401` | Wrong `ATLAS_CLIENT_ID` / `ATLAS_CLIENT_SECRET`, or Service Account lacks Project Owner role | Atlas UI → Access Manager → Service Accounts | Regenerate credentials; verify the account has **Project Owner** on the target project |
| `Atlas API request to ... failed with HTTP 404` | Wrong `ATLAS_PROJECT_ID` or `ATLAS_DB_USER`, or user is BYOC not Atlas-managed | Atlas UI → Database Access → confirm user exists and x509Type is `MANAGED` | Correct `ATLAS_PROJECT_ID` / `ATLAS_DB_USER` in `.env` |
| `Certificate validity must be 1-24 months` | `--months` or `ATLAS_CERT_MONTHS` out of range | — | Pass a value in `[1, 24]` |
| `Could not reach the Atlas API: ConnectError` | Network / firewall blocking `cloud.mongodb.com` | `curl -s https://cloud.mongodb.com/api/atlas/v2` | Check network/proxy; Atlas Admin API requires outbound HTTPS on port 443 |
| `TLS client certificate file not found: <path>` | `settings.json` points at a deleted or renamed cert, or `TLS_CERT_FILE` is stale | `ls ~/.bookscrapper/X509-cert-*.pem` | Run `rotate-cert --no-verify` to issue a new cert; update `settings.json` or `TLS_CERT_FILE` |
| `WARNING: the TLS client certificate <path> expired` | Cert past its expiry date | `rotate-cert --check` | `rotate-cert` (or `--no-verify` if Atlas rejects the old cert during verification) |
| `WARNING: the new certificate at <path> failed verification (OperationFailure 8000 AtlasError)` | <!-- SME REVIEW NEEDED --> Root cause **UNVERIFIED** — pending subtask 08 live re-diagnosis (no Atlas credentials on this machine as of 2026-09-30). Possible causes: (a) Atlas propagation lag — new cert not yet visible; (b) wrong `authMechanism`/`authSource` in URI; (c) cert issued for wrong user/project; (d) server-side rejection (claimed but unconfirmed) | Check the URI for `?authMechanism=MONGODB-X509&authSource=$external`; retry with `--verify-attempts 10`; confirm `ATLAS_DB_USER` matches the Atlas user | Add `?authMechanism=MONGODB-X509&authSource=$external` to URI; or `rotate-cert --verify-attempts 10`; if still failing, `rotate-cert --no-verify` then re-diagnose |
| `Warning: TLS certificate configured but the URI does not request MONGODB-X509 authentication` | URI is missing `authMechanism=MONGODB-X509&authSource=$external` — `client._warn_if_x509_mechanism_missing` logged this | Inspect URI: `echo $MONGODB_URI` | Append `?authMechanism=MONGODB-X509&authSource=$external` (or `&` if query string already has params) to the URI |
| `settings.json: unrecognized top-level key(s): <key>` (`ConfigError`) | Typo in `settings.json` (e.g. `"x059"` instead of `"x509"`) | `cat ~/.bookscrapper/settings.json` | Fix the typo; allowed top-level keys: `auth_type`, `pass`, `x509`, `database` |
| `settings.json: 'auth_type' must be 'pass' or 'x509', got ...` (`ConfigError`) | Wrong or missing `auth_type` value | `cat ~/.bookscrapper/settings.json` | Set `"auth_type"` to `"pass"` or `"x509"` |
| `~/.bookscrapper/settings.json is readable by group/other` (warning) | File permissions too open | `ls -l ~/.bookscrapper/settings.json` | `chmod 600 ~/.bookscrapper/settings.json` |
| `~/.bookscrapper/settings.json is a symlink` (warning) | `settings.json` is a symbolic link | `ls -la ~/.bookscrapper/settings.json` | Replace with a regular file: `cp settings.json settings.json.bak && rm settings.json && cp settings.json.bak settings.json && chmod 600 settings.json` |
| `~/.bookscrapper/settings.json is not owned by the current user` (warning) | File owned by a different UID | `ls -ln ~/.bookscrapper/settings.json` | Recreate the file as your own user: copy, delete original, `chmod 600` |
| `Skipping cert pruning: the active certificate could not be resolved` | `ConfigError` from `config.resolve_mongo_connection()` after repointing | Fix `settings.json` errors (see ConfigError rows above), then run `rotate-cert` again | Resolve the `settings.json` issue first; pruning skips rather than deleting on uncertainty |

---

## Behavior reference

This section documents the code-level behavior of `commands/rotate_cert.py` and
`backends/mongo/cert_rotation.py` for developers and on-call engineers.

### Verify-before-repoint

After the new PEM is written but *before* `settings.json` is repointed,
`cert_rotation.verify_certificate()` pings Atlas with the new cert. Verification builds a client
**only** via `backends/mongo/client.build_mongo_client(uri, new_cert, TLS_CA_FILE)` and pings via
`client.ping` — reusing the single client factory so it inherits the redaction contract (a
failure surfaces as a `MongoConnectionError` carrying only the pymongo `code`/`codeName`, never
the URI, host, DB user, cert contents, or Atlas response body).

The URI is `config.resolve_mongo_connection()[0]`; if none is configured, verification is skipped
with a status line. Atlas can take a short while to propagate a freshly issued cert, so a
connection failure is retried up to `--verify-attempts N` (default `5`) times with a fixed delay,
closing the client after every attempt.

On **final** failure: `settings.json` is **not** repointed (the previously configured cert keeps
working), the new PEM is left on disk, a `WARNING:` line names the new path and the redacted
error code, and the command exits `1`. If `config.resolve_mongo_connection()` raises `ConfigError`,
rotation fails loudly (`RotationError`, exit `1`) without repointing.

### Old-certificate pruning

After a successful (or skipped) verification, `cert_rotation.prune_old_certificates()` removes
superseded certs, keeping only the newest `--keep N` (default `2`, must be ≥ 1) plus the active
cert.

The **protected** set is: `{cert just written} ∪ {cert resolved by config after repointing} ∪
{TLS_CERT_FILE if set}`. Candidates and protected paths are compared canonically (`Path.resolve()`)
so a symlink-, relative-, or `..`-spelled reference to the active cert is never deleted.

Candidates come **only** from `config.CONFIG_DIR.glob("X509-cert-*.pem")` — a `--output` cert
written elsewhere is never pruned. The matched glob path is unlinked directly (a symlink
candidate's target outside the directory is left alone); a failed unlink is a `WARNING:` status
line, not a crash.

Pruning is **skipped entirely** (with a status line) if the active cert cannot be resolved
(`ConfigError`). `--no-prune` disables pruning. `--no-verify` also suppresses pruning: without a
positive authentication signal, old certs are kept to avoid a self-inflicted lockout.

### Expiry and pre-flight warning

`cert_rotation.read_certificate_expiry()` and `cert_rotation.cert_expiry_warning()` parse **only
the first `CERTIFICATE` PEM block** of the combined key+cert file (via the `cryptography`
package). The private-key block is never read or logged; a missing file / absent-or-unparseable
certificate block raises `RotationError` naming only the file path, never its contents.

`resolve_store_backend("mongo")` runs a non-raising pre-flight call to
`cert_rotation.cert_expiry_warning(cert_path, warn_days=30)` and `print_log`s any warning
*before* connecting; a parse failure there is a warning, never a crash.

After a successful (or skipped) verification, the command also prints the **new** cert's expiry
date, parsed from the just-written file — this line appears right after
`Certificate written to <path>` and before the verification/repoint status lines.

### Atlas error 8000 status — UNVERIFIED

Earlier revisions stated as fact that Atlas rejects this cluster's X.509 certificates
server-side. That conclusion is not currently reproducible and was never backed by a recorded
run. As of 2026-09-23, `~/.bookscrapper/` contains only `logs/` (no `settings.json`, no
`X509-cert-*.pem`), so no client certificate is ever presented to Atlas, and the eight
`tests/integration/` tests **skip** at the first gate. The root cause is still open —
re-diagnose from a real run before repeating the "server-side issue" claim. The `xfail(strict=False)`
markers on the x509 integration legs are inert until credentials return.
