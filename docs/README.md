# Documentation Registry

## CLI Reference

- [`cli/cli.md`](cli/cli.md) — Complete flag reference for all six subcommands (`scrape-urls`, `search`,
  `rotate-cert`, `detect-schema`, `migrate`, `doctor`): synopsis, flag tables with `dest`/default/env
  override, examples, and exit codes.

## Architecture & Design

- [ADR index](adr/) — architectural decision records
- [Agent runbooks](agent/) — agent-specific operation guides
- [Security](security/) — threat models and security reviews

## Configuration

- [Configuration examples](examples/) — annotated `settings.json` and other config templates
  - [`settings.example.json`](examples/settings.example.json) — MongoDB auth settings.json reference

## Development

- [Developer guides](dev/) — setup, conventions, and contribution guides
- [`dev/ci.md`](dev/ci.md) — CI workflows, required checks, the `atlas-integration` Environment, secret handling
- [`dev/logging.md`](dev/logging.md) — log file location, logger hierarchy, redacting handler, scrape context,
  level configuration, and run-report format
- [Test guides](test/) — testing strategy and tier documentation
  - [`test/code_test_coverage.md`](test/code_test_coverage.md) — coverage threshold, enforcement, tier model, and per-module floor
  - [`test/html-fixtures.md`](test/html-fixtures.md) — real-HTML fixture corpus refresh & drift workflow, and test harness reference

## Runbooks

- [`runbooks/x509-certificate-rotation.md`](runbooks/x509-certificate-rotation.md) —
  Issuing, verifying, and scheduling Atlas-managed X.509 certificate rotation; expiry inspection;
  recovery procedures; troubleshooting matrix; behavior reference
- [`runbooks/migrate-v1-to-v2.md`](runbooks/migrate-v1-to-v2.md) — Migrating a store from the
  per-site v1 document shapes to canonical schema-2 `BookRecord`

## Scraping

- [Schema detection](scraping/schema-detection.md) — AI-assisted CSS selector detection guide
- [ADR 0002 — AI-assisted schema detection](adr/0002-ai-assisted-schema-detection.md)
- [`scraping/llm-extraction-fallback.md`](scraping/llm-extraction-fallback.md) — `--ai-fallback`:
  opt-in LLM fill for empty eligible fields; eligible fields, gates, provenance, and security contracts
- [`scraping/book-record-schema.md`](scraping/book-record-schema.md) — canonical `BookRecord` schema-2
  field table, worked examples per site, and v1→v2 mapping

## Roadmap

- [Roadmap overview](roadmap/README.md) — milestone hierarchy and vocabulary
- [Milestone 0001 — generic implementation plan](roadmap/0001-generic-implementation/plan.md)
- [Milestone 0001 — status](roadmap/0001-generic-implementation/status.md)
