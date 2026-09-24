# Task 03.1 - Canonical Book Data Model

**Parent milestone:** [plan.md](/docs/roadmap/0001-generic-implementation/plan.md)
**Status:** ⬜ Not started
**Category:** feature | **Priority:** P1
**Depends on:** [Task 03.0](/docs/roadmap/0001-generic-implementation/03.0-scraping-engine-hardening/README.md)

## Scope

Today each site writes a different document shape to the same collection:

| Concept          | Playwright sites (`scrape_book`)   | Leanpub (`get_leanpub_book_details`) |
|------------------|------------------------------------|--------------------------------------|
| Description      | `description`                      | `about_the_book`                     |
| Tags             | *(never scraped)*                  | `categories`                         |
| Date             | `publication_date` (raw site text) | `last_published_at` (ISO date)       |
| Year             | `publication_year`                 | *(only inside the hash input)*       |
| Missing ISBN     | `"N/A"`                            | key absent                           |
| Site value       | `"amazon"`                         | `"leanpub.com"`                      |
| Source identity  | `url`, `asin`                      | `book_id`, `slug`, no `url`          |

Any query or export across sites has to special-case every field. This task defines one canonical `BookRecord`,
normalizes ISBNs and dates, versions the schema, validates at the save boundary, and migrates existing data.

## Subtasks

| #  | Document                                                                  | Status         | Blocks     |
|----|---------------------------------------------------------------------------|----------------|------------|
| 01 | [BookRecord schema](/docs/roadmap/0001-generic-implementation/03.1-canonical-book-data-model/01-book-record-schema.md)                       | ⬜ Not started | 02, 03, 04 |
| 02 | [ISBN normalization & validation](/docs/roadmap/0001-generic-implementation/03.1-canonical-book-data-model/02-isbn-normalization.md)         | ⬜ Not started | 04         |
| 03 | [Date normalization](/docs/roadmap/0001-generic-implementation/03.1-canonical-book-data-model/03-date-normalization.md)                      | ⬜ Not started | 04         |
| 04 | [Per-site normalizers](/docs/roadmap/0001-generic-implementation/03.1-canonical-book-data-model/04-per-site-normalizers.md)                  | ⬜ Not started | 05, 06     |
| 05 | [Save-boundary validation](/docs/roadmap/0001-generic-implementation/03.1-canonical-book-data-model/05-save-boundary-validation.md)          | ⬜ Not started | -          |
| 06 | [Schema version & data migration](/docs/roadmap/0001-generic-implementation/03.1-canonical-book-data-model/06-schema-version-and-migration.md) | ⬜ Not started | -       |

## Key constraints

- **Hash stability is non-negotiable.** `hash_book(title, authors, year)` is the unique key in both backends. The
  canonical model must feed it *the same inputs as today* for Playwright sites. For Leanpub (which today hashes the
  year from `last_published_at`), the migration must prove that no existing hash changes, or else record every
  changed hash in the migration report.
- `models.py` sits at the package root next to `book_utils.py`, so `backends/` and `scraping/` can both import it
  without violating the import direction.
- No new runtime dependency (no pydantic). Use stdlib `dataclasses` + explicit validation.
