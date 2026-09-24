# 05 - Cross-site deduplication

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/03.3-multi-site-search-pipeline/README.md)
**Status:** ⬜ Not started
**Depends on:** [Task 03.1 / 02](/docs/roadmap/0001-generic-implementation/03.1-canonical-book-data-model/02-isbn-normalization.md)
**Role:** Python Expert

## Context

The same book often exists on several sites: an O'Reilly title is also on Amazon, and a Packt book is on both. The
`hash` (title + authors + year) catches exact matches, but site-specific title decorations ("…, 2nd Edition",
subtitles) and author spellings defeat it. The only cross-site identity that's reliable is the ISBN-13.

## Requirements

- **Within a run, before the detail scrape:** group candidates by
  `dedup_key = normalize_title(title) + "|" + normalize_author(first_author)` (lowercase, strip punctuation,
  edition markers, subtitles after `:`), and keep one candidate per key using the priority
  `DEDUP_SITE_PRIORITY = ["oreilly", "packtpub", "leanpub", "amazon"]` (publisher sites carry richer metadata
  than a reseller). Dropped candidates are logged at info level with the kept one's URL.
- **Against the store, after the detail scrape:** new contract method
  `StorageBackend.book_exists_by_isbn13(isbn13: str) -> bool` (both backends; sparse Mongo index
  `isbn13_lookup_index`; parity scenario). A detail record whose `isbn13` already exists → `DUPLICATE`, not saved.
- Pure helpers `normalize_title`, `normalize_author`, `dedup_key` in `scraping/dedup.py`, unit-tested with a
  table of real title pairs gathered from the 06.0 fixtures.
- No fuzzy/Levenshtein matching in this subtask (a false merge loses a book forever). Record it as a possible
  follow-up in the PR.

## Files

- Create `src/bookscraper/scraping/dedup.py`.
- Modify `src/bookscraper/commands/search.py`, `src/bookscraper/backends/storage.py`,
  `backends/mongo/database.py`, `backends/local/store.py`.
- Create `tests/unit/scraping/test_dedup.py`; modify `tests/mock/commands/test_search.py`,
  `tests/mock/backends/test_backend_parity.py`, `tests/mock/backends/mongo/test_database.py`.

## Tests

- `test_normalize_title_strips_edition_and_subtitle`
- `test_dedup_key_equal_for_cross_site_pair` (parametrized, ≥ 5 real pairs)
- `test_dedup_key_differs_for_different_editions_when_year_differs` (documented limitation if not)
- `test_candidate_priority_keeps_publisher_over_reseller`
- `test_isbn13_duplicate_marked_duplicate_not_saved`
- `test_book_exists_by_isbn13_parity`

## Success criteria

- [ ] A `search` run over Amazon + O'Reilly for the same query stores each ISBN-13 at most once.
