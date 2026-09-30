# Documentation Registry

## Architecture & Design

- [ADR index](adr/) — architectural decision records
- [Agent runbooks](agent/) — agent-specific operation guides
- [Security](security/) — threat models and security reviews

## Configuration

- [Configuration examples](examples/) — annotated `settings.json` and other config templates
  - [`settings.example.json`](examples/settings.example.json) — MongoDB auth settings.json reference

## Development

- [Developer guides](dev/) — setup, conventions, and contribution guides
- [Test guides](test/) — testing strategy and tier documentation

## Runbooks

- [`docs/runbooks/x509-certificate-rotation.md`](runbooks/x509-certificate-rotation.md) —
  Issuing, verifying, and scheduling Atlas-managed X.509 certificate rotation; expiry inspection;
  recovery procedures; troubleshooting matrix

## Scraping

- [Schema detection](scraping/schema-detection.md) — AI-assisted CSS selector detection guide
- [ADR 0002 — AI-assisted schema detection](adr/0002-ai-assisted-schema-detection.md)

## Roadmap

- [Milestone 0001 — generic implementation](roadmap/0001-generic-implementation/plan.md)
