# Canonical book record schema

`src/bookscraper/models.py` defines `BookRecord`, the single document shape every site writes and both
storage backends (`mongo`, `json`) store verbatim. `SCHEMA_VERSION = 2` stamps every new record; version 1 is
everything written before task
[03.1](/docs/roadmap/0001-generic-implementation/03.1-canonical-book-data-model/README.md), when each site wrote a
different shape (see the [v1 to v2 mapping](#v1-to-v2-mapping) below).

`BookRecord` is a frozen, slotted stdlib dataclass (no pydantic, no runtime dependency). It computes and validates
nothing: per-site normalizers (subtask 04) build a fully formed record from raw scrape output and save-boundary
validation (subtask 05) enforces the invariants in the table below. `to_document()` returns the exact
JSON-serializable dict the backends store; `from_document()` is its strict inverse (every schema-2 key must be
present).

## Field table

| Field                 | Type                                               | Required | Notes                                                                                   |
|-----------------------|----------------------------------------------------|----------|-----------------------------------------------------------------------------------------|
| `schema_version`      | `int`                                              | yes      | always `SCHEMA_VERSION` (`2`) for new records                                           |
| `site`                | `Literal["amazon","packtpub","leanpub","oreilly"]` | yes      | bare site key, never a hostname                                                         |
| `url`                 | `str`                                              | yes      | canonical human-facing detail URL (Leanpub: `https://leanpub.com/<slug>`)               |
| `source_id`           | `Optional[str]`                                    | no       | ASIN (Amazon), Leanpub `book_id`, else `None`                                           |
| `slug`                | `Optional[str]`                                    | no       | Leanpub slug; kept for pre-scrape dedup                                                  |
| `asin`                | `Optional[str]`                                    | no       | kept as its own field for `book_exists_by_asin`                                         |
| `title`               | `str`                                              | yes      | stripped, non-empty                                                                     |
| `authors`             | `list[str]`                                        | yes      | may be empty; stripped, de-duplicated, order preserved                                  |
| `isbn10`              | `Optional[str]`                                    | no       | 10 chars, digits + optional `X`, checksum-valid, else `None`                            |
| `isbn13`              | `Optional[str]`                                    | no       | 13 digits, checksum-valid, else `None`                                                   |
| `publication_date`    | `Optional[str]`                                    | no       | ISO `YYYY-MM-DD`, `YYYY-MM`, or `YYYY` (precision preserved)                             |
| `publication_year`    | `Optional[int]`                                    | no       | derived from `publication_date`                                                          |
| `description`         | `Optional[str]`                                    | no       | plain text (via `scraping/text.html_to_text` where HTML)                                |
| `tags`                | `list[str]`                                        | yes      | may be empty                                                                             |
| `ai_extracted_fields` | `list[str]`                                        | yes      | always `[]` until [04.1](/docs/roadmap/0001-generic-implementation/04.1-llm-extraction-fallback/README.md) lands; then the sorted names of LLM-filled fields |
| `scraped_at`          | `str`                                              | yes      | UTC ISO-8601 with `Z`                                                                    |
| `hash`                | `str`                                              | yes      | `hash_book(title, authors, publication_year)` - the dedup key in both backends          |

**No `"N/A"` sentinels anywhere in a schema-2 document.** Absent optional data is an explicit `null` key, so every
document has a uniform shape.

## Examples

### Amazon

```json
{
  "schema_version": 2,
  "site": "amazon",
  "url": "https://www.amazon.com/dp/0136816487",
  "source_id": "0136816487",
  "slug": null,
  "asin": "0136816487",
  "title": "A Tour of C++",
  "authors": ["Bjarne Stroustrup"],
  "isbn10": "0136816487",
  "isbn13": "9780136816485",
  "publication_date": "2022-09-24",
  "publication_year": 2022,
  "description": "A concise introduction to C++.",
  "tags": ["C++ Programming Language", "Object-Oriented Design"],
  "ai_extracted_fields": [],
  "scraped_at": "2026-10-05T12:00:00Z",
  "hash": "1b93bf19f83335db49b6af3fc2700565f80d42e1dabb44834b2c55a9ff4ad648"
}
```

### Packtpub

```json
{
  "schema_version": 2,
  "site": "packtpub",
  "url": "https://www.packtpub.com/product/c-windows-programming/9781786464224",
  "source_id": null,
  "slug": null,
  "asin": null,
  "title": "C++ Windows Programming",
  "authors": ["Stefan Bjornander"],
  "isbn10": null,
  "isbn13": "9781786464224",
  "publication_date": "2016-09",
  "publication_year": 2016,
  "description": "Hands-on Windows programming in modern C++.",
  "tags": ["C++", "Programming"],
  "ai_extracted_fields": [],
  "scraped_at": "2026-10-05T12:00:00Z",
  "hash": "bfc97faa0f5cdaee735dae7f1109efc10eea27df1f2a0587b12a0d84cd606743"
}
```

### Leanpub

```json
{
  "schema_version": 2,
  "site": "leanpub",
  "url": "https://leanpub.com/cpplambda",
  "source_id": "102",
  "slug": "cpplambda",
  "asin": null,
  "title": "C++ Lambda Story",
  "authors": ["Bartlomiej Filipek"],
  "isbn10": null,
  "isbn13": null,
  "publication_date": "2020-09-01",
  "publication_year": 2020,
  "description": "Everything you need to know about C++ lambda expressions.",
  "tags": ["Computer Science", "C and C++"],
  "ai_extracted_fields": [],
  "scraped_at": "2026-10-05T12:00:00Z",
  "hash": "3b1beb4b6d58060b587c648b566e9af155241f7c6405a9bfa8c3249f2cff26c5"
}
```

### O'Reilly

```json
{
  "schema_version": 2,
  "site": "oreilly",
  "url": "https://www.oreilly.com/library/view/c-in-a/0596002985/",
  "source_id": null,
  "slug": null,
  "asin": null,
  "title": "C++ In a Nutshell",
  "authors": ["Ray Lischner"],
  "isbn10": null,
  "isbn13": "9780596002985",
  "publication_date": "2003-05",
  "publication_year": 2003,
  "description": "A desktop quick reference for the C++ language and library.",
  "tags": ["C++", "Programming"],
  "ai_extracted_fields": [],
  "scraped_at": "2026-10-05T12:00:00Z",
  "hash": "f7d3d35ebe2d6d5d3056e6de643ae149352c976f7b6adfd8dad90a42711c8f23"
}
```

## v1 to v2 mapping

Version 1 documents are the per-site shapes written before this task - see the committed goldens under
`tests/fixtures/**/*.expected.json` for real v1 documents. The Playwright sites (Amazon, Packtpub, O'Reilly) shared
one v1 shape; Leanpub used raw API-derived keys. The migration (subtask
[06](/docs/roadmap/0001-generic-implementation/03.1-canonical-book-data-model/06-schema-version-and-migration.md))
rewrites v1 documents to v2 and must keep every `hash` stable (or record each changed hash in its report).

| v2 field              | Playwright v1 (Amazon / Packtpub / O'Reilly) | Leanpub v1                      | Migration note                                                              |
|-----------------------|----------------------------------------------|---------------------------------|-----------------------------------------------------------------------------|
| `schema_version`      | *(absent)*                                   | *(absent)*                      | new; set to `2`                                                             |
| `site`                | `site` (`"amazon"`)                          | `site` (`"leanpub.com"`)        | normalize the Leanpub hostname to the bare key `"leanpub"`                 |
| `url`                 | `url`                                         | *(absent)*                      | Leanpub: derive `https://leanpub.com/<slug>` from `slug`                   |
| `source_id`           | `asin` (Amazon) / *(absent)*                 | `book_id`                       | folds the site's own id into a shared field                                |
| `slug`                | *(absent)*                                   | `slug`                          | carried through                                                            |
| `asin`                | `asin` (Amazon) / *(absent)*                 | *(absent)*                      | carried through for `book_exists_by_asin`                                  |
| `title`               | `title`                                       | `title`                         | unchanged (hash input - must not drift)                                    |
| `authors`             | `authors`                                     | `authors`                       | unchanged (hash input - must not drift)                                    |
| `isbn10`              | `isbn10` (`"N/A"` when missing)              | *(absent)*                      | `"N/A"` to `null`; otherwise normalized/validated by subtask 02            |
| `isbn13`              | `isbn13` (`"N/A"` when missing)              | *(absent)*                      | `"N/A"` to `null`; otherwise normalized/validated by subtask 02            |
| `publication_date`    | `publication_date` (raw site text)           | `last_published_at` (ISO date)  | normalized to ISO precision by subtask 03                                  |
| `publication_year`    | `publication_year`                           | *(only inside the hash input)*  | Leanpub: derive from the date (hash input - must not drift)                |
| `description`         | `description`                                | `about_the_book`                | Leanpub key renamed; HTML converted via `scraping/text.html_to_text`       |
| `tags`                | *(never scraped in v1)*                       | `categories`                    | Playwright sites gained `tags` in task 03.0; Leanpub key renamed           |
| `ai_extracted_fields` | *(absent)*                                   | *(absent)*                      | new; always `[]`                                                           |
| `scraped_at`          | *(absent)*                                   | *(absent)*                      | new; UTC ISO-8601 with `Z`                                                 |
| `hash`                | `hash`                                        | `hash`                          | unchanged - the migration proves it does not change                        |
