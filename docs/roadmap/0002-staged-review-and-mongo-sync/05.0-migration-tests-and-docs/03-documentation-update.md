# 03 - CLAUDE.md, README & docs update

**Parent task:** [README.md](/docs/roadmap/0002-staged-review-and-mongo-sync/05.0-migration-tests-and-docs/README.md)
**Status:** ✅ Complete
**Depends on:** 02.0, 03.0, 04.0 complete
**Role:** Docs Updater

## Context

CLAUDE.md's *Storage backend* section, *Running* examples, *Testing* tiers, and *Architecture* list all describe
`--store-backend` and the interchangeable-backend design. Left as-is they would actively mislead future sessions.

## Requirements

- **CLAUDE.md:**
  - Intro: "two interchangeable storage backends" → the two-step staged pipeline plus `pull`; subcommand count
    four → six.
  - Replace *Storage backend* with a *Data flow* section: staging file format (link `docs/dev/staging-file-format.md`),
    reserved `_id` / `_staging`, the reversal of "`_id` never appears in `books.json`", `--check-mongo`,
    `push` phases and exit codes, `pull` no-clobber rule, "push never deletes".
  - *Running*: remove every `--store-backend`; add `push` (`--dry-run`, `--allow-updates`, `--approved-only`) and
    `pull -o` examples.
  - *Testing* / *Architecture*: new modules (`staging.py`, `push_plan.py`, `push_writer.py`, `export.py`,
    `commands/push.py`, `commands/pull.py`) and their test files; `isolated_staging_file`.
  - *Known inconsistencies*: add `push_report.json` and the pull example file to the gitignored-artifacts list.
- **README.md:** quick-start shows scrape → review → push.
- **`docs/`:** find stale references with `/doc-xref --store-backend` and `/doc-xref StorageBackend` and fix them;
  `docs/scraping/*` examples included.
- Mark ADR 0003 `Implemented`.

## Files

- Modify `CLAUDE.md`, `README.md`, `docs/adr/0003-staged-review-and-two-way-sync.md`, `docs/adr/README.md`, and
  whatever `/doc-xref` finds.

## Tests

`/link-check docs/`; `grep -rn "store-backend" CLAUDE.md README.md docs/ --exclude-dir=roadmap` is empty.

## Success criteria

- [x] A new session reading only CLAUDE.md could run the full two-step flow correctly.
