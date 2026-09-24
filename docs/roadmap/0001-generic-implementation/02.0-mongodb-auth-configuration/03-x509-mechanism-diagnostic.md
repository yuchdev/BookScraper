# 03 - X.509 mechanism diagnostic

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/02.0-mongodb-auth-configuration/README.md)
**Status:** ✅ Complete
**Role:** Python Expert

## Context

pymongo never infers `MONGODB-X509` from the presence of a client certificate. A cert paired with a URI that lacks
`authMechanism=MONGODB-X509` either authenticates as SCRAM (if the URI embeds a password) or fails authentication,
which looks like a server-side rejection.

## Requirements

- `database.uri_selects_x509_auth(uri) -> bool` - true iff the query string has `authMechanism=MONGODB-X509`
  (case-insensitive).
- `database.uri_has_embedded_credentials(uri) -> bool` - true iff the netloc carries `user:pass@`.
- `_warn_if_x509_mechanism_missing(uri, cert_file)`: mechanism present → silent; SCRAM credentials plus a stray
  cert → warning; neither → critical log naming the fix (`authMechanism=MONGODB-X509&authSource=$external`).
  It never logs the URI itself.

## Tests

`tests/mock/backends/mongo/test_database.py`: `test_uri_selects_x509_auth`, `test_uri_has_embedded_credentials`,
`test_x509_mechanism_present_warns_nothing`, `test_scram_uri_with_stray_cert_warns_but_does_not_escalate`,
`test_cert_without_mechanism_or_credentials_is_critical`.

## Success criteria

- [x] A misconfigured X.509 setup yields an actionable local diagnostic before any network attempt.
