# 06 - Replace bare print() with print_log / logger

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/03.0-scraping-engine-hardening/README.md)
**Status:** ⬜ Not started
**Role:** Python Expert

## Context

`scrape_details.py` and `search_utils.py` use bare `print()` (`"Trying attempt 1..."`, `"Leanpub book"`, the
scraped title, error messages). Those lines bypass the per-run log file entirely, ignore `--log-severity`, and
interleave unpredictably across ten concurrent tasks.

## Requirements

- Replace every `print(` in `src/bookscraper/` (except `commands/detect_schema.py`'s intentional report to stdout)
  with either:
  - `module_logger.debug(...)` for progress chatter (attempt numbers, the "Leanpub book" marker, echoed titles), or
  - `print_log(..., status)` **and** `module_logger.<level>` for user-relevant events, following the existing
    pairing pattern.
- Each concurrent scrape's log lines carry the URL (or a short `[site:slug]` tag) so interleaved output stays
  readable.
- Add a guard test that AST-scans `src/bookscraper/` for `print(` calls outside an explicit allow-list
  (`commands/detect_schema.py`, `book_utils.print_log` itself).

## Files

- Modify `src/bookscraper/scraping/scrape_details.py`, `src/bookscraper/scraping/search_utils.py`, and any other
  module the scan finds.
- Create `tests/unit/test_no_bare_print.py`.

## Tests

- `test_no_bare_print_outside_allow_list`

## Success criteria

- [ ] `test_no_bare_print_outside_allow_list` passes.
- [ ] Running `scrape-urls --log-severity debug` shows attempt numbers in the log file, not on the console.
