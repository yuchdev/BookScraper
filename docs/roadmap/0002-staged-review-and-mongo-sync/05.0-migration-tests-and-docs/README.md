# Task 05.0 - Migration, Tests & Documentation

**Parent milestone:** [plan.md](/docs/roadmap/0002-staged-review-and-mongo-sync/plan.md)
**Status:** ✅ Complete
**Category:** chore | **Priority:** P0

## Scope

Close out the milestone: remove every trace of the `--store-backend` era from the test suite, add one end-to-end
mock test of the full **scrape → edit → push** loop, rewrite the documentation that describes storage (CLAUDE.md
above all), and reconcile milestone 0001's roadmap so it no longer promises work this milestone superseded.

## Subtasks

| #  | Document                                                                                                                                     | Status         | Blocks |
|----|----------------------------------------------------------------------------------------------------------------------------------------------|----------------|--------|
| 01 | [Retire `--store-backend` tests & fixtures](/docs/roadmap/0002-staged-review-and-mongo-sync/05.0-migration-tests-and-docs/01-retire-store-backend-tests.md) | ✅ Complete    | 02     |
| 02 | [End-to-end two-step mock test](/docs/roadmap/0002-staged-review-and-mongo-sync/05.0-migration-tests-and-docs/02-end-to-end-two-step-test.md)               | ✅ Complete    | -      |
| 03 | [CLAUDE.md, README & docs update](/docs/roadmap/0002-staged-review-and-mongo-sync/05.0-migration-tests-and-docs/03-documentation-update.md)                  | ✅ Complete    | 04     |
| 04 | [Reconcile milestone 0001](/docs/roadmap/0002-staged-review-and-mongo-sync/05.0-migration-tests-and-docs/04-reconcile-milestone-0001.md)                    | ✅ Complete    | -      |

## Key constraints

- Coverage stays ≥90% overall after old tests are deleted - removed tests are replaced, not just dropped.
- Docs describe only shipped behavior; anything deferred by ADR 0003 is labeled as such.
- `/link-check docs/` passes after every doc change.
