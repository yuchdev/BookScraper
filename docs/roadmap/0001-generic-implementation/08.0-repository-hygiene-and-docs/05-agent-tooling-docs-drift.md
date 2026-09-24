# 05 - Agent tooling docs drift

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/08.0-repository-hygiene-and-docs/README.md)
**Status:** ⬜ Not started
**Role:** Docs Writer

## Context

The fleet agents and loops that drive *this very milestone* are briefed with pre-restructure facts. The
implement-subtasks loop tells coders that "there is no real suite yet" and that `src/scrape_existing_books.py` runs
as a script. That misleads every iteration.

## Requirements

- `.claude/loops/implement-subtasks.md`: replace the stale *Known inconsistencies / Running / Testing* briefing
  with pointers to the current CLAUDE.md sections. Fix `src/book_scrapper/…` → `src/bookscraper/…` and
  `--cov=book_scrapper` → the canonical `uv run pytest --cov` (task 06.1 subtask 02). Update the example task
  names (`Hello World Endpoint`, `0001-working-implementation`) to this milestone.
- `.claude/agents/subtask-verifier.md`: update the example spec path to `0001-generic-implementation`.
- `.claude/agents/app-architect.md` and `.claude/agents/background-reviewer.md`: replace references to
  `scrape_existing_books.py`, `search_and_scrape.main`, and pre-restructure line numbers with the current module
  paths. Where a paragraph describes a defect this milestone fixes (page leak, per-document `insert_one`,
  unindexed `book_id`/`slug`), link the fixing subtask instead of restating the defect.
- Grep sweep: `grep -rn "book_scrapper\|scrape_existing_books\|search_and_scrape\|working-implementation" .claude/ docs/`
  must return nothing outside historical review reports.

## Files

- Modify `.claude/loops/implement-subtasks.md`, `.claude/loops/implement-milestone.md` (if affected),
  `.claude/agents/subtask-verifier.md`, `.claude/agents/app-architect.md`, `.claude/agents/background-reviewer.md`.

## Tests

- The grep sweep above returns nothing.
- `/link-check .claude/`.

## Success criteria

- [ ] An `implement-subtasks` run on any task in this milestone gets an accurate briefing.
