# Task 06.2 - Continuous Integration

**Parent milestone:** [plan.md](/docs/roadmap/0001-generic-implementation/plan.md)
**Status:** ⬜ Not started
**Category:** infra | **Priority:** P1
**Depends on:** [Task 06.1](/docs/roadmap/0001-generic-implementation/06.1-coverage-gate/README.md), [Task 08.0 / 01](/docs/roadmap/0001-generic-implementation/08.0-repository-hygiene-and-docs/01-python-version-alignment.md)

## Scope

There is no CI: no `.github/workflows/`. Every quality gate in this milestone (ruff, the coverage gate, the
real-HTML tier, the integration tier) currently depends on someone remembering to run it. This task adds GitHub
Actions workflows that make those gates automatic.

## Subtasks

| #  | Document                                                        | Status         | Blocks |
|----|-----------------------------------------------------------------|----------------|--------|
| 01 | [Lint workflow](/docs/roadmap/0001-generic-implementation/06.2-continuous-integration/01-lint-workflow.md)                      | ⬜ Not started | 02     |
| 02 | [Test & coverage workflow](/docs/roadmap/0001-generic-implementation/06.2-continuous-integration/02-test-and-coverage-workflow.md) | ⬜ Not started | 03  |
| 03 | [Chromium tier in CI](/docs/roadmap/0001-generic-implementation/06.2-continuous-integration/03-chromium-tier.md)                | ⬜ Not started | -      |
| 04 | [Manual integration workflow](/docs/roadmap/0001-generic-implementation/06.2-continuous-integration/04-integration-workflow.md) | ⬜ Not started | -      |
| 05 | [Scheduled audits: dependencies & selector drift](/docs/roadmap/0001-generic-implementation/06.2-continuous-integration/05-scheduled-audits.md) | ⬜ Not started | - |
| 06 | [Branch protection & badges](/docs/roadmap/0001-generic-implementation/06.2-continuous-integration/06-branch-protection-and-badges.md) | ⬜ Not started | - |

## Key constraints

- `uv sync --frozen`: CI never resolves new versions, and `uv.lock` is the truth.
- Pin third-party actions to a full commit SHA with a version comment.
- **Secrets only in the integration and drift workflows**, only on `workflow_dispatch`/`schedule`, never on
  `pull_request` from forks. `security-auditor` reviews every workflow that touches a secret.
- `permissions: contents: read` at the top of every workflow, widened per job only where needed.
