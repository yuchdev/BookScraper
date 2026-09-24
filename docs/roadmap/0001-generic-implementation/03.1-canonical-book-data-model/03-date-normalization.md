# 03 - Date normalization

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/03.1-canonical-book-data-model/README.md)
**Status:** ⬜ Not started
**Depends on:** [01-book-record-schema.md](/docs/roadmap/0001-generic-implementation/03.1-canonical-book-data-model/01-book-record-schema.md)
**Role:** Python Expert

## Context

`book_utils.extract_year_from_date()` understands `YYYY-MM-DD` (including embedded in text), `%B %d, %Y`, `%Y`,
`%b %d, %Y`, and `%B %Y`, but returns only the year. The stored `publication_date` is whatever raw text the site
showed (`"December 17, 2019"`, `"Last updated on 2016-11-29"`), so nothing downstream can sort or filter by date.

## Requirements

- New `normalize_date(raw: Optional[str]) -> tuple[Optional[str], Optional[int]]` in `book_utils.py`, returning
  `(iso_date_with_preserved_precision, year)`:

  | Input example                  | Output                  |
  |--------------------------------|-------------------------|
  | `"2023-12-19"`                 | `("2023-12-19", 2023)`  |
  | `"2023-12-19T08:00:00Z"`       | `("2023-12-19", 2023)`  |
  | `"Last updated on 2016-11-29"` | `("2016-11-29", 2016)`  |
  | `"December 17, 2019"`          | `("2019-12-17", 2019)`  |
  | `"Apr 23, 2021"`               | `("2021-04-23", 2021)`  |
  | `"September 2016"`             | `("2016-09", 2016)`     |
  | `"1995"`                       | `("1995", 1995)`        |
  | `"17 Dec. 2019"` / `"17 December 2019"` | `("2019-12-17", 2019)` (new: Packt/O'Reilly style) |
  | `"N/A"`, `""`, `None`, garbage | `(None, None)`          |

- Parsing is locale-independent: use explicit English month tables, not `strptime`'s `%B`/`%b`, which depend on
  the process locale.
- Re-implement `extract_year_from_date(s)` as `normalize_date(s)[1]` and keep it for backward compatibility. All
  existing `tests/unit/test_book_utils.py` cases must still pass.

## Files

- Modify `src/bookscraper/book_utils.py`.
- Modify `tests/unit/test_book_utils.py`.

## Tests

- `test_normalize_date_table` - parametrized over every row above.
- `test_normalize_date_is_locale_independent` (monkeypatch `locale.setlocale` to `de_DE` if available, else skip).
- `test_extract_year_from_date_backward_compatible` (the existing cases).

## Success criteria

- [ ] Every row of the table is a passing parametrized case.
