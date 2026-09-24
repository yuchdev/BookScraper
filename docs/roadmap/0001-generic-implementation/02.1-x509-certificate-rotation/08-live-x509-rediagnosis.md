# 08 - Live X.509 re-diagnosis

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/02.1-x509-certificate-rotation/README.md)
**Status:** ⬜ Not started
**Role:** Testing Expert → Python Expert

## Context

CLAUDE.md marks the claim that *"certificate validation failed" (Atlas error 8000) is server-side* as
**UNVERIFIED**: it entered the docs with no recorded run behind it. As of 2026-09-23 this machine had no
`settings.json`, no cert, and no `TEST_MONGODB_URI_*` variables, so all eight integration tests **skipped**. On top
of that, `tests/integration/test_connection_tls.py` and the four x509 legs of `test_connection_settings.py` are
marked `@pytest.mark.xfail(strict=False)`, which would silently absorb a genuine *client-side* failure.

## Requirements

1. **Reproduce with real inputs** (manual; needs the user's credentials):
   - `uv run bookscraper rotate-cert` → fresh cert.
   - Confirm `TEST_MONGODB_URI_TLS` carries `authMechanism=MONGODB-X509&authSource=$external`. (Task 02.0
     subtask 03 logs a critical line if it doesn't.)
   - Run `uv run pytest -m integration -rxXs -v` and save the full output, **redacted** (hosts → `<cluster>`), to
     `docs/reviews/<date>-x509-integration-run.md`.
2. **Classify the outcome** in that report as exactly one of:
   - (a) **All pass** → remove every x509 `xfail` marker and the `X509_XFAIL_REASON` constants.
   - (b) **Client-side cause found** (missing mechanism, wrong `authSource`, cert/user mismatch, wrong file) → fix it
     in code or docs, then do (a).
   - (c) **Reproducible server-side rejection** → keep the marker but make it `strict=True` with
     `raises=pymongo.errors.OperationFailure`, and put the recorded error code/codeName in the reason string. A
     *different* failure mode then fails the suite instead of hiding.
3. Rewrite CLAUDE.md's *Status of the `certificate validation failed` report* section to match the recorded
   outcome and link the report. Update the *Testing* section's "Expected outcome" paragraph to match.
4. If the `TEST_MONGODB_URI_*` variables still can't be provided, the subtask stays 🔶 with the blocker written
   down. The markers must **not** be removed without a run.

## Files

- Create `docs/reviews/<date>-x509-integration-run.md`.
- Modify `tests/integration/test_connection_tls.py`, `tests/integration/test_connection_settings.py`.
- Modify `CLAUDE.md`.

## Tests

- The eight existing integration tests, run with `-m integration` against Atlas.
- New unit guard `test_no_non_strict_xfail_in_integration_tier` in `tests/unit/test_integration_helpers.py`. It
  AST-scans `tests/integration/*.py` and fails on any `xfail(` call that has `strict=False` or no `strict` argument.

## Success criteria

- [ ] A redacted, dated run log exists under `docs/reviews/`.
- [ ] No `xfail(strict=False)` remains in `tests/integration/`.
- [ ] CLAUDE.md states the root cause as observed, not as assumed.
