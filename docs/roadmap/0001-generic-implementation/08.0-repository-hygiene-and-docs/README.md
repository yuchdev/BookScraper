# Task 08.0 - Repository Hygiene & Documentation

**Parent milestone:** [plan.md](/docs/roadmap/0001-generic-implementation/plan.md)
**Status:** ⬜ Not started
**Category:** chore | **Priority:** P2
**Depends on:** [Task 05.0](/docs/roadmap/0001-generic-implementation/05.0-cli-improvements/README.md) (for README / CLI doc alignment)

## Scope

Accumulated drift between what the repo *says* and what it *is*:

| Where                              | Says                                                         | Reality                                                   |
|------------------------------------|--------------------------------------------------------------|-----------------------------------------------------------|
| `pyproject.toml` `requires-python` | `>=3.9`                                                      | Code uses `datetime.UTC` (3.11+) and `zip(strict=True)` (3.10+); `ruff.toml` targets `py312` |
| README badge                       | Python 3.9+                                                  | as above                                                  |
| README quick start                 | `uv run bookscraper search --output-to-mongo`                | That flag doesn't exist; `--store-backend` is required     |
| README features                    | CSV output, CSV pre-flight                                   | CSV book output was replaced by the `json` backend        |
| `pyproject.toml` description       | "…with CSV/MongoDB output"                                   | JSON / MongoDB                                            |
| Repo root                          | `release_package.py`, `RELEASE_NOTES.json`                   | Cookiecutter leftovers that read a nonexistent `setup.cfg` |
| `.claude/loops/implement-subtasks.md` | `src/scrape_existing_books.py`, `book_scrapper` package, "no real suite yet", `--cov=book_scrapper` | `bookscraper` package, 344-test suite |
| `.claude/agents/app-architect.md`, `background-reviewer.md` | `scrape_existing_books.py`, `search_and_scrape.main`, pre-restructure line numbers | `commands/scrape_urls.py`, `commands/search.py` |
| CLAUDE.md *Testing*                | the test-credential paragraph appears twice                  | should appear once                                        |

## Subtasks

| #  | Document                                                             | Status         | Blocks |
|----|----------------------------------------------------------------------|----------------|--------|
| 01 | [Python version alignment](/docs/roadmap/0001-generic-implementation/08.0-repository-hygiene-and-docs/01-python-version-alignment.md)     | ⬜ Not started | -      |
| 02 | [Remove template leftovers](/docs/roadmap/0001-generic-implementation/08.0-repository-hygiene-and-docs/02-remove-template-leftovers.md)   | ⬜ Not started | -      |
| 03 | [README refresh](/docs/roadmap/0001-generic-implementation/08.0-repository-hygiene-and-docs/03-readme-refresh.md)                         | ⬜ Not started | -      |
| 04 | [Packaging metadata & build smoke test](/docs/roadmap/0001-generic-implementation/08.0-repository-hygiene-and-docs/04-packaging-metadata.md) | ⬜ Not started | -   |
| 05 | [Agent tooling docs drift](/docs/roadmap/0001-generic-implementation/08.0-repository-hygiene-and-docs/05-agent-tooling-docs-drift.md)     | ⬜ Not started | -      |
| 06 | [CLAUDE.md consolidation](/docs/roadmap/0001-generic-implementation/08.0-repository-hygiene-and-docs/06-claude-md-consolidation.md)       | ⬜ Not started | -      |

## Key constraints

- Verify every documentation claim against the code, never against another doc.
- Run `/doc-xref` before renaming or removing any doc path, and `/link-check docs/` after.
