# Task 02.0 - MongoDB Auth Configuration

**Parent milestone:** [plan.md](/docs/roadmap/0001-generic-implementation/plan.md)
**Status:** 🔶 In progress
**Category:** feature | **Priority:** P0

## Scope

`backends/mongo/config.py::resolve_mongo_connection()` is the single place that decides how the app authenticates to
MongoDB Atlas. If `~/.bookscrapper/settings.json` exists, it alone decides: each field is explicitly
`{"source": "env" | "literal"}`, and any mismatch raises `ConfigError`. Otherwise the legacy
`MONGODB_URI` / `TLS_CERT_FILE` env vars apply, with a newest-`X509-cert-*.pem` glob fallback.

Subtasks 01-03 record the delivered resolver. Subtasks 04-06 fix what's left:
- `get_mongo_collection()` prints *"Connection to MongoDB Atlas successful."* even when the connection failed.
- `book_utils.check_mongodb_connection()` is a second, divergent `MongoClient` construction path. It is only called
  from tests, ignores `TLS_CA_FILE`, and uses a timeout the real path lacks.
- `settings.json` gets no validation of its permissions or shape beyond the resolver's own field checks, and the
  database/collection names can only come from undocumented env vars.

## Subtasks

| #  | Document                                                                          | Status         | Blocks |
|----|-----------------------------------------------------------------------------------|----------------|--------|
| 01 | [Deterministic settings.json resolver](/docs/roadmap/0001-generic-implementation/02.0-mongodb-auth-configuration/01-settings-json-resolver.md)        | ✅ Complete    | 02, 06 |
| 02 | [Legacy env-var fallback](/docs/roadmap/0001-generic-implementation/02.0-mongodb-auth-configuration/02-legacy-env-fallback.md)                         | ✅ Complete    | -      |
| 03 | [X.509 mechanism diagnostic](/docs/roadmap/0001-generic-implementation/02.0-mongodb-auth-configuration/03-x509-mechanism-diagnostic.md)                | ✅ Complete    | -      |
| 04 | [Honest connection logging](/docs/roadmap/0001-generic-implementation/02.0-mongodb-auth-configuration/04-honest-connection-logging.md)                 | ⬜ Not started | 05     |
| 05 | [Single MongoClient factory](/docs/roadmap/0001-generic-implementation/02.0-mongodb-auth-configuration/05-single-mongo-client-factory.md)              | ⬜ Not started | -      |
| 06 | [settings.json validation & hardening](/docs/roadmap/0001-generic-implementation/02.0-mongodb-auth-configuration/06-settings-json-hardening.md)        | ⬜ Not started | -      |

## Key constraints

- **Determinism:** when `settings.json` exists, nothing else is consulted, and nothing is guessed.
- `book_utils.py` must import `config` **inside** the function body (circular-import guard, see CLAUDE.md).
- Tests never touch the real `~/.bookscrapper/settings.json` (`isolated_config` fixture).
- Security-sensitive: subtasks 05 and 06 handle connection strings. Log `uri_has_embedded_credentials()`-style
  booleans, never the URI itself.
