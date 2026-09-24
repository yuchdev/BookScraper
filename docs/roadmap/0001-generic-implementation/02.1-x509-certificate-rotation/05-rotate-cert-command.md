# 05 - `rotate-cert` command

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/02.1-x509-certificate-rotation/README.md)
**Status:** ✅ Complete
**Depends on:** [01-atlas-admin-api-client.md](/docs/roadmap/0001-generic-implementation/02.1-x509-certificate-rotation/01-atlas-admin-api-client.md), [02-months-and-output-resolution.md](/docs/roadmap/0001-generic-implementation/02.1-x509-certificate-rotation/02-months-and-output-resolution.md), [04-settings-json-repointing.md](/docs/roadmap/0001-generic-implementation/02.1-x509-certificate-rotation/04-settings-json-repointing.md)
**Role:** Python Expert

## Requirements

- `cli.py`: a `rotate-cert` subparser with `--months` (`int`, default `None`), `--output` (default `None`), and
  `--log-severity`. No `--store-backend`.
- `commands/rotate_cert.run(args)`: `load_dotenv()`, then run `rotate_certificate(months, output)` in a worker
  thread via `asyncio.to_thread` (`output` is `Path(...).expanduser()` or `None`). Print the written path and each
  status line (`WARNING:` lines at warning level). A `RotationError` → `print_log` error + `sys.exit(1)`.
- `main.py` dispatches `"rotate-cert"`.

## Tests

`tests/unit/test_cli.py`: `test_rotate_cert_parses_full_valid_arg_set`,
`test_rotate_cert_needs_no_arguments_and_defaults_defer_to_env`, `test_rotate_cert_rejects_non_integer_months`.
`tests/mock/commands/test_rotate_cert.py`: `test_run_success_passes_args_through_and_reports_notes`,
`test_run_without_output_flag_passes_none`, `test_run_expands_user_in_output_flag`,
`test_run_settings_warning_is_logged_as_warning`, `test_run_rotation_error_logs_and_exits_nonzero`.
`tests/mock/backends/mongo/test_cert_rotation.py`: `test_rotate_certificate_happy_path_writes_cert_and_updates_settings`.

## Success criteria

- [x] `uv run bookscraper rotate-cert` exits 0 on success and 1 on any `RotationError`.
