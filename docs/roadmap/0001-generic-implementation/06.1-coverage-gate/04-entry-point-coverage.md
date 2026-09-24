# 04 - Entry-point coverage

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/06.1-coverage-gate/README.md)
**Status:** ⬜ Not started
**Role:** Testing Expert

## Requirements

- Subprocess smoke tests that exercise the real entry points exactly as a user would, without network:
  - `python -m bookscraper --help` → exit 0, output lists all subcommands.
  - `bookscraper --help` via the console script (`shutil.which("bookscraper")` in the venv; skip if absent).
  - `python -m bookscraper search` (missing `--store-backend`) → exit 2 with an argparse message.
- Subprocess coverage: set `COVERAGE_PROCESS_START` and add a `sitecustomize`-free approach using
  `coverage run -m bookscraper` inside the test, **or** accept that subprocesses aren't measured and cover
  `__main__.py` with an in-process `runpy.run_module("bookscraper", run_name="__main__")` under a patched
  `sys.argv`. Pick in-process `runpy` (simpler, no coverage-config gymnastics) and document why.
- Then remove `*/__main__.py` from `omit` (subtask 02).

## Files

- Create `tests/mock/test_entry_points.py`.
- Modify `pyproject.toml`.

## Tests

- `test_python_m_bookscraper_help_lists_subcommands`
- `test_console_script_help` (skip if not installed)
- `test_missing_required_flag_exits_two`
- `test_runpy_main_module_invokes_main_sync`

## Success criteria

- [ ] `__main__.py` and `main.py` at 100%.
