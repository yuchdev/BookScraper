# 01 - BookRecord schema

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/03.1-canonical-book-data-model/README.md)
**Status:** ⬜ Not started
**Role:** Architect → Python Expert

## Requirements

- New module `src/bookscraper/models.py` with `SCHEMA_VERSION = 2` (version 1 = everything written before this
  task) and:

  | Field              | Type                  | Required | Notes                                                          |
  |--------------------|-----------------------|----------|----------------------------------------------------------------|
  | `schema_version`   | `int`                 | yes      | always `SCHEMA_VERSION` for new records                        |
  | `site`             | `Literal["amazon","packtpub","leanpub","oreilly"]` | yes | bare site key, never a hostname      |
  | `url`              | `str`                 | yes      | canonical human-facing detail URL (Leanpub: `https://leanpub.com/<slug>`) |
  | `source_id`        | `Optional[str]`       | no       | ASIN (Amazon), Leanpub `book_id`, else `None`                  |
  | `slug`             | `Optional[str]`       | no       | Leanpub slug; kept for pre-scrape dedup                        |
  | `asin`             | `Optional[str]`       | no       | kept as its own field for `book_exists_by_asin`                |
  | `title`            | `str`                 | yes      | stripped, non-empty                                            |
  | `authors`          | `list[str]`           | yes      | may be empty; stripped, de-duplicated, order preserved         |
  | `isbn10`           | `Optional[str]`       | no       | 10 chars, digits + optional `X`, checksum-valid, else `None`   |
  | `isbn13`           | `Optional[str]`       | no       | 13 digits, checksum-valid, else `None`                         |
  | `publication_date` | `Optional[str]`       | no       | ISO `YYYY-MM-DD`, `YYYY-MM`, or `YYYY` (precision preserved)   |
  | `publication_year` | `Optional[int]`       | no       | derived from `publication_date`                                |
  | `description`      | `Optional[str]`       | no       | plain text (via `scraping/text.html_to_text` where HTML)       |
  | `tags`             | `list[str]`           | yes      | may be empty                                                   |
  | `scraped_at`       | `str`                 | yes      | UTC ISO-8601 with `Z`                                          |
  | `hash`             | `str`                 | yes      | `hash_book(title, authors, publication_year)`                  |

- `@dataclass(frozen=True, slots=True) class BookRecord` with the fields above, plus:
  - `to_document() -> dict` - the exact dict both backends store (JSON-serializable, no `None`-valued
    `Optional` keys dropped: absent data is an explicit `null`, so documents are uniform).
  - `@classmethod from_document(cls, doc: dict) -> "BookRecord"` - strict inverse, used by tests and the migration.
  - `__post_init__` computes nothing. Normalizers (subtask 04) build a fully formed record, and validation lives
    in subtask 05.
- **No `"N/A"` sentinels anywhere** in schema-2 documents.

## Files

- Create `src/bookscraper/models.py`.
- Create `tests/unit/test_models.py`.
- Create `docs/scraping/book-record-schema.md` - field table, examples per site, v1→v2 mapping.

## Tests

- `test_to_document_round_trips_through_from_document`
- `test_to_document_is_json_serializable`
- `test_optional_fields_serialize_as_explicit_null`
- `test_site_literal_rejects_hostname_values` (a type-check-level test; also covered by subtask 05)

## Success criteria

- [ ] `docs/scraping/book-record-schema.md` exists and CLAUDE.md *Storage backend* links it.
