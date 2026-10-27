# Test Coverage

Coverage is measured as the **combined line+branch** figure for `src/bookscraper/` and must
stay at or above **90%** at every level: the aggregate gate and the per-module floor both
require 90%. `scripts/*.py` is out of scope.

## Quick Start

```bash
# 1. Install dependencies (once per clone)
uv sync

# 2. Run the default suite with coverage (offline, fast)
uv run pytest --cov

# 3. Open an HTML report (written to htmlcov/)
uv run pytest --cov --cov-report=html
open htmlcov/index.html

# 4. Check the per-module floor as well
uv run pytest --cov --cov-report=json -q
uv run python scripts/check_module_coverage.py
```

Both `htmlcov/` and `coverage.json` are gitignored.

## Threshold and Enforcement

Coverage is configured in `pyproject.toml` under `[tool.coverage.*]`:

- **`[tool.coverage.run] branch = true`** — measures both line and branch coverage so that a
  taken-but-not-both-arms branch counts as a gap.
- **`[tool.coverage.report] fail_under = 90`** — `uv run pytest --cov` fails the run when the
  combined total drops below 90%. There is no separate `--cov-fail-under` flag needed; the
  configuration is authoritative.
- **`[tool.coverage.run] source = ["bookscraper"]`** — `--cov` alone is sufficient; omit
  `--cov=bookscraper`.

`--cov` is deliberately absent from `addopts` in `[tool.pytest.ini_options]` so a single-file
focused run stays fast and the `run_tests` pre-commit hook is unaffected. Add `--cov` explicitly
when you want to measure.

## Test Tiers

| Tier | Directory | Marker | Counts toward coverage |
|------|-----------|--------|------------------------|
| Unit | `tests/unit/` | (none) | Yes |
| Mock | `tests/mock/` | (none) | Yes |
| Mock — offline HTML corpus | `tests/mock/scraping/html/` | `chromium` | Yes (skips cleanly when no browser is available; CI installs Chromium) |
| Integration | `tests/integration/` | `integration` | Excluded from default run |

The default run (`uv run pytest`) excludes `integration`-marked tests via
`addopts = "-m \"not integration\""`. Run integration tests explicitly:

```bash
uv run pytest -m integration
```

Integration tests hit a real MongoDB Atlas cluster and require credentials in `.env`; see the
Testing section of `CLAUDE.md` for the credential setup.

## Running Coverage

### Aggregate gate

```bash
uv run pytest --cov
```

Passes when the combined line+branch total is ≥90%; fails with a coverage error otherwise.
The terminal report shows missing lines (`show_missing = true`) and hides already-covered
modules (`skip_covered = true`).

### HTML report

```bash
uv run pytest --cov --cov-report=html
open htmlcov/index.html
```

Colors: green = covered, yellow = branch-partially-covered, red = uncovered.

### Per-module floor check

A high aggregate can hide a single under-tested module. After a full run:

```bash
uv run pytest --cov --cov-report=json -q
uv run python scripts/check_module_coverage.py
```

`scripts/check_module_coverage.py` reads `coverage.json` (written to the repo root by
`--cov-report=json`) and fails (exit 1) when any `src/bookscraper/` module falls below 90%,
printing a table of offenders. Exit 0 means every module clears its floor. Exit 2 signals a
configuration error (missing `coverage.json` or a malformed exception entry).

## Excluding Code from Coverage

`# pragma: no cover` is allowed only for the patterns listed in
`[tool.coverage.report] exclude_also` in `pyproject.toml`. Those are:

| Pattern | Rationale |
|---------|-----------|
| `if TYPE_CHECKING:` | Import-time type annotation blocks, never executed at runtime |
| `raise NotImplementedError` | Abstract-interface stubs; callers are expected to override these |
| `^\s*\.\.\.$` | Bare `...` ellipsis bodies (protocol stubs, abstract placeholders) |
| `if __name__ == .__main__.:` | Script entry-point guards; exercised by the console-script entry point, not by the test suite |

Do not add `# pragma: no cover` outside these cases. Every new annotation must assert an
observable outcome — tests exist to verify behavior, not just to execute lines.

