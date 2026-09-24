# 03 - Browser & concurrency flags

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/05.0-cli-improvements/README.md)
**Status:** ⬜ Not started
**Depends on:** [01-shared-parent-parsers.md](/docs/roadmap/0001-generic-implementation/05.0-cli-improvements/01-shared-parent-parsers.md)
**Role:** Python Expert

## Requirements

On `scrape-urls`, `search`, and `detect-schema` (via `_scrape_runtime_parent`, where applicable):

| Flag                               | `dest`            | Type                           | Default / precedence                                   |
|------------------------------------|-------------------|--------------------------------|--------------------------------------------------------|
| `--headless` / `--no-headless`     | `headless`        | `argparse.BooleanOptionalAction` | `None` → env `BOOKSCRAPER_HEADLESS` (`1/0/true/false`) → `HEADLESS_BROWSER` |
| `--concurrency`                    | `concurrency`     | `int` 1-32                     | `4` (consumed by task 03.2 subtask 06)                 |
| `--nav-timeout`                    | `nav_timeout_s`   | `float` seconds > 0            | `60` (→ `NAVIGATION_TIMEOUT_MS`, task 03.0 subtask 03) |
| `--ignore-robots`                  | `ignore_robots`   | `store_true`                   | `False` (consumed by task 03.2 subtask 02)             |

- One helper `runtime_options(args) -> RuntimeOptions` (frozen dataclass) resolves the precedence. Commands pass it
  down instead of importing `HEADLESS_BROWSER` directly (today `scrape_urls.py` and `schema_detection.py` import
  it).
- Flip the `HEADLESS_BROWSER` default in `parameters.py` to `True`. Headed mode is a debugging choice, and a
  scheduled or CI run must never try to open a window. Record this in the PR and in CLAUDE.md's *Architecture*
  description of `parameters.py`.

## Files

- Modify `src/bookscraper/cli.py`, `src/bookscraper/scraping/parameters.py`,
  `src/bookscraper/commands/scrape_urls.py`, `src/bookscraper/commands/search.py`,
  `src/bookscraper/commands/detect_schema.py`, `src/bookscraper/scraping/schema_detection.py`.
- Create `src/bookscraper/runtime.py` (`RuntimeOptions`, `runtime_options`).
- Tests: `tests/unit/test_cli.py`, `tests/unit/test_runtime.py`, command mock tests.

## Tests

- `test_headless_flag_beats_env_beats_parameter`
- `test_env_headless_parsing_accepts_common_spellings`
- `test_concurrency_bounds_enforced`
- `test_nav_timeout_converted_to_ms`
- `test_commands_do_not_import_headless_browser_constant` (grep guard)

## Success criteria

- [ ] `grep -rn "HEADLESS_BROWSER" src/bookscraper/commands src/bookscraper/scraping/schema_detection.py` returns
      nothing.
