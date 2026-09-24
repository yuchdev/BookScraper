# 06 - CLAUDE.md consolidation

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/08.0-repository-hygiene-and-docs/README.md)
**Status:** ⬜ Not started
**Role:** Docs Writer

## Requirements

- Remove the duplicated test-credential paragraph in *Testing*: the "Test credentials (…)" and "Credential
  separation (test vs. production)" blocks state the same facts. Keep one.
- Re-verify every *Known inconsistencies to watch for* bullet against the code. Delete the ones fixed by this
  milestone (connection-success log → task 02.0 subtask 04; template scripts → subtask 02) and add any new
  intentional quirk.
- Add a short *Roadmap* section linking [docs/roadmap/README.md](/docs/roadmap/README.md) and this milestone's
  `status.md`, so agents discover the plan.
- Keep CLAUDE.md under ~400 lines. Move long procedural content (the rotation walkthrough, the fixture workflow)
  into the docs created by this milestone and link to it.

## Files

- Modify `CLAUDE.md`.

## Tests

- `/link-check CLAUDE.md`.

## Success criteria

- [ ] No fact is stated twice in CLAUDE.md.
