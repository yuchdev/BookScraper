# 10 - Rotation runbook

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/02.1-x509-certificate-rotation/README.md)
**Status:** ⬜ Not started
**Depends on:** [06-certificate-expiry-inspection.md](/docs/roadmap/0001-generic-implementation/02.1-x509-certificate-rotation/06-certificate-expiry-inspection.md), [07-old-certificate-retention.md](/docs/roadmap/0001-generic-implementation/02.1-x509-certificate-rotation/07-old-certificate-retention.md), [09-verify-before-repoint.md](/docs/roadmap/0001-generic-implementation/02.1-x509-certificate-rotation/09-verify-before-repoint.md)
**Role:** Docs Writer

## Requirements

Create `docs/runbooks/x509-certificate-rotation.md` with these sections:

1. **Prerequisites** - how to create a Project-Owner-scoped Atlas API Service Account, which `ATLAS_*` variables it
   yields, and where they go (`.env`, git-ignored). Link to MongoDB's Atlas docs instead of copying them.
2. **First-time setup** - enabling X.509 for the DB user, the required URI parameters
   (`authMechanism=MONGODB-X509&authSource=$external`), and a `settings.json` example (link
   `docs/examples/settings.example.json` from task 02.0 subtask 06).
3. **Routine rotation** - the command, the expected output, and what happens in each `settings.json` mode (reuse
   the table from subtask 04).
4. **Scheduling** - a cron / launchd example that runs `rotate-cert --check --warn-days 30` weekly and alerts on
   exit code `3`.
5. **Recovery** - failed verification (subtask 09), accidental deletion, an expired cert, a leaked key (revoke in
   the Atlas UI, rotate, prune).
6. **Troubleshooting matrix** - symptom → likely cause → check → fix, seeded from subtask 08's re-diagnosis report.

Link the runbook from CLAUDE.md (*TLS certificate rotation pipeline*) and from `docs/README.md`.

## Files

- Create `docs/runbooks/x509-certificate-rotation.md`.
- Modify `CLAUDE.md`, `docs/README.md`.

## Tests

- `/link-check docs/runbooks/ CLAUDE.md` passes.
- `/secret-scan docs/runbooks/` passes (placeholders only).

## Success criteria

- [ ] A newcomer can rotate a cert end-to-end using only the runbook.
