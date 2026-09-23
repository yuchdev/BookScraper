# Architecture Decision Records

ADRs for Book Scrapper use the [MADR](https://adr.github.io/madr/) (Markdown Any Decision Records) template.
Each record lives in this directory as `000N-slug.md`. Mermaid diagrams referenced by ADRs
are in `assets/`.

## Inventory

| ADR                                              | Title                                  | Status   | Date       |
|--------------------------------------------------|----------------------------------------|----------|------------|
| [0002](0002-ai-assisted-schema-detection.md)     | AI-Assisted CSS-Selector Schema Detection | Accepted | 2026-09-23 |

The first real ADR landed as `0002`: `0001` was an illustrative example that has since
been deleted (see the Naming conventions note below - a gap in the sequence is expected
and fine).

## Template

Use `template.md` when creating a new ADR:

```bash
cp docs/adr/template.md docs/adr/0003-short-title.md
```

Replace the template placeholders with the record's number, title, date, and status.

## Naming conventions

- Filename: `000N-kebab-slug.md` - sequential, zero-padded to four digits.
- Status values: `Proposed` | `Accepted` | `Implemented` | `Superseded` | `Deprecated`.
- Superseded ADRs keep their file; add a `Superseded by: [000N](...)` line to their header.
- Gaps in the sequence are acceptable (e.g. a removed illustrative record); numbering does
  not need to stay contiguous.
