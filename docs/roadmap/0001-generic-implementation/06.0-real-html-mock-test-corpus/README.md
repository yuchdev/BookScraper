# Task 06.0 - Real-HTML Mock Test Corpus

**Parent milestone:** [plan.md](/docs/roadmap/0001-generic-implementation/plan.md)
**Status:** ⬜ Not started
**Category:** test | **Priority:** P0

## Scope

The scraping tier is tested with hand-written doubles: `tests/mock/scraping/playwright_fakes.py`'s `FakeLocator`
returns whatever text a test asserts. Those tests prove control flow, but none of them would notice that
`#rpi-attribute-book_details-isbn13 .rpi-attribute-value span` no longer matches Amazon's markup, or that the
Packtpub `:has-text('ISBN-13 :')` selector never matched in the first place. That's also why `scrape_details.py`
sits at 80% coverage: the DOM-heavy branches can't be reached without inventing markup.

This task builds a versioned corpus of **real, captured, sanitized** pages (HTML for Playwright sites, JSON for the
Leanpub API). Unmodified production code runs against it in a real headless Chromium with the network fully
intercepted, and golden files record the expected records.

## Subtasks

| #  | Document                                                             | Status         | Blocks     |
|----|----------------------------------------------------------------------|----------------|------------|
| 01 | [Capture & sanitize script](/docs/roadmap/0001-generic-implementation/06.0-real-html-mock-test-corpus/01-capture-and-sanitize-script.md)  | ⬜ Not started | 02, 05, 06 |
| 02 | [Corpus layout & policy](/docs/roadmap/0001-generic-implementation/06.0-real-html-mock-test-corpus/02-corpus-layout-and-policy.md)        | ⬜ Not started | 03         |
| 03 | [Chromium fixture-serving harness](/docs/roadmap/0001-generic-implementation/06.0-real-html-mock-test-corpus/03-fixture-serving-harness.md) | ⬜ Not started | 04       |
| 04 | [Detail-page golden tests](/docs/roadmap/0001-generic-implementation/06.0-real-html-mock-test-corpus/04-detail-page-golden-tests.md)      | ⬜ Not started | -          |
| 05 | [Search-page & API JSON fixtures](/docs/roadmap/0001-generic-implementation/06.0-real-html-mock-test-corpus/05-search-and-api-fixtures.md) | ⬜ Not started | -         |
| 06 | [Edge-case fixtures](/docs/roadmap/0001-generic-implementation/06.0-real-html-mock-test-corpus/06-edge-case-fixtures.md)                  | ⬜ Not started | -          |
| 07 | [Fixture refresh & drift workflow](/docs/roadmap/0001-generic-implementation/06.0-real-html-mock-test-corpus/07-fixture-refresh-workflow.md) | ⬜ Not started | -       |

## Key constraints

- **Characterization first.** The goldens record what the code does *today*, including known-wrong output (e.g.
  missing `tags`). Tasks 03.0/03.1 then change goldens deliberately, with explained diffs.
- **No network in tests, ever.** The harness aborts any request not served from the corpus and fails the test that
  made it.
- **Sanitize before commit.** No cookies, session IDs, CSRF tokens, emails, or personal data. Capture runs logged
  out. Pages are trimmed to the minimum structure selectors need (the size budget is in subtask 02).
- Existing `FakeLocator` tests stay: they cover error branches (timeouts, exceptions) that a real page can't
  easily reproduce.
