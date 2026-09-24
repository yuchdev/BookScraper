# 01 - Atlas Admin API client

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/02.1-x509-certificate-rotation/README.md)
**Status:** ✅ Complete
**Role:** Security Auditor → Python Expert

## Requirements

- Constants `ATLAS_OAUTH_URL = "https://cloud.mongodb.com/api/oauth/token"`,
  `ATLAS_API_BASE_URL = "https://cloud.mongodb.com/api/atlas/v2"`; exception `RotationError`.
- `_require_env(name) -> str`: `RotationError` naming the missing `ATLAS_*` variable.
- `get_access_token(client_id, client_secret) -> str`: `httpx.post` with HTTP Basic auth and
  `grant_type=client_credentials`, then `raise_for_status()`; returns `access_token`.
- `issue_certificate(token, project_id, db_user, months) -> bytes`: `POST
  {base}/groups/{project_id}/databaseUsers/{db_user}/certs` with JSON body `{"monthsUntilExpiration": months}`
  (in the body, **not** the query string) and a versioned `Accept` header; returns the PEM bytes.
- `rotate_certificate()` maps `httpx.HTTPStatusError` → `RotationError("... failed with HTTP <code>.")` (no body),
  and any other `httpx.HTTPError` → `RotationError("Could not reach the Atlas API: <ExcType>.")`.

## Tests

`tests/mock/backends/mongo/test_cert_rotation.py`: `test_get_access_token_posts_client_credentials`,
`test_get_access_token_propagates_http_errors`, `test_issue_certificate_sends_months_in_json_body_not_query`,
`test_rotate_certificate_requires_each_credential`,
`test_rotate_certificate_http_status_error_becomes_rotation_error_without_leaking_body`,
`test_rotate_certificate_network_error_becomes_rotation_error`.

## Success criteria

- [x] No Atlas response body can reach a log line or an exception message.
