# Staging File Format

The file a human reviews between `bookscraper scrape-urls` / `search` (step 1) and `bookscraper push` (step 2), and
the file `bookscraper pull` writes. One format serves all three - a `pull` export is valid `push` input and valid
`scrape-urls -f` input. Design rationale: [ADR 0003](/docs/adr/0003-staged-review-and-two-way-sync.md).

Implementation: `src/bookscraper/backends/local/staging.py` (`load_staging`, `write_staging`,
`validate_staging_documents`, `prepare_for_push`).

## File shape

- One top-level JSON **array** of JSON **objects** (one object per book), UTF-8 (a BOM is accepted on read),
  2-space indented - the same `book_utils.dumps_documents()` output as every other JSON file the tool writes.
- Default location: `staging/books.json` (the `staging/` directory is gitignored and created on first write).
  `pull` has no default; its documented example is `staging/books_mongo.json`.
- Every write is atomic (temp file + `os.replace`); a crash never leaves a half-written file.
- A corrupt file is **never** treated as empty: every command that reads it stops with an error naming the file
  and the line/column.

## Book fields

`push` validates every non-`rejected` document before connecting to anything:

| Field     | Rule                                                                                       |
|-----------|--------------------------------------------------------------------------------------------|
| `title`   | **Required**, non-empty string                                                             |
| `site`    | **Required**, non-empty string (`amazon`, `packtpub`, `oreilly`, `leanpub.com`, …)         |
| `authors` | **Required**, list of strings (may be empty when the site exposed none)                    |
| `url`     | Optional; if present, a non-empty string. All four sites now set it                        |
| `hash`    | Optional on input; **always recomputed by `push`** (see below)                             |
| others    | Free-form JSON (`isbn10`, `isbn13`, `publication_date`, `publication_year`, `description`, `about_the_book`, `categories`, `book_id`, `slug`, `asin`, `last_published_at`, …) |

**Forbidden key names** (any depth, including inside lists): a key starting with `$`, containing `.`, or containing
a NUL character. At the top level, no key other than the two reserved ones below may start with `_`. These rules
make MongoDB operator injection through a hand-edited file impossible.

**Size:** a document whose JSON encoding exceeds 16 MB (MongoDB's BSON limit) is rejected.

`push` reports **every** problem in the file in one pass (document index, field path, message) and writes nothing
until the file is clean.

### `hash`

`hash` is SHA-256 of normalized title + authors + publication year (`book_utils.hash_book`); the year comes from
`publication_year` when the document has that key, else from `last_published_at` (Leanpub). Because a reviewer's
title fix changes it, `push` recomputes it from the edited fields and reports `hash_changed` per document.
For a pulled document whose title, authors, and year fields were **not** edited, the stored hash is kept as-is,
so documents hashed under an older rule never show up as spurious updates.

## Reserved keys

### `_id` - identity of a document that already exists in MongoDB

- **Absent** on freshly scraped documents → `push` inserts them (or reports `duplicate` when the `hash` already
  exists).
- **Present** on pulled documents: MongoDB's `ObjectId` as a 24-character lowercase hex string, e.g.
  `"6744e7c2a1b2c3d4e5f60718"`. Never Extended JSON (`{"$oid": ...}`). `push` compares the document with the
  live one: identical → `unchanged`; different → `update` (requires `--allow-updates`).
- Do **not** hand-edit or invent an `_id`. An `_id` that does not exist in MongoDB is an error that stops the push.
- `push` never deletes: removing a pulled document from the file leaves it untouched in MongoDB.

### `_staging` - review metadata, never sent to MongoDB

Optional object; stripped before any write.

| Key          | Meaning                                                                                      |
|--------------|----------------------------------------------------------------------------------------------|
| `status`     | `"pending"` (default when absent), `"approved"`, or `"rejected"`                              |
| `scraped_at` | ISO-8601 UTC timestamp set by step 1                                                         |
| `source`     | `"scrape-urls"`, `"search"`, or `"pull"`                                                     |
| `note`       | Free text for the reviewer                                                                   |

Unknown `_staging` keys are allowed (reviewer scratch space) and stripped too.

- `push` sends every document whose status is not `rejected`; `push --approved-only` sends only `approved` ones.
- A `rejected` document is exempt from the book-field rules above (it is never sent), so marking a broken
  document `rejected` is a valid way to get a file past validation. Structural rules (must be an object,
  valid `_staging`, valid `_id`) still apply.
- Step 1 only ever **appends** to the file: re-scraping never changes or reorders documents a reviewer has
  already edited, approved, or rejected.

## Examples

A freshly scraped document (step 1 output):

```json
{
  "url": "https://www.amazon.com/dp/1098131029",
  "site": "amazon",
  "title": "Fluent Python",
  "authors": ["Luciano Ramalho"],
  "publication_date": "2022-04-26",
  "publication_year": 2022,
  "isbn13": "978-1492056355",
  "description": "Python's simplicity lets you become productive quickly...",
  "hash": "3f1c…",
  "_staging": {"status": "pending", "scraped_at": "2026-11-19T10:15:03Z", "source": "scrape-urls"}
}
```

A pulled document (`bookscraper pull` output), with a reviewer's note:

```json
{
  "_id": "6744e7c2a1b2c3d4e5f60718",
  "site": "leanpub.com",
  "url": "https://leanpub.com/some-book",
  "title": "Some Book",
  "book_id": "12345",
  "slug": "some-book",
  "authors": ["A. Author"],
  "about_the_book": "…",
  "categories": ["Python"],
  "last_published_at": "2026-03-02",
  "hash": "9ab0…",
  "_staging": {"status": "pending", "source": "pull", "note": "check the author spelling"}
}
```

## Field survey (2026-11-19)

Required-field coverage of each site's step 1 output (`scraping/scrape_details.py`):

| Site                     | `title` | `site` | `authors` | `url` | Year source          |
|--------------------------|---------|--------|-----------|-------|----------------------|
| Amazon / Packtpub / O'Reilly (Playwright) | yes, but `null` when the selector misses | yes | yes (list, may be empty) | yes | `publication_year` (absent if the site has no date selector) |
| Leanpub (JSON API)       | yes     | yes (`leanpub.com`) | yes (list) | yes - added in milestone 0002 (public `https://leanpub.com/<slug>`) | `last_published_at` |

A Playwright scrape whose title selector missed produces `"title": null`; `push` rejects it until the reviewer
fills in the title or marks the document `rejected`.
