# 06 - Edge-case fixtures

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/06.0-real-html-mock-test-corpus/README.md)
**Status:** ⬜ Not started
**Depends on:** [01-capture-and-sanitize-script.md](/docs/roadmap/0001-generic-implementation/06.0-real-html-mock-test-corpus/01-capture-and-sanitize-script.md)
**Role:** Testing Expert → Scraping Expert

## Requirements

Capture (or, where capture is impossible, **minimally derive from a real capture** and set `"derived_from"` in the
meta) and test each of these:

| Fixture                       | Site(s)            | Expected behavior (today → after the fixing task)                           |
|-------------------------------|--------------------|------------------------------------------------------------------------------|
| `_404`                        | all                | `FAILED` → `NOT_FOUND` (03.0/02)                                             |
| `_bot_wall`                   | amazon             | `FAILED` after 3 retries → `BLOCKED`, no retry (03.2/04)                     |
| `_no_isbn`                    | amazon (Kindle-only) | isbn fields `"N/A"` → `None` (03.1)                                        |
| `_read_more_expander`         | oreilly            | full description after the expander click                                    |
| `_audiobook`                  | amazon             | release date via the audiobook selector branch of `PUBLICATION_DATE`         |
| `_many_authors`               | oreilly, packtpub  | all authors, order preserved, no duplicates                                  |
| `_non_ascii_title`            | any                | title and hash stable under Unicode (NFC)                                    |
| `_missing_title`              | derived            | `KeyError`→`FAILED` today → `PARSE_ERROR`, no retry (03.0/03)                |
| `_leanpub_null_about`         | leanpub JSON       | `TypeError`→`None` today → success with empty description (03.0/05)          |

- Each gets a golden (`.expected.json`) recording **today's** behavior, with `_known_issues` pointing at the fixing
  subtask. When that subtask lands, it updates the golden and removes the issue entry.

## Files

- Create the fixtures above plus their `.meta.json` / `.expected.json`.
- Extend `tests/mock/scraping/html/test_detail_goldens.py` (edge cases included in the parametrization).

## Tests

- `test_detail_golden[<site>/_<edge>]` (one per row)

## Success criteria

- [ ] Every row in the table has a committed fixture and golden.
