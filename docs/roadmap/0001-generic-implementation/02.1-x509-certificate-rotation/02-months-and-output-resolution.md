# 02 - Months & output resolution

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/02.1-x509-certificate-rotation/README.md)
**Status:** ✅ Complete
**Role:** Python Expert

## Requirements

- `DEFAULT_CERT_MONTHS = 6`, `MIN_CERT_MONTHS = 1`, `MAX_CERT_MONTHS = 24`.
- `resolve_months(months=None) -> int`: argument > `ATLAS_CERT_MONTHS` > default. A non-integer env value or an
  out-of-range value → `RotationError`, raised **before** any HTTP request.
- Output path: `--output` > `ATLAS_CERT_FILE` (`~` expanded) > `default_output_path()`
  = `CONFIG_DIR / f"X509-cert-{UTC %Y%m%d_%H%M%S}.pem"` (same timestamp format as run logs).

## Tests

`tests/unit/backends/mongo/test_cert_rotation.py`: `test_resolve_months_defaults_to_six`,
`test_resolve_months_reads_env`, `test_resolve_months_argument_beats_env`,
`test_resolve_months_rejects_non_integer_env`, `test_resolve_months_rejects_out_of_range`,
`test_resolve_months_accepts_range_boundaries`, `test_default_output_path_is_timestamped_under_config_dir`.
`tests/mock/backends/mongo/test_cert_rotation.py`: `test_rotate_certificate_invalid_months_fails_before_any_request`,
`test_rotate_certificate_explicit_arguments_beat_env`, `test_rotate_certificate_env_overrides_apply_when_no_arguments`.

## Success criteria

- [x] For both months and output: flag > env var > default.
