# 02 - Corpus layout & policy

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/06.0-real-html-mock-test-corpus/README.md)
**Status:** ⬜ Not started
**Depends on:** [01-capture-and-sanitize-script.md](/docs/roadmap/0001-generic-implementation/06.0-real-html-mock-test-corpus/01-capture-and-sanitize-script.md)
**Role:** Docs Writer → Testing Expert

## Requirements

- Layout:
  ```text
  tests/fixtures/
    README.md                       ← policy (below)
    html/<site>/<name>.html         ← sanitized DOM snapshots
    html/<site>/<name>.meta.json
    html/<site>/<name>.expected.json← golden record (subtask 04)
    json/leanpub/<name>.json        ← API responses (+ .meta.json, .expected.json)
    robots/<site>.txt
    schema_detection/…              ← task 04.0 subtask 07
  ```
- Naming: `snake_case`. Detail pages are named after the book; search pages are `search_<query>_p<N>`; edge cases
  get a leading underscore (`_404`, `_bot_wall`).
- `tests/fixtures/README.md` policy:
  - Purpose: interoperability testing of the scraper against minimized page structure. Not redistribution.
  - Keep each fixture to the minimum needed. **Per-file budget 400 KB, corpus budget 15 MB** (enforced by a test).
  - Every fixture has a `.meta.json`, and orphans fail a test.
  - How to capture, sanitize, and refresh (links to subtasks 01 and 07).
- `.gitattributes`: `tests/fixtures/** linguist-generated=true -diff` for `*.html`, so PR diffs stay readable;
  goldens (`*.expected.json`) **stay diffable**.
- Minimum initial corpus: amazon ×3, packtpub ×3, oreilly ×3 detail pages; leanpub ×3 book JSON; plus subtasks
  05-06.

## Files

- Create `tests/fixtures/README.md`, `.gitattributes` (or amend it).
- Create `tests/unit/test_fixture_corpus.py`.

## Tests

- `test_every_fixture_has_meta_json`
- `test_no_orphan_meta_or_expected_files`
- `test_fixture_file_size_budget`
- `test_corpus_total_size_budget`
- `test_meta_sha256_matches_file` (detects hand edits after capture)

## Success criteria

- [ ] The minimum initial corpus is committed and all corpus tests pass.
