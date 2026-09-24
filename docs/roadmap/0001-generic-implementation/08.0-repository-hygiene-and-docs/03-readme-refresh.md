# 03 - README refresh

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/08.0-repository-hygiene-and-docs/README.md)
**Status:** ⬜ Not started
**Depends on:** [Task 05.0 / 08](/docs/roadmap/0001-generic-implementation/05.0-cli-improvements/08-cli-reference-docs.md)
**Role:** Docs Writer

## Requirements

- Rewrite `README.md` sections against the code as it is:
  - **Quick start:** `uv sync` → `uv run playwright install chromium` → `uv run bookscraper doctor` →
    `uv run bookscraper search --store-backend json --query Python --dry-run`.
  - **Features:** the `json` / `mongo` backends (no CSV book output), X.509 rotation, `detect-schema`, politeness
    (rate limit, robots.txt), exit codes. Remove "CSV pre-flight".
  - **Configuration:** a short summary with a link to CLAUDE.md *Authentication configuration* and the runbook
    (task 02.1 subtask 10). No duplicated JSON shapes.
  - **Development:** tests, coverage, fixture corpus, CI, each linking its doc.
- Every command in the README must run verbatim. Add a unit guard that extracts `bookscraper …` lines from fenced
  blocks and checks that each parses with `build_parser()` (subcommand + flags only, no execution).

## Files

- Modify `README.md`.
- Create `tests/unit/test_readme_commands.py`.

## Tests

- `test_every_readme_bookscraper_command_parses`
- `/link-check README.md`

## Success criteria

- [ ] `grep -n "output-to-mongo\|CSV Files" README.md` returns nothing.
