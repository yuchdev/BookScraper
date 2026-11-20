# 05 - Run summary & next-step hint

**Parent task:** [README.md](/docs/roadmap/0002-staged-review-and-mongo-sync/02.0-scrape-to-staging/README.md)
**Status:** ✅ Complete
**Depends on:** [01-cli-staging-file-flag.md](/docs/roadmap/0002-staged-review-and-mongo-sync/02.0-scrape-to-staging/01-cli-staging-file-flag.md), [04-merge-into-staging-file.md](/docs/roadmap/0002-staged-review-and-mongo-sync/02.0-scrape-to-staging/04-merge-into-staging-file.md)
**Role:** Python Expert

## Context

The pause between steps is only useful if the user knows a pause happened. Today the run ends with an insertion
summary that reads as if the data were already saved somewhere final.

## Requirements

- At run start, `print_log` the resolved staging file path and whether `--check-mongo` is active.
- At run end, print: appended / duplicate-in-file / duplicate-in-Mongo (if checked) / failed counts; the number of
  documents in the file still `pending`; and the next command, e.g.
  `Review staging/books.json, then run: bookscraper push --staging-file staging/books.json --dry-run`.
- Wording must not say "saved to database" anywhere in step 1.

## Files

- Modify `src/bookscraper/commands/scrape_urls.py`, `src/bookscraper/commands/search.py`.
- Extend `tests/mock/commands/test_scrape_urls.py`, `tests/mock/commands/test_search.py`.

## Tests

`test_summary_reports_counts_and_next_step` (capture `print_log`), `test_summary_uses_custom_staging_path`,
`test_no_database_wording_in_step_one_output`.

## Success criteria

- [x] Every successful scrape run ends with a copy-pasteable `push --dry-run` command.
