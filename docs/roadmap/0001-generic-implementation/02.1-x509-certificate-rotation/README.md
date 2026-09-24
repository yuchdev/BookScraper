# Task 02.1 - X.509 Certificate Rotation

**Parent milestone:** [plan.md](/docs/roadmap/0001-generic-implementation/plan.md)
**Status:** 🔶 In progress
**Category:** feature | **Priority:** P0
**Depends on:** [Task 02.0](/docs/roadmap/0001-generic-implementation/02.0-mongodb-auth-configuration/README.md)

## Scope

`bookscraper rotate-cert` (`commands/rotate_cert.py` → `backends/mongo/cert_rotation.py`) issues a fresh
Atlas-managed X.509 client certificate through the Atlas Admin API. It authenticates as an OAuth2 client-credentials
Service Account configured by `ATLAS_CLIENT_ID` / `ATLAS_CLIENT_SECRET` / `ATLAS_PROJECT_ID` / `ATLAS_DB_USER`
(`.env` or the environment). The cert is written atomically and owner-only to
`~/.bookscrapper/X509-cert-<UTC>.pem`, and `settings.json` is repointed when it references the cert by literal
filename.

Subtasks 01-05 record the delivered pipeline. Subtasks 06-10 make it safe to run unattended:
- Know when a cert is about to expire.
- Stop old keys from piling up.
- Never repoint config at a cert that can't actually authenticate.
- Settle the open *certificate validation failed* (Atlas 8000) question with a recorded run.
- Give operators a runbook.

## Subtasks

| #  | Document                                                                         | Status         | Blocks |
|----|----------------------------------------------------------------------------------|----------------|--------|
| 01 | [Atlas Admin API client](/docs/roadmap/0001-generic-implementation/02.1-x509-certificate-rotation/01-atlas-admin-api-client.md)                      | ✅ Complete    | 05     |
| 02 | [Months & output resolution](/docs/roadmap/0001-generic-implementation/02.1-x509-certificate-rotation/02-months-and-output-resolution.md)            | ✅ Complete    | 05     |
| 03 | [Atomic owner-only write](/docs/roadmap/0001-generic-implementation/02.1-x509-certificate-rotation/03-atomic-owner-only-write.md)                    | ✅ Complete    | 04     |
| 04 | [settings.json repointing](/docs/roadmap/0001-generic-implementation/02.1-x509-certificate-rotation/04-settings-json-repointing.md)                  | ✅ Complete    | 05, 09 |
| 05 | [`rotate-cert` command](/docs/roadmap/0001-generic-implementation/02.1-x509-certificate-rotation/05-rotate-cert-command.md)                          | ✅ Complete    | 06, 07 |
| 06 | [Certificate expiry inspection](/docs/roadmap/0001-generic-implementation/02.1-x509-certificate-rotation/06-certificate-expiry-inspection.md)        | ⬜ Not started | 10     |
| 07 | [Old-certificate retention](/docs/roadmap/0001-generic-implementation/02.1-x509-certificate-rotation/07-old-certificate-retention.md)                | ⬜ Not started | 10     |
| 08 | [Live X.509 re-diagnosis](/docs/roadmap/0001-generic-implementation/02.1-x509-certificate-rotation/08-live-x509-rediagnosis.md)                      | ⬜ Not started | 09     |
| 09 | [Verify before repoint](/docs/roadmap/0001-generic-implementation/02.1-x509-certificate-rotation/09-verify-before-repoint.md)                        | ⬜ Not started | 10     |
| 10 | [Rotation runbook](/docs/roadmap/0001-generic-implementation/02.1-x509-certificate-rotation/10-rotation-runbook.md)                                  | ⬜ Not started | -      |

## Key constraints

- **Security-sensitive throughout.** Every ⬜ subtask gets a `security-auditor` pass before coding. Atlas response
  bodies, tokens, and private keys never appear in logs, output, or exception messages. HTTP errors report only the
  status code.
- **Never modify the user's environment or `.env`.** With `x509.cert.source: "env"`, only print a reminder.
- **Never delete, or repoint away from, a working identity** on the strength of an unverified new one
  (subtasks 07, 09).
- `cert_rotation.py` returns status lines. `commands/rotate_cert.py` owns all user-facing output.
