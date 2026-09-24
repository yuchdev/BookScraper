# 01 - Secret-redacting log filter

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/07.0-observability-and-run-reporting/README.md)
**Status:** ⬜ Not started
**Role:** Security Auditor → Python Expert

## Requirements

- `book_utils.redact(text: str) -> str` (pure). The patterns are compiled once:

  | Pattern                                                  | Replacement                          |
  |----------------------------------------------------------|--------------------------------------|
  | `mongodb(\+srv)?://<user>:<pass>@`                        | `mongodb\1://<redacted>@`             |
  | `sk-ant-[A-Za-z0-9_\-]{10,}`                             | `sk-ant-<redacted>`                  |
  | `(?i)(authorization:\s*bearer\s+)[A-Za-z0-9._\-]+`        | `\1<redacted>`                        |
  | `(?i)(client_secret|password|access_token)=([^&\s]+)`    | `\1=<redacted>`                       |
  | `-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----` | `<redacted private key>` |

- `RedactingFilter(logging.Filter)`: redacts `record.msg` after `%`-formatting (use `record.getMessage()`, then set
  `record.msg = redacted; record.args = ()`), and redacts `record.exc_text` / formatted tracebacks through a
  `RedactingFormatter` subclass. Attach it to the file handler in `configure_logging()`.
- `print_log()` runs `redact()` on its text before writing to both the console and the file.
- Cross-check the pattern list with `.claude/skills/secret-scan/references/pattern-catalog.md`. Anything that
  catalog blocks in source code should also be redacted in logs.

## Files

- Modify `src/bookscraper/book_utils.py`.
- Tests: `tests/unit/test_book_utils.py` (pure `redact`), `tests/mock/test_book_utils.py` (handler integration).

## Tests

- `test_redact_mongodb_srv_credentials`
- `test_redact_anthropic_key`
- `test_redact_bearer_token_and_query_secrets`
- `test_redact_private_key_block`
- `test_redact_leaves_ordinary_text_untouched`
- `test_log_record_with_args_is_redacted_after_formatting`
- `test_exception_traceback_is_redacted_in_log_file`
- `test_print_log_redacts_console_and_file`

## Success criteria

- [ ] Logging `pymongo.errors.ConfigurationError("... mongodb+srv://u:p@h ...")` with `exc_info=True` leaves no
      `u:p` in the log file.
