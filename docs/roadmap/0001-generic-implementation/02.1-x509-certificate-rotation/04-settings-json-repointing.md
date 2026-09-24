# 04 - settings.json repointing

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/02.1-x509-certificate-rotation/README.md)
**Status:** ✅ Complete
**Depends on:** [03-atomic-owner-only-write.md](/docs/roadmap/0001-generic-implementation/02.1-x509-certificate-rotation/03-atomic-owner-only-write.md)
**Role:** Python Expert

## Requirements

`update_settings_after_rotation(new_cert_path) -> list[str]`:

| `settings.json` state           | Action                                                        |
|---------------------------------|---------------------------------------------------------------|
| absent                          | untouched; note that the legacy glob picks up the new cert    |
| malformed                       | untouched; `WARNING:` line                                    |
| `auth_type != "x509"`           | untouched; note                                               |
| `x509.cert.source == "literal"` | set `value` to the new **filename**; atomic `0600` rewrite    |
| `x509.cert.source == "env"`     | untouched; reminder naming the env var and the new path       |
| anything else                   | untouched; "unrecognized shape" note                          |

## Tests

`tests/unit/backends/mongo/test_cert_rotation.py`: `test_update_settings_no_file_reports_legacy_fallback`,
`test_update_settings_malformed_file_warns_and_is_untouched`, `test_update_settings_pass_auth_is_untouched`,
`test_update_settings_literal_cert_is_repointed`, `test_update_settings_env_cert_only_reminds`,
`test_update_settings_unrecognized_cert_shape_is_untouched`.
`tests/mock/backends/mongo/test_cert_rotation.py`: `test_rotate_certificate_does_not_touch_settings_when_atlas_fails`.

## Success criteria

- [x] The app never edits `.env` or the process environment.
