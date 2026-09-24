# Task 05.0 - CLI Improvements

**Parent milestone:** [plan.md](/docs/roadmap/0001-generic-implementation/plan.md)
**Status:** ⬜ Not started
**Category:** feature | **Priority:** P1

## Scope

The `bookscraper` CLI (`cli.py` + `main.py` + `commands/*`) works, but it has gaps a user hits immediately:

- `--max-search-pages` is parsed and never read.
- Which sites and queries `search` covers can only be changed by editing `parameters.py`
  (`SITES_TO_SCRAPE`, `SEARCH_QUERIES`).
- Headed-vs-headless is a source constant (`HEADLESS_BROWSER = False`), so a server run needs a code edit.
- There is no dry run, no way to redirect output files, no `--version`, and no one-shot environment check.
- Exit codes are ad hoc: the `scrape-urls` "no valid URLs" path calls `sys.exit(0)` mid-function, partial failures
  exit 0, and Ctrl+C prints a traceback from `asyncio.run`.
- `--log-severity` is defined four times and `--store-backend` twice, copy-pasted.

## Subtasks

| #  | Document                                                             | Status         | Blocks |
|----|----------------------------------------------------------------------|----------------|--------|
| 01 | [Shared parent parsers](/docs/roadmap/0001-generic-implementation/05.0-cli-improvements/01-shared-parent-parsers.md)          | ⬜ Not started | 02-07  |
| 02 | [Search targeting flags](/docs/roadmap/0001-generic-implementation/05.0-cli-improvements/02-search-targeting-flags.md)        | ⬜ Not started | -      |
| 03 | [Browser & concurrency flags](/docs/roadmap/0001-generic-implementation/05.0-cli-improvements/03-browser-and-concurrency-flags.md) | ⬜ Not started | - |
| 04 | [Dry run & output locations](/docs/roadmap/0001-generic-implementation/05.0-cli-improvements/04-dry-run-and-output-locations.md) | ⬜ Not started | -   |
| 05 | [Exit-code contract & interrupt handling](/docs/roadmap/0001-generic-implementation/05.0-cli-improvements/05-exit-code-contract.md) | ⬜ Not started | - |
| 06 | [`--version` & help polish](/docs/roadmap/0001-generic-implementation/05.0-cli-improvements/06-version-and-help.md)           | ⬜ Not started | -      |
| 07 | [`doctor` subcommand](/docs/roadmap/0001-generic-implementation/05.0-cli-improvements/07-doctor-subcommand.md)                | ⬜ Not started | 08     |
| 08 | [CLI reference documentation](/docs/roadmap/0001-generic-implementation/05.0-cli-improvements/08-cli-reference-docs.md)       | ⬜ Not started | -      |

## Key constraints

- `cli.py` stays argparse-only (no new CLI framework dependency), and every flag keeps an explicit `dest=`.
- Flags beat env vars, and env vars beat `parameters.py` defaults, everywhere (same rule as `rotate-cert`).
- `--store-backend` remains required, with no implicit default.
- Every new flag gets a `tests/unit/test_cli.py` parse test and a mock-tier behavior test.
