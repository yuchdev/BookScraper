# secret-scan

Use this skill when changed files may contain hardcoded credentials, tokens, passwords, private keys, connection strings, or similar secrets.

## When to use

- Before finishing work that touched auth, credentials, environment configuration, certificates, logs, or external integrations.
- After editing config files, docs, examples, tests, or code that may have introduced copied real values.
- Whenever a task mentions secret handling, redaction, key rotation, or accidental credential exposure.

## Workflow

1. Scan the narrowest useful scope first: the changed files or the module that handled secrets.
2. Look for high-signal secret shapes and suspicious assignments, while ignoring obvious placeholders such as `example`, `changeme`, `${VAR}`, or other clearly fake values.
3. Report findings as `file:line`, secret type, and a short redacted excerpt only.
4. If a real secret is present, remove it from the change, switch to environment-variable or secret-manager usage, and tell the user whether rotation or history cleanup is needed.
5. If the task is security-sensitive, hand off to `security-auditor` for deeper review.

## Rules

- Never repeat a full secret value back to the user.
- Treat logs, fixtures, docs, JSON data files, and code comments as equally in-scope for leaks.
- If a value might be real and you cannot safely verify that it is fake, treat it as sensitive.
- If the task is only a manual scan, do not change logic; confine edits to removing or redacting the secret material.