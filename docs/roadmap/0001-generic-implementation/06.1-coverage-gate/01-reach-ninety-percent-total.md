# 01 - Reach ≥90% total line coverage

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/06.1-coverage-gate/README.md)
**Status:** ✅ Complete
**Role:** Testing Expert

## Requirements

- Three-tier suite (`tests/unit/`, `tests/mock/`, `tests/integration/`) mirroring `src/bookscraper`'s subpackages.
- Shared fixtures in `tests/conftest.py`: `isolated_config`, `isolated_local_store`, `no_real_env`.
- `uv run pytest -m "not integration" --cov=bookscraper --cov-report=term-missing` ≥ 90% total.

## Evidence (2026-09-24)

`344 passed, 8 deselected`; TOTAL 1577 statements, 78 missed, **95%**. Below 90%: `scrape_details.py` 80%,
`search_utils.py` 88% (handled by subtasks 03 and task 06.0).

## Success criteria

- [x] Total ≥ 90%.
