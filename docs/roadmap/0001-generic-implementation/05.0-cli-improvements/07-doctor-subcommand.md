# 07 - `doctor` subcommand

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/05.0-cli-improvements/README.md)
**Status:** ⬜ Not started
**Depends on:** [Task 02.0 / 05](/docs/roadmap/0001-generic-implementation/02.0-mongodb-auth-configuration/05-single-mongo-client-factory.md), [Task 02.1 / 06](/docs/roadmap/0001-generic-implementation/02.1-x509-certificate-rotation/06-certificate-expiry-inspection.md)
**Role:** Python Expert → Security Auditor (output review)

## Context

Diagnosing a broken setup today means reading CLAUDE.md and running pieces by hand. The 2026-09-23 investigation
("no settings.json, no cert, test URIs unset, so everything skipped") is exactly what a single command should
report in one screen.

## Requirements

- `bookscraper doctor [--store-backend {mongo|json}] [--online]`, via `commands/doctor.py`. Each check returns
  `CheckResult(name, status: OK|WARN|FAIL|SKIP, detail: str)`:

  | Check                    | How                                                                          | Needs `--online` |
  |--------------------------|------------------------------------------------------------------------------|------------------|
  | Python version           | `sys.version_info` vs `requires-python`                                       | no               |
  | Package version          | `importlib.metadata`                                                          | no               |
  | Playwright Chromium      | the executable path from `async_playwright().chromium.executable_path` exists  | no               |
  | `.env` present           | file exists (never print its contents)                                        | no               |
  | settings.json            | `config.load_settings()` + resolve; reports mode (`settings`/`legacy`), `auth_type`, permission warning | no |
  | Client cert              | resolved path exists; expiry via `read_certificate_expiry`                    | no               |
  | JSON store               | path writable; file parses; book count                                        | no               |
  | Log dir                  | writable; number of retained logs                                            | no               |
  | `ANTHROPIC_API_KEY`      | **presence only** (`set` / `not set`)                                        | no               |
  | `ATLAS_*` vars           | presence only, per variable                                                  | no               |
  | MongoDB ping             | `check_mongodb_connection()`                                                  | yes              |

- Output: an aligned table. Exit `0` if no FAIL, else `1`. `WARN` doesn't fail.
- **Never** prints a secret value, a full URI, or a host. The detail text for credential checks is limited to
  `set` / `not set` / `resolved from env X` / `resolved from literal`.
- `--online` is off by default, so `doctor` is instant and offline-safe.

## Files

- Create `src/bookscraper/commands/doctor.py`; modify `src/bookscraper/cli.py`, `src/bookscraper/main.py`.
- Tests: `tests/mock/commands/test_doctor.py`, `tests/unit/test_cli.py`.

## Tests

- `test_doctor_all_ok_exits_zero`
- `test_doctor_missing_chromium_is_fail`
- `test_doctor_reports_settings_mode_and_auth_type`
- `test_doctor_never_prints_secret_values` (seed env with sentinel secrets; assert none appear in stdout)
- `test_doctor_mongo_ping_only_with_online_flag`
- `test_doctor_warn_does_not_fail_exit_code`

## Success criteria

- [ ] On the 2026-09-23 machine state, `doctor` would have reported "no settings.json (legacy mode), no client
      cert, MONGODB_URI not set" in one run.
