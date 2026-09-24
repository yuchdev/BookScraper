# 05 - Scheduled audits: dependencies & selector drift

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/06.2-continuous-integration/README.md)
**Status:** ⬜ Not started
**Depends on:** [Task 04.0 / 08](/docs/roadmap/0001-generic-implementation/04.0-ai-assisted-schema-detection/08-json-report-and-drift-check.md)
**Role:** Python Expert

## Requirements

- `.github/workflows/audit.yml`, weekly `schedule` + `workflow_dispatch`:
  - **Dependencies:** `uv export --frozen --no-hashes > requirements-audit.txt`, then
    `uvx pip-audit -r requirements-audit.txt --strict`. The job fails on any known vulnerability. Mirrors the
    `/dep-audit` skill.
  - **Selector drift (offline):** `detect-schema --check-current-only --format json --html-file …` over the
    committed corpus, per site. That detects a change in *our* selectors against the stored snapshots (a
    regression guard), with no network or key needed.
  - **Selector drift (live, optional):** a separate job gated on `workflow_dispatch` input `live: true`. It runs
    `--check-current-only --url …` against one live sample URL per site, with Chromium installed, respecting the
    politeness layer. Exit `4` → it opens or updates a GitHub issue titled `Selector drift: <site>` (via `gh issue`,
    with `permissions: issues: write` scoped to that job).
- Both jobs upload their JSON reports as artifacts.

## Files

- Create `.github/workflows/audit.yml`.
- Modify `docs/scraping/schema-detection.md` (a CI section).

## Tests

- A `workflow_dispatch` run of each job (noted in the PR).

## Success criteria

- [ ] A newly published CVE in a locked dependency fails the weekly audit.
