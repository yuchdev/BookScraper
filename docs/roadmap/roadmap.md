# Book Scraper Roadmap - Overview

The roadmap is organised as **Milestone → Task → Subtask**; see [README.md](/docs/roadmap/README.md) for the
taxonomy, file conventions, and linking rules.

## Milestone 0001 - Generic Implementation

Spec: [plan.md](/docs/roadmap/0001-generic-implementation/plan.md) ·
Progress: [status.md](/docs/roadmap/0001-generic-implementation/status.md)

| Area              | Tasks                                                                                          |
|-------------------|------------------------------------------------------------------------------------------------|
| Storage           | 01.0 Storage backend abstraction                                                               |
| MongoDB Atlas     | 02.0 Auth configuration · 02.1 X.509 certificate rotation (`ATLAS_*`, `cert_rotation.py`)      |
| Scraping          | 03.0 Engine hardening · 03.1 Canonical data model · 03.2 Politeness & anti-bot · 03.3 Multi-site search |
| AI tooling        | 04.0 Playwright + Claude schema detection                                                      |
| CLI               | 05.0 CLI improvements                                                                          |
| Quality           | 06.0 Real-HTML mock test corpus · 06.1 90% coverage gate · 06.2 Continuous integration          |
| Operations        | 07.0 Observability & run reporting                                                             |
| Hygiene           | 08.0 Repository hygiene & documentation                                                        |
