# Task 03.0 - Scraping Engine Hardening

**Parent milestone:** [plan.md](/docs/roadmap/0001-generic-implementation/plan.md)
**Status:** ⬜ Not started
**Category:** bugfix | **Priority:** P0
**Depends on:** [Task 06.0](/docs/roadmap/0001-generic-implementation/06.0-real-html-mock-test-corpus/README.md) (golden fixtures must characterize current behavior first)

## Scope

`scraping/scrape_details.py::scrape_book()` is the heart of `scrape-urls`, and a code review turned up several
defects that make it lose data or waste retries:

| # | Defect | Effect |
|---|--------|--------|
| a | The Leanpub branch opens a Playwright page, then `return`s the httpx result without closing it | One leaked page per Leanpub URL |
| b | On the final timeout attempt, `page.close()` is called twice | Spurious `Target closed` errors |
| c | `book_details["title"]` / `["publication_year"]` are read unconditionally when the hash is computed | A missing selector or date → `KeyError` → the whole URL is retried 3× and then marked `FAILED` |
| d | Leanpub returns `None` on failure, not `(None, "FAILED")` | `scrape_urls` files it as `UNKNOWN_ERROR` |
| e | `TAGS`, `DESCRIPTION_ALT`, `DETAILS_BUTTON` selectors exist in `parameters.py` but are never read | Data quietly missing; dead config |
| f | Bare `print()` calls throughout (including an httpx error that prints the response body) | Output bypasses the log file; a response body can leak into the console |
| g | Every exception is retried, including deterministic parsing bugs | 3× the latency for errors that can never succeed |

This task fixes them without changing the *shape* of the output document. That's 03.1's job, so golden-file diffs
stay attributable.

## Subtasks

| #  | Document                                                                   | Status         | Blocks     |
|----|----------------------------------------------------------------------------|----------------|------------|
| 01 | [Page lifecycle & leak fixes](/docs/roadmap/0001-generic-implementation/03.0-scraping-engine-hardening/01-page-lifecycle-and-leaks.md)          | ⬜ Not started | 02         |
| 02 | [Typed scrape result](/docs/roadmap/0001-generic-implementation/03.0-scraping-engine-hardening/02-typed-scrape-result.md)                      | ⬜ Not started | 03, 05     |
| 03 | [Missing-field resilience & retry policy](/docs/roadmap/0001-generic-implementation/03.0-scraping-engine-hardening/03-missing-field-resilience.md) | ⬜ Not started | 04     |
| 04 | [Wire unused selectors (tags, fallbacks, expanders)](/docs/roadmap/0001-generic-implementation/03.0-scraping-engine-hardening/04-wire-unused-selectors.md) | ⬜ Not started | - |
| 05 | [Leanpub API detail hardening](/docs/roadmap/0001-generic-implementation/03.0-scraping-engine-hardening/05-leanpub-api-detail-hardening.md)     | ⬜ Not started | -          |
| 06 | [Replace bare print() with print_log / logger](/docs/roadmap/0001-generic-implementation/03.0-scraping-engine-hardening/06-replace-bare-print.md) | ⬜ Not started | -       |

## Key constraints

- Characterize first: each subtask's PR includes the 06.0 golden-file diffs it causes, and each diff is
  explained in the PR description.
- `scrape_book()` stays the single Playwright entry point that `commands/scrape_urls.py` calls. Its signature may
  change only as subtask 02 specifies.
- Keep `route_handler` behavior unchanged. `schema_detection.py` reuses it.
- Delegate selector-level work to `scraping-expert`, and the rest to `python-expert`.
