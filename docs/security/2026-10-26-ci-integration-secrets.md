# Threat Model - CI integration workflow secrets (task 06.2, subtask 04) - 2026-10-26

Written **before coding** as the security-auditor merge gate for
[subtask 04](/docs/roadmap/0001-generic-implementation/06.2-continuous-integration/04-integration-workflow.md)
(parent [06.2](/docs/roadmap/0001-generic-implementation/06.2-continuous-integration/README.md)). Scope is the new
`.github/workflows/integration.yml`, which is the only place in the repo that hands **real Atlas credentials** to an
automated runner. The offline `tests.yml` / `lint.yml` (both `permissions: contents: read`, SHA-pinned actions) carry
no secrets and are the baseline this extends rather than restates. Findings are numbered **CI-1..CI-10**; each maps to
a concrete mitigation the workflow MUST implement, listed in the mapping table at the end (the subtask success
criterion). No product code is in scope - the integration tier already resolves credentials from `.env`/env and the
cert via `mongo_test_helpers.get_tls_cert_file()` (env `TLS_CERT_FILE`, else newest `~/.bookscrapper/X509-cert-*.pem`);
the workflow only has to populate `HOME` and the environment the existing suite reads.

## Assets & trust boundaries

- **Assets:** `TEST_MONGODB_URI_PASS` and `TEST_MONGODB_URI_TLS` (each a `mongodb+srv://` URI embedding a live Atlas
  SCRAM password / pointing at a real cluster - a database credential); `TEST_X509_CERT_PEM` (a live X.509 **client
  private key + certificate** - filesystem-equivalent-to-credentials per the project's X.509 posture); the Atlas test
  cluster and its `bookscraper_db` data; the GitHub Actions job logs (public on a public repo); the GitHub Environment
  `atlas-integration` protection itself.
- **Boundary A (GitHub secret store -> runner process):** secrets are injected only when the job runs in the
  `atlas-integration` Environment and its required reviewers approve. The approval gate is the primary authorization
  control; everything downstream assumes a reviewer consciously released the credentials for this ref.
- **Boundary B (runner env/files -> test process):** the URIs reach pytest via `env:`; the PEM is materialized to
  `$RUNNER_TEMP/home/.bookscrapper/X509-cert-ci.pem` and discovered by the production glob resolver. This is the
  write/cleanup boundary - a leftover PEM on a reused/self-hosted runner is a credential at rest.
- **Boundary C (test process -> job log):** pytest output (`-rxXs`), pymongo connection/auth errors, and any `echo`
  of a derived value are the disclosure surface. Logs are the one Boundary-B artifact that persists publicly.
- **Boundary D (trigger -> credential release):** who/what can cause the job to run at all. `pull_request` /
  `pull_request_target` from a fork is the classic path to exfiltrate CI secrets; this workflow must be unreachable
  that way.
- **Trust posture:** GitHub-hosted ephemeral runner trusted for the job's lifetime only; fork contributors and
  unapproved refs are untrusted; the Atlas test cluster is a real external service; job logs are world-readable.

## Findings

### [HIGH] CI-1 Fork / event-triggered secret exfiltration
- Vector / evidence: Boundary D. If the workflow were reachable by `pull_request_target` (or `pull_request` with
  secrets), a fork PR could add a step that prints or POSTs the three secrets. `pull_request_target` runs with the
  base repo's secrets against attacker-controlled head code - the standard CI-secret theft primitive.
- Impact: full disclosure of the Atlas test URIs and the X.509 client key to any fork author. CWE-200, CWE-285,
  OWASP CICD-SEC-4 (Poisoned Pipeline Execution).
- Mitigation (MUST): `on:` is **exactly** `workflow_dispatch` + `schedule` (weekly cron) and **nothing else** - never
  `pull_request`, `pull_request_target`, `push`, `issue_comment`, or `workflow_call`. Secrets live only in the
  `atlas-integration` Environment with **required reviewers**, so even a dispatch must be human-approved before the
  job sees a secret. No step runs untrusted PR code (checkout is of the dispatched ref, not a PR head).

### [HIGH] CI-2 X.509 private key leaked via lax file permissions or leftover at rest
- Vector / evidence: Boundary B. The PEM is a private key. Writing it world/group-readable, or leaving it on a runner
  that is reused (self-hosted) or whose `$RUNNER_TEMP` outlives the job, is a credential-at-rest disclosure. A
  non-`always()` cleanup leaves the key behind whenever a prior step fails.
- Impact: Atlas client identity compromise (database access). CWE-312, CWE-276, CWE-459.
- Mitigation (MUST): set `HOME=$RUNNER_TEMP/home`; create `$HOME/.bookscrapper` and write the PEM under `umask 077`
  (the whole write step runs `umask 077` before the redirect) so the file is `0600`; write via a heredoc/`printenv`
  redirect from the secret env var, **never** an `echo`/`cat` of the literal on the command line. An `if: always()`
  final step `shred -u` (fallback `rm -f`) the PEM and removes `$HOME/.bookscrapper`. Prefer a GitHub-hosted
  ephemeral runner (documented) so `$RUNNER_TEMP` is destroyed with the VM.

### [HIGH] CI-3 Secret value printed into a public job log
- Vector / evidence: Boundary C. `echo "$TEST_X509_CERT_PEM"`, `set -x` over a step that expands a secret, a pymongo
  exception rendering the full `mongodb+srv://user:pass@host` URI, or `pytest -rxXs` surfacing a value in an assertion
  repr. GitHub masks registered secret values but **not** values *derived* from them (a base64 slice, a host parsed
  out of the URI).
- Impact: credential disclosure in a world-readable log. CWE-532, CWE-209, OWASP A09.
- Mitigation (MUST): never `echo`/`cat` a secret or a value derived from one; no `set -x` on secret-bearing steps;
  `::add-mask::` any derived value the job must compute (e.g. a cert fingerprint). The product path already redacts
  pymongo errors to class + `code`/`codeName` via `client.ping()` / `MongoConnectionError`, but the standalone
  `mongo_test_helpers.run_connection_test` prints the raw `ConnectionFailure`/`PyMongoError` text and only
  `mask_uri`s the URI it explicitly logs - treat its exception prints as a potential URI/host sink and rely on
  GitHub secret masking plus the no-`set -x` rule; do not add a step that re-prints the URI unmasked. Success
  criterion: a sentinel grep of the finished log finds no secret substring.

### [HIGH] CI-4 All-skipped run reads green (silent loss of the regression guard)
- Vector / evidence: `require_env` (`tests/integration/conftest.py:30`) and the cert resolver **skip** (not fail)
  when a credential is absent. If the Environment secrets are unset/misconfigured, every integration test skips and
  `pytest` exits `0` - the workflow looks healthy while testing nothing, exactly the "silently staying green" failure
  the project's testing policy warns against (CLAUDE.md, the xfail note).
- Impact: false assurance - the x509 regression guard (its whole purpose per the subtask) is silently disabled.
  CWE-754.
- Mitigation (MUST): a pre-flight guard step asserts the three secrets are non-empty **before** running pytest and
  exits `1` with an explicit `::error::integration secrets not configured` message if any is missing (check presence,
  never print the value). Independently, run `pytest -m integration -rxXs` and fail the job if the run collected zero
  non-skipped integration tests (e.g. assert the summary shows passed/xfailed, not all-skipped) so a mid-run skip
  storm also cannot pass as green.

### [MEDIUM] CI-5 Over-privileged `GITHUB_TOKEN`
- Vector / evidence: Boundary A. A default or `write` token on a secret-bearing, human-approved job widens the blast
  radius of any compromised step (it could push code, publish artifacts, or edit the Environment).
- Impact: privilege escalation from a poisoned dependency/step to repo write. CWE-250, OWASP CICD-SEC-5.
- Mitigation (MUST): top-level `permissions: contents: read` (the job reads only the checked-out tree, like
  `tests.yml`/`lint.yml`); no `id-token`/`packages`/`contents: write` unless a later need is justified in the file.

### [MEDIUM] CI-6 Unpinned / mutable action references (supply chain)
- Vector / evidence: Boundary B. A `@v4`/`@main` action tag is mutable; a compromised upstream tag running in a
  secret-bearing job can read `TEST_X509_CERT_PEM` from the runner. Both existing workflows already pin by full
  commit SHA.
- Impact: third-party code with access to the live credentials. CWE-829, OWASP CICD-SEC-3.
- Mitigation (MUST): pin every `uses:` to a full 40-char commit SHA with a `# vX.Y.Z` comment, reusing the exact
  SHAs already vetted in `tests.yml` (`actions/checkout`, `astral-sh/setup-uv`). Run `uv sync --frozen` so no new
  dependency versions resolve on a credentialed runner.

### [MEDIUM] CI-7 Cron schedule as an unattended credential-release trigger
- Vector / evidence: Boundary D. The weekly `schedule` runs unattended; required-reviewer gates do **not** apply to
  scheduled triggers the same way as `workflow_dispatch` in every configuration, so the cron effectively authorizes a
  recurring live-credential run without a human in the loop each time.
- Impact: standing, unattended use of real credentials; a compromised runner image or action silently harvests them
  on the next tick. CWE-285.
- Mitigation (SHOULD): keep the cadence weekly (not more frequent); scope the Environment reviewers/branch policy so
  the workflow runs only on the default branch's committed definition; document that the Atlas test user is
  least-privilege (test DB only, not Project-Owner) so a disclosed URI cannot touch production data or the Atlas
  Admin API. Confirm in the Environment settings that scheduled runs are still bound to the protected branch.

### [MEDIUM] CI-8 Secret smeared into the environment of unrelated steps
- Vector / evidence: Boundary B. Declaring the secrets at job (or workflow) level via `env:` exposes them to **every**
  step, including action steps whose code the maintainer does not control (setup-uv, checkout), widening CI-6's reach.
- Impact: unnecessary exposure surface for the three credentials. CWE-668.
- Mitigation (MUST): inject each secret with step-scoped `env:` **only** on the steps that need it (the cert-write
  step for `TEST_X509_CERT_PEM`; the pytest step for the two URIs). No job-level or workflow-level `env:` secret block.

### [LOW] CI-9 Concurrent runs racing the shared Atlas test identity / cert file
- Vector / evidence: overlapping `workflow_dispatch` + `schedule` runs write the same `X509-cert-ci.pem` and hit the
  same test cluster; a cleanup in one run could `shred` a cert an overlapping run still needs, or two runs mutate test
  data.
- Impact: flaky failures or a mid-run cert deletion misread as an auth failure (availability, not disclosure).
- Mitigation (SHOULD): a `concurrency: group: integration` with `cancel-in-progress: false` serializes runs; each run
  uses its own `$RUNNER_TEMP` HOME so file writes do not actually collide, but serialization avoids shared-cluster
  data races.

### [LOW] CI-10 Cert content validation / malformed-secret handling
- Vector / evidence: Boundary B. If `TEST_X509_CERT_PEM` is set but blank/truncated, the glob resolver still finds the
  file and the connection fails with a confusing error; a naive validation step that greps the PEM could echo part of
  it.
- Impact: misleading failures; a careless validation step becomes a CI-3 leak.
- Mitigation (SHOULD): the CI-4 presence guard already treats empty as missing (fail loudly, no print). If any shape
  check is added, test file **size/existence only**, never print contents; the `read_certificate_expiry` posture
  (parse only the CERTIFICATE block, never log the key) is the product-side precedent to mirror if a check is wanted.

## Mapping of findings to required workflow mitigations (subtask success criterion)

| Finding | Severity | Required workflow mitigation |
|---------|----------|------------------------------|
| CI-1 fork/event exfiltration | HIGH | `on:` = `workflow_dispatch` + `schedule` only; never `pull_request`/`pull_request_target`; `environment: atlas-integration` with required reviewers |
| CI-2 private key at rest/perms | HIGH | `HOME=$RUNNER_TEMP/home`; `umask 077` write of PEM to `$HOME/.bookscrapper/X509-cert-ci.pem` (`0600`), no `echo` of literal; `if: always()` `shred -u` + remove dir; ephemeral runner |
| CI-3 secret in public log | HIGH | no `echo`/`cat`/`set -x` of secrets or derived values; `::add-mask::` derived values; sentinel grep of log finds nothing |
| CI-4 all-skipped green | HIGH | pre-flight presence guard exits 1 with `::error::integration secrets not configured`; fail if zero non-skipped integration tests collected; `-rxXs` |
| CI-5 over-privileged token | MEDIUM | top-level `permissions: contents: read` |
| CI-6 unpinned actions | MEDIUM | full-SHA-pin every `uses:` (reuse vetted SHAs); `uv sync --frozen` |
| CI-7 unattended cron | MEDIUM | weekly cadence; least-privilege Atlas test user; Environment bound to protected branch |
| CI-8 env smear | MEDIUM | step-scoped `env:` only; no job/workflow-level secret `env:` |
| CI-9 concurrency race | LOW | `concurrency: group: integration`, `cancel-in-progress: false` |
| CI-10 malformed secret | LOW | presence guard treats empty as missing; size/existence checks only, never print contents |

## Verdict: PASS_WITH_FOLLOWUP

No CRITICAL finding and no code exists yet, so nothing is presently exploitable. Every HIGH (CI-1 triggers/environment,
CI-2 cert handling, CI-3 log hygiene, CI-4 fail-not-green) is a **merge-gate condition** for the workflow: the
delivered `.github/workflows/integration.yml` must implement each required mitigation in the table above, and the
success criterion (a sentinel grep of a completed job log finding no secret substring, plus a no-secrets run failing
with the explicit message rather than passing) must be demonstrated in the PR.

**This verdict flips to BLOCK if the delivered workflow:** is reachable by `pull_request`/`pull_request_target` or
carries secrets outside the required-reviewer Environment (CI-1); writes the PEM without `umask 077`/`0600` or omits
the `always()` shred (CI-2); `echo`s a secret/derived value or runs `set -x` on a secret step (CI-3); lets an
all-skipped or zero-collected run exit `0` (CI-4); grants more than `contents: read` (CI-5); or uses an unpinned
action tag on the credentialed job (CI-6).

Hand the workflow implementation to `testing-expert` (owner per the subtask role) and any product-side follow-up
(none required here - the integration tier already redacts and resolves correctly) to `python-expert`.
