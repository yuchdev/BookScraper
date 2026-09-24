# 04 - Diff report & `detect-schema` command

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/04.0-ai-assisted-schema-detection/README.md)
**Status:** ✅ Complete
**Role:** Python Expert

## Requirements

- `format_diff_report(...)`: per field, shows current vs proposed selector, a changed/unchanged marker, the
  rationale, per-sample match counts, and an inconsistency flag, followed by a summary line.
- `detect_schema(site, urls, model)`: orchestrates load → prompt → validate → report. An unknown site, an empty URL
  list, or a missing key → `SchemaDetectionError`.
- `cli.py` `detect-schema`: `--site` (choices), `--url` (`action="append"`, required), `--output`,
  `--log-severity`.
- `commands/detect_schema.run()`: prints the report to stdout, optionally writes `--output`, and exits 1 on
  `SchemaDetectionError` or on an output-write failure.

## Tests

`tests/unit/scraping/test_schema_detection.py`: `test_format_diff_report_marks_changed_field`,
`test_format_diff_report_marks_unchanged_field`, `test_format_diff_report_flags_inconsistent_field`,
`test_format_diff_report_all_consistent_summary`, `test_format_diff_report_handles_field_not_currently_present`.
`tests/mock/scraping/test_schema_detection.py`: `test_detect_schema_happy_path_flags_inconsistency`,
`test_detect_schema_missing_api_key`, `test_detect_schema_malformed_ai_json_fails_loudly`,
`test_detect_schema_page_load_failure_fails_loudly`, `test_detect_schema_unknown_site`, `test_detect_schema_no_urls`.
`tests/mock/commands/test_detect_schema.py`: `test_run_prints_report_to_stdout`, `test_run_exits_one_on_detection_error`,
`test_run_writes_report_to_output_file`, `test_run_exits_one_when_output_write_fails`.
`tests/unit/test_cli.py`: `test_detect_schema_*` (6 tests).

## Success criteria

- [x] `parameters.py` is never written by any code path.
