# 08 - CLI reference documentation

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/05.0-cli-improvements/README.md)
**Status:** ⬜ Not started
**Depends on:** [07-doctor-subcommand.md](/docs/roadmap/0001-generic-implementation/05.0-cli-improvements/07-doctor-subcommand.md)
**Role:** Docs Writer

## Requirements

- `docs/cli.md`: one section per subcommand (`scrape-urls`, `search`, `rotate-cert`, `detect-schema`, `migrate`,
  `doctor`) with a synopsis, a flag table (flag, `dest`, default, env override, description), examples, and exit
  codes.
- A guard test that keeps the doc honest: walk `build_parser()`'s subparsers and assert that every
  `option_strings` entry appears in `docs/cli.md`. A new flag without docs fails CI.
- Replace CLAUDE.md's *Running* examples with a short list plus a link to `docs/cli.md`, and update README's
  quick start to match.

## Files

- Create `docs/cli.md`.
- Create `tests/unit/test_cli_docs.py`.
- Modify `CLAUDE.md`, `README.md`, `docs/README.md`.

## Tests

- `test_every_cli_flag_documented_in_docs_cli_md`
- `/link-check docs/cli.md CLAUDE.md README.md`

## Success criteria

- [ ] No CLI flag exists that is missing from `docs/cli.md`.
