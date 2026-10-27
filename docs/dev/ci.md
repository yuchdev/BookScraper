# Continuous integration

All workflows live in `.github/workflows/`. Every third-party action is pinned to a full commit SHA (with the tag in a
trailing comment); resolve new SHAs with `gh api repos/<owner>/<repo>/git/ref/tags/<tag>`, never by guessing. Everything
installs from the frozen `uv.lock` (`uv sync --frozen`), so CI never resolves new versions.

## Workflows

| Workflow | File | Triggers | What it does |
|----------|------|----------|--------------|
| Lint | `lint.yml` | push to `master`, pull request | `ruff check` and `ruff format --check` with the pinned `ruff` |
| Tests | `tests.yml` | push to `master`, pull request | `uv run pytest --cov` (line+branch, `fail_under = 90`), the per-module floor (`scripts/check_module_coverage.py`), and a Chromium corpus tier that **fails if it skipped**. Matrix: ubuntu + macOS on Python 3.13 (widened by task 08.0/01). Uploads `coverage.xml`. |
| Integration | `integration.yml` | `workflow_dispatch`, weekly schedule — **never** pull requests | `pytest -m integration` against real Atlas, behind the `atlas-integration` Environment |
| Audit | `audit.yml` | weekly schedule, `workflow_dispatch` | `pip-audit --strict` on the locked dependencies; offline selector-drift check per site; optional live drift (`live: true`) that opens a `Selector drift: <site>` issue on exit `4` |

## Required checks on `master`

Recommended protection, applied by the repository owner (nothing here is automated):

- Require the **`lint`** and **`tests`** checks (all `tests` matrix legs) to pass before merging.
- Require linear history; block force-pushes and branch deletion.
- Do **not** require `integration` or `audit`: they need secrets or the network and run on a schedule.

## Re-running

Open the run under the repository's *Actions* tab and choose *Re-run failed jobs*. Manual runs of `integration.yml` and
`audit.yml`: *Actions → workflow → Run workflow* (or `gh workflow run integration.yml`; `gh workflow run audit.yml -f live=true`
for the live drift job).

## The `atlas-integration` Environment

1. *Settings → Environments → New environment* named `atlas-integration`; add **required reviewers**.
2. Add Environment secrets `TEST_MONGODB_URI_PASS`, `TEST_MONGODB_URI_TLS` and `TEST_X509_CERT_PEM` (the full PEM,
   key plus certificate).
3. Run the workflow once by hand. With any secret missing it fails with `integration secrets not configured` — by design,
   so an all-skipped run can never look green.

## Secret-handling rules

Full reasoning: [`docs/security/2026-10-26-ci-integration-secrets.md`](/docs/security/2026-10-26-ci-integration-secrets.md).

- Credentialed jobs trigger only on `workflow_dispatch`/`schedule`; never `pull_request`, `pull_request_target` or `push`.
- Secrets are passed through step-scoped `env:` only, never workflow- or job-level, and never echoed; no `set -x`.
- The X.509 PEM is written with `umask 077` under `$RUNNER_TEMP/home/.bookscrapper/` and shredded in an `always()` step.
- `permissions` default to `contents: read`; `issues: write` is granted only to the live drift job.
