# 04 - Reconcile milestone 0001

**Parent task:** [README.md](/docs/roadmap/0002-staged-review-and-mongo-sync/05.0-migration-tests-and-docs/README.md)
**Status:** ✅ Complete
**Depends on:** [03-documentation-update.md](/docs/roadmap/0002-staged-review-and-mongo-sync/05.0-migration-tests-and-docs/03-documentation-update.md)
**Role:** Docs Updater

## Context

Milestone 0001's task 01.0 and several other subtasks (found by grep: 02.0/04, 02.0/05, 02.1/06, 03.1/06, 03.3/01,
03.3/03, 03.3/05, 05.0/01, 05.0/05, 07.0/03) reference `--store-backend` or `StorageBackend`. Leaving them would
make 0001 specs contradict the shipped code.

## Requirements

- 0001 01.0 README: add a "**Superseded in part by** [milestone 0002](/docs/roadmap/0002-staged-review-and-mongo-sync/plan.md)"
  banner. Per subtask, as listed in 0002's plan *Relationship to milestone 0001*:
  - 04 - keep ✅ but note it was reverted by 0002 02.0/01;
  - 05, 06 - re-point to 0002 03.0/02 and 02.0/03 respectively, or mark ✅ if absorbed there;
  - 07 - mark "Superseded by 0002 01.0/03";
  - 08 - mark "Superseded by 0002 04.0/03".
- Every other 0001 subtask that mentions `--store-backend` / `StorageBackend`: update the wording to the new
  commands (`--staging-file`, `DedupSource`, `push`) or the example command lines; do not change their scope.
- Update 0001 `status.md` counts and the *Definition of done* example command (it uses `--store-backend json`).
- `docs/roadmap/README.md` milestones table and `docs/roadmap/roadmap.md` overview already list 0002 (added with this
  milestone); update their status column.

## Files

- Modify files under `docs/roadmap/0001-generic-implementation/` found by
  `grep -rln "store.backend\|StorageBackend" docs/roadmap/0001-generic-implementation`.
- Modify `docs/roadmap/README.md`, `docs/roadmap/roadmap.md`.

## Tests

`/link-check docs/`.

## Success criteria

- [x] That grep returns only the intentional "superseded" notes.
- [x] 0001's status totals still add up.
