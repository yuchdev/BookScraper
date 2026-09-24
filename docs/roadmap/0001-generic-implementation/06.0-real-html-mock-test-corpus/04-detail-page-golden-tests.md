# 04 - Detail-page golden tests

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/06.0-real-html-mock-test-corpus/README.md)
**Status:** ⬜ Not started
**Depends on:** [03-fixture-serving-harness.md](/docs/roadmap/0001-generic-implementation/06.0-real-html-mock-test-corpus/03-fixture-serving-harness.md)
**Role:** Testing Expert

## Requirements

- `tests/mock/scraping/html/test_detail_goldens.py`: parametrized over every `html/<site>/<name>.html` that isn't an
  edge case (no leading `_`), plus every `json/leanpub/<name>.json`. For each fixture:
  1. Serve it through the harness at its `meta.url`.
  2. Call the **real** `scrape_book(meta.url, chromium_browser, site, storage_backend=None)` (or
     `get_leanpub_book_details` for JSON).
  3. Normalize volatile fields (`scraped_at` once 03.1 exists; nothing else should be volatile).
  4. Compare with `<name>.expected.json` using a field-by-field diff message (not a 200-line dict repr).
- `--update-goldens` pytest option (registered in `tests/conftest.py`) rewrites the `.expected.json` files instead of
  asserting. The run then **fails** with "goldens updated - review the diff", so an update is never silently
  green.
- Initial goldens are generated against the **current** code and committed as-is. Anything wrong in them (e.g.
  empty `tags`, raw date strings, `"N/A"`) gets a `"_known_issues"` list in the golden naming the task/subtask that
  will fix it. That key is ignored by the comparison.

## Files

- Create `tests/mock/scraping/html/test_detail_goldens.py`.
- Modify `tests/conftest.py` (option).
- Create `tests/fixtures/html/<site>/<name>.expected.json`, `tests/fixtures/json/leanpub/<name>.expected.json`.

## Tests

- `test_detail_golden[<site>/<name>]` (one per fixture)
- `test_update_goldens_option_rewrites_and_fails`

## Success criteria

- [ ] `scrape_details.py` line coverage ≥ 90% from this tier plus the existing mock tests (feeds task 06.1).
- [ ] Each `_known_issues` entry links to a real subtask.
