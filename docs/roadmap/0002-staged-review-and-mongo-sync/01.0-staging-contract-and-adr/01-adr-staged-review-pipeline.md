# 01 - ADR 0003: staged review & two-way sync

**Parent task:** [README.md](/docs/roadmap/0002-staged-review-and-mongo-sync/01.0-staging-contract-and-adr/README.md)
**Status:** ✅ Complete
**Depends on:** -
**Role:** App Architect

## Context

`--store-backend {mongo|json}` (0001 task 01.0) treats Mongo and `books.json` as interchangeable stores, one per run.
That gives no point at which a human can inspect scraped data before it lands in the system of record, and no way
to bring database content back out for bulk correction. The milestone [plan.md](/docs/roadmap/0002-staged-review-and-mongo-sync/plan.md)
lists the chosen design and four open questions (answered 2026-11-19); this subtask ratifies them before code changes the CLI.

## Requirements

- Create `docs/adr/0003-staged-review-and-two-way-sync.md` from `docs/adr/template.md` (MADR), status `Proposed`,
  moving to `Accepted` once the user signs off.
- Decision drivers: human review before write; Mongo as single system of record; bidirectional flow; explicit
  write targets; fail loudly on bad input; idempotent re-push.
- Considered options, at minimum:
  1. Keep `--store-backend`, add a third `staged` value.
  2. **Two-step: scrape → staging JSON → `push`; `pull` for the reverse** (proposed).
  3. Scrape straight to Mongo with a `reviewed: false` flag and review in Atlas UI / Compass.
- Record each plan decision with rationale: `_id`-as-identity vs `hash`; opt-in updates (`--allow-updates`);
  `_staging` metadata; whole-file validation; push never modifies the staging file; push never deletes; `pull`
  never clobbers; `--check-mongo` read-only dedup.
- Resolve (or explicitly defer) each of the plan's *Open questions*, and update the plan if an answer changes.
- Note the consequence that CLAUDE.md's "`_id` never appears in `books.json`" rule is reversed for pulled documents.
- Add the row to the inventory table in `docs/adr/README.md`.

## Files

- Create `docs/adr/0003-staged-review-and-two-way-sync.md`.
- Modify `docs/adr/README.md`; possibly `docs/roadmap/0002-staged-review-and-mongo-sync/plan.md`.

## Tests

None (documentation). Run `/link-check docs/`.

## Success criteria

- [x] ADR exists, follows MADR, and is linked from `docs/adr/README.md`.
- [x] Every *Open question* in the plan is answered or marked deferred with a reason.
- [x] Status is `Accepted` before 02.0 starts.
