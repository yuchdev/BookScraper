# 02 - Search targeting flags

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/05.0-cli-improvements/README.md)
**Status:** ⬜ Not started
**Depends on:** [01-shared-parent-parsers.md](/docs/roadmap/0001-generic-implementation/05.0-cli-improvements/01-shared-parent-parsers.md)
**Role:** Python Expert

## Requirements

| Flag                   | `dest`             | Type / action                       | Default                          |
|------------------------|--------------------|-------------------------------------|----------------------------------|
| `--site`               | `sites`            | `append`, choices = site keys       | `None` → `SITES_TO_SCRAPE`       |
| `--query` / `-q`       | `queries`          | `append`                            | `None` → `SEARCH_QUERIES`        |
| `--queries-file`       | `queries_file`     | path; one query per line, `#` comments, blanks skipped | `None`    |
| `--max-search-pages`   | `max_search_pages` | `int`, validated `>= 1`             | `3` (existing)                   |
| `--no-filters`         | `no_filters`       | `store_true`                        | `False` (consumed by task 03.3 subtask 04) |

- `--query` and `--queries-file` combine (union, order preserved, de-duplicated).
- An empty resolved query list or site list → `parser.error(...)` (exit 2).
- `commands/search.run()` reads `args.sites` / `args.queries` / `args.max_search_pages` through one helper,
  `resolve_search_targets(args) -> tuple[list[str], list[str], int]`, so defaults are applied in exactly one place.
- Until task 03.3 lands, selecting a site that has no provider yet → a clear error naming the supported sites (not
  a silent skip).

## Files

- Modify `src/bookscraper/cli.py`, `src/bookscraper/commands/search.py`.
- Tests: `tests/unit/test_cli.py`, `tests/mock/commands/test_search.py`.

## Tests

- `test_site_flag_repeatable_and_validated`
- `test_query_and_queries_file_are_merged_and_deduplicated`
- `test_queries_file_skips_comments_and_blanks`
- `test_max_search_pages_rejects_zero`
- `test_defaults_fall_back_to_parameters_constants`
- `test_max_search_pages_reaches_search_provider`
- `test_unsupported_site_errors_clearly`

## Success criteria

- [ ] `bookscraper search --store-backend json --query "Rust" --max-search-pages 1` searches exactly one query and
      one page per site (mock-verified).
