# 06 - Branch protection & badges

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/06.2-continuous-integration/README.md)
**Status:** ⬜ Not started
**Depends on:** [02-test-and-coverage-workflow.md](/docs/roadmap/0001-generic-implementation/06.2-continuous-integration/02-test-and-coverage-workflow.md)
**Role:** Docs Writer

## Requirements

- `docs/dev/ci.md`: what each workflow does, its triggers, which checks are required on `master`, how to re-run
  them, how to configure the `atlas-integration` environment, and the secret-handling rules.
- The recommended `master` protection (applied by the repo owner, not automated): `lint` and `tests` required,
  linear history, no force-push.
- README badges: lint, tests, and the Python versions derived from `requires-python`. Replace the static
  "Python 3.9+" badge, which is wrong (see task 08.0 subtask 01).

## Files

- Create `docs/dev/ci.md`.
- Modify `README.md`, `docs/README.md`.

## Tests

- `/link-check docs/dev/ci.md README.md`.

## Success criteria

- [ ] README badges reflect real workflow status.