## Requesting a Floor Exception

When a module genuinely cannot reach 90% yet (for example, because a required subsystem is
not yet built), add a temporary exception to `tests/coverage_floor_exceptions.toml`:

```toml
["bookscraper.scraping.example"]
floor  = 85
reason = "route_handler branches require a live browser; covered in task 06.0"
until  = "task-06.0-subtask-04"
```

All three fields are required; a missing or blank field causes `check_module_coverage.py` to
exit 2 so an exception can never be added silently. The file ships empty — every module is
expected to clear 90% on its own. Remove the entry once the linked task lands.

---

<!-- BEGIN: branch-coverage baseline (task 06.1 / subtask 05) -->
## Branch-coverage baseline (2026-10-24)

> Added by task 06.1 / subtask 05 when `[tool.coverage.run] branch` was flipped to `true`, so the
> ≥90% gate (`fail_under = 90`) now applies to the **combined line+branch** figure. The rest of this
> document still describes an earlier project setup and is reconciled by subtask 06.

Measured with `uv run pytest -m "not integration" --cov --cov-report=json -q`.

- **Combined total:** 98.14% (was 98.55% line-only before the flip).
- **Gate:** `fail_under = 90` kept on the combined figure; `scripts/check_module_coverage.py` (per-module
  floor 90%) passes with every module clearing the floor. No `fail_under` reduction and no
  `coverage_floor_exceptions.toml` entry were needed.
- **Gap closed:** the branch flip dropped `scraping/search_providers.py` to 89.71% (the lazy
  `SearchContext.browser()` launch/reuse guard and the `aclose()` teardown guards were branch-uncovered);
  a new lifecycle test (`tests/mock/scraping/test_search_providers.py::test_browser_launched_once_reused_and_closed_on_aclose`)
  brings it to 100%.

Per-module combined (line+branch) coverage, dated baseline:

| Module | Combined % |
|--------|-----------:|
| `backends/local/store.py` | 96.12% |
| `backends/migration.py` | 100.00% |
| `backends/mongo/cert_rotation.py` | 98.84% |
| `backends/mongo/client.py` | 100.00% |
| `backends/mongo/config.py` | 99.32% |
| `backends/mongo/database.py` | 98.54% |
| `backends/mongo/deduplicate.py` | 100.00% |
| `backends/save_summary.py` | 100.00% |
| `backends/storage.py` | 98.06% |
| `book_utils.py` | 95.24% |
| `cli.py` | 98.41% |
| `commands/_reporting.py` | 100.00% |
| `commands/detect_schema.py` | 100.00% |
| `commands/doctor.py` | 99.58% |
| `commands/migrate.py` | 98.31% |
| `commands/rotate_cert.py` | 100.00% |
| `commands/scrape_urls.py` | 94.07% |
| `commands/search.py` | 92.05% |
| `exitcodes.py` | 100.00% |
| `isbn.py` | 100.00% |
| `main.py` | 100.00% |
| `models.py` | 100.00% |
| `runtime.py` | 100.00% |
| `scraping/ai_common.py` | 99.43% |
| `scraping/ai_extraction.py` | 100.00% |
| `scraping/browser.py` | 100.00% |
| `scraping/dedup.py` | 96.97% |
| `scraping/filters.py` | 94.59% |
| `scraping/normalize.py` | 100.00% |
| `scraping/parameters.py` | 100.00% |
| `scraping/politeness.py` | 98.25% |
| `scraping/results.py` | 100.00% |
| `scraping/safe_text.py` | 100.00% |
| `scraping/sanitize.py` | 99.77% |
| `scraping/schema_detection.py` | 100.00% |
| `scraping/scrape_details.py` | 93.45% |
| `scraping/search_providers.py` | 100.00% |
| `scraping/search_utils.py` | 98.48% |
| `scraping/text.py` | 100.00% |

Package `__init__.py` files and `__main__.py` (all 100.00%) are omitted for brevity.
<!-- END: branch-coverage baseline (task 06.1 / subtask 05) -->
