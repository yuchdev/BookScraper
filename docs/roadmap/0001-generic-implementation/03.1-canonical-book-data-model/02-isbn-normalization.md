# 02 - ISBN normalization & validation

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/03.1-canonical-book-data-model/README.md)
**Status:** ⬜ Not started
**Depends on:** [01-book-record-schema.md](/docs/roadmap/0001-generic-implementation/03.1-canonical-book-data-model/01-book-record-schema.md)
**Role:** Python Expert

## Context

Scraped ISBN text arrives as `"978-1-09-813102-4"`, `"1098131029"`, `"ISBN-13 : 978…"`, `"N/A"`, or garbage
from a drifted selector. Nothing validates checksums, so a mis-matched element (e.g. a page count) can be stored as
an ISBN. Cross-site dedup (task 03.3 subtask 05) needs a canonical ISBN-13.

## Requirements

New module `src/bookscraper/isbn.py` (pure, package root):

| Function                                          | Behavior                                                                                   |
|---------------------------------------------------|--------------------------------------------------------------------------------------------|
| `clean(raw: Optional[str]) -> str`                | strip every non-`[0-9Xx]` character; uppercase `x`                                          |
| `is_valid_isbn10(s: str) -> bool`                 | length 10, `X` only in last position, weighted mod-11 checksum                             |
| `is_valid_isbn13(s: str) -> bool`                 | length 13, all digits, prefix `978`/`979`, alternating 1/3 mod-10 checksum                  |
| `isbn10_to_isbn13(s: str) -> str`                 | `978` + first 9 digits + recomputed check digit; requires a valid ISBN-10                  |
| `normalize(raw10, raw13) -> tuple[Optional[str], Optional[str]]` | returns `(isbn10, isbn13)`: each valid-or-`None`; if only a valid ISBN-10 exists, derive ISBN-13 |
| `extract_first(text: str) -> Optional[str]`       | find the first 10- or 13-length valid ISBN in free text (for label-prefixed selectors)     |

- Invalid input is never an exception: it becomes `None`, with a debug log carrying the raw value truncated to 32
  chars.

## Files

- Create `src/bookscraper/isbn.py`.
- Create `tests/unit/test_isbn.py`.

## Tests

- `test_clean_strips_hyphens_spaces_and_labels`
- `test_valid_isbn10_with_x_check_digit` (`080442957X`)
- `test_invalid_isbn10_checksum_rejected`
- `test_valid_isbn13` (`9781098131029` or another verified value)
- `test_isbn13_with_bad_prefix_rejected`
- `test_isbn10_to_isbn13_conversion`
- `test_normalize_derives_13_from_valid_10`
- `test_normalize_na_sentinel_becomes_none`
- `test_extract_first_from_labelled_text`
- Property-style: `test_every_converted_isbn13_is_valid` over 200 generated valid ISBN-10s (stdlib `random`, fixed
  seed - no new dependency).

## Success criteria

- [ ] 100% line + branch coverage of `isbn.py`.
