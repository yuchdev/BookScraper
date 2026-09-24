# 04 - Manual integration workflow

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/06.2-continuous-integration/README.md)
**Status:** ⬜ Not started
**Role:** Security Auditor → Testing Expert

## Requirements

- `.github/workflows/integration.yml`, triggered by `workflow_dispatch` and a weekly `schedule`. **Never** by
  `pull_request`.
- `environment: atlas-integration` (a GitHub Environment with required reviewers) holding secrets
  `TEST_MONGODB_URI_PASS`, `TEST_MONGODB_URI_TLS`, `TEST_X509_CERT_PEM`.
- Job steps:
  - Set `HOME` to `$RUNNER_TEMP/home`. Write `TEST_X509_CERT_PEM` to `$HOME/.bookscrapper/X509-cert-ci.pem` with
    `umask 077`. Never `echo` it, and add `::add-mask::` for any derived value.
  - `uv run pytest -m integration -rxXs`.
  - `always()` cleanup: shred and delete the cert file.
- If a secret is missing, the tests **skip** (the existing `require_env` behavior). The workflow then fails with an
  explicit "integration secrets not configured" message, so an all-skipped run can't look green.
- Pairs with task 02.1 subtask 08. Once the x509 root cause is recorded, this workflow is its ongoing regression
  guard.

## Files

- Create `.github/workflows/integration.yml`.
- Create `docs/security/<date>-ci-integration-secrets.md` (security-auditor).
- Modify `CLAUDE.md` (*Testing*: how to run the integration tier in CI).

## Tests

- A `workflow_dispatch` run with secrets configured (noted in the PR).
- A run without secrets fails with the explicit message, not green.

## Success criteria

- [ ] No secret value appears in any job log (verify by searching the log for a sentinel).
