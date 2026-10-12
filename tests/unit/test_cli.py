"""Unit tests for bookscraper.cli.build_parser().

The parser is the single translation layer between CLI flags and the attribute
names the rest of the app reads (main.py -> commands/*.py). A drifted dest= or a
silently-dropped required flag reroutes a whole run, so every dest and every
required/choice/default is asserted explicitly here.
"""

import pathlib

import pytest

from bookscraper import cli
from bookscraper.backends import storage
from bookscraper.cli import SITE_KEYS, STORE_BACKENDS, build_parser
from bookscraper.scraping.parameters import site_constants


def test_scrape_urls_parses_full_valid_arg_set():
    """
    [Unit] cli scrape-urls: parses -f, --store-backend, and --log-severity into the expected dest= attributes.

    Scenario:
        Given a scrape-urls invocation with --input-file, --store-backend mongo, and --log-severity debug
        When build_parser().parse_args() processes the argument list
        Then args.command, args.input_file, args.store_backend, and args.log_severity all hold the expected values.

    Boundaries:
        - Pure argparse; no filesystem, no network
        - Covers the short -f form of --input-file alongside the named flags

    On failure, first check:
        - Whether dest= names on the scrape-urls subparser flags still match the asserted attribute names
        - Recent changes to the scrape-urls subparser definition in cli.py
    """
    args = build_parser().parse_args(
        [
            "scrape-urls",
            "-f",
            "content/urls.csv",
            "--store-backend",
            "mongo",
            "--log-severity",
            "debug",
        ]
    )
    assert args.command == "scrape-urls"
    assert args.input_file == "content/urls.csv"
    assert args.store_backend == "mongo"
    assert args.log_severity == "debug"


def test_scrape_urls_long_input_file_flag():
    """
    [Unit] cli scrape-urls: --input-file (long form) binds to args.input_file correctly.

    Scenario:
        Given a scrape-urls invocation using --input-file instead of -f
        When build_parser().parse_args() processes the argument list
        Then args.input_file holds the given path and args.store_backend holds the given backend.

    Boundaries:
        - Pure argparse; verifies only the long-flag alias, not the short form
        - No file is opened

    On failure, first check:
        - Whether the dest= for --input-file in the scrape-urls subparser is still "input_file"
        - Recent renames of the long flag in cli.py
    """
    args = build_parser().parse_args(["scrape-urls", "--input-file", "x.csv", "--store-backend", "json"])
    assert args.input_file == "x.csv"
    assert args.store_backend == "json"


def test_search_parses_full_valid_arg_set():
    """
    [Unit] cli search: parses --store-backend, --max-search-pages, and --log-severity into the expected dest= attributes.

    Scenario:
        Given a search invocation with --store-backend json, --max-search-pages 5, and --log-severity warning
        When build_parser().parse_args() processes the argument list
        Then args.command, args.store_backend, args.max_search_pages, and args.log_severity hold the expected values.

    Boundaries:
        - Pure argparse; no filesystem, no network
        - max_search_pages=5 is an integer, not a string

    On failure, first check:
        - Whether dest= names on the search subparser flags still match the asserted attribute names
        - Recent changes to the search subparser definition in cli.py
    """
    args = build_parser().parse_args(
        [
            "search",
            "--store-backend",
            "json",
            "--max-search-pages",
            "5",
            "--log-severity",
            "warning",
        ]
    )
    assert args.command == "search"
    assert args.store_backend == "json"
    assert args.max_search_pages == 5
    assert args.log_severity == "warning"


def test_scrape_urls_requires_store_backend():
    """
    [Unit] cli scrape-urls: omitting --store-backend causes argparse to exit.

    Scenario:
        Given a scrape-urls invocation with only -f but no --store-backend
        When build_parser().parse_args() processes the argument list
        Then SystemExit is raised (argparse reports the missing required argument).

    Boundaries:
        - Pure argparse; confirms the flag is required, not optional with a default

    On failure, first check:
        - Whether required=True is still set on --store-backend in the scrape-urls subparser
        - Whether a default value was accidentally added to the flag in cli.py
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["scrape-urls", "-f", "content/urls.csv"])


def test_search_requires_store_backend():
    """
    [Unit] cli search: omitting --store-backend causes argparse to exit.

    Scenario:
        Given a search invocation with no arguments
        When build_parser().parse_args() processes the argument list
        Then SystemExit is raised (argparse reports the missing required argument).

    Boundaries:
        - Pure argparse; confirms the flag is required, not optional with a default

    On failure, first check:
        - Whether required=True is still set on --store-backend in the search subparser
        - Whether a default value was accidentally added to the flag in cli.py
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["search"])


def test_scrape_urls_requires_input_file():
    """
    [Unit] cli scrape-urls: omitting -f / --input-file causes argparse to exit.

    Scenario:
        Given a scrape-urls invocation with --store-backend but no input file flag
        When build_parser().parse_args() processes the argument list
        Then SystemExit is raised (argparse reports the missing required argument).

    Boundaries:
        - Pure argparse; confirms the flag is required, not optional with a default

    On failure, first check:
        - Whether required=True is still set on --input-file in the scrape-urls subparser
        - Whether a default value was accidentally added to the flag in cli.py
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["scrape-urls", "--store-backend", "mongo"])


def test_invalid_store_backend_choice_rejected():
    """
    [Unit] cli: an unrecognized --store-backend value causes argparse to exit.

    Scenario:
        Given a scrape-urls invocation with --store-backend sqlite (not in the choices list)
        When build_parser().parse_args() processes the argument list
        Then SystemExit is raised (argparse reports an invalid choice).

    Boundaries:
        - Pure argparse; tests rejection of a value outside {mongo, json}
        - Does not test acceptance of valid choices

    On failure, first check:
        - Whether the choices list on --store-backend still excludes "sqlite"
        - Whether the choices list was accidentally removed or widened in cli.py
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["scrape-urls", "-f", "x.csv", "--store-backend", "sqlite"])


def test_invalid_log_severity_choice_rejected():
    """
    [Unit] cli: an unrecognized --log-severity value causes argparse to exit.

    Scenario:
        Given a search invocation with --log-severity trace (not in the choices list)
        When build_parser().parse_args() processes the argument list
        Then SystemExit is raised (argparse reports an invalid choice).

    Boundaries:
        - Pure argparse; tests rejection of a value outside {debug, info, warning, error}
        - Does not test acceptance of valid choices

    On failure, first check:
        - Whether the choices list on --log-severity excludes "trace"
        - Whether the choices list was accidentally removed or widened in cli.py
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["search", "--store-backend", "json", "--log-severity", "trace"])


def test_search_max_search_pages_defaults_to_three():
    """
    [Unit] cli search: --max-search-pages defaults to 3 when not supplied.

    Scenario:
        Given a search invocation with only --store-backend and no --max-search-pages
        When build_parser().parse_args() processes the argument list
        Then args.max_search_pages equals 3.

    Boundaries:
        - Pure argparse; verifies the default value, not the type

    On failure, first check:
        - Whether the default= for --max-search-pages in the search subparser was changed in cli.py
    """
    args = build_parser().parse_args(["search", "--store-backend", "json"])
    assert args.max_search_pages == 3


def test_search_max_search_pages_type_is_int():
    """
    [Unit] cli search: --max-search-pages is coerced to int by argparse.

    Scenario:
        Given a search invocation with --max-search-pages 7 (a string on the command line)
        When build_parser().parse_args() processes the argument list
        Then args.max_search_pages is an int, not a string.

    Boundaries:
        - Pure argparse; verifies the type= conversion, not the default

    On failure, first check:
        - Whether type=int is still set on --max-search-pages in the search subparser in cli.py
    """
    args = build_parser().parse_args(["search", "--store-backend", "json", "--max-search-pages", "7"])
    assert isinstance(args.max_search_pages, int)


def test_site_flag_repeatable_and_validated():
    """
    [Unit] cli search: --site is repeatable into args.sites and an unknown site is rejected.

    Scenario:
        Given a search invocation repeating --site leanpub and --site amazon
        When build_parser().parse_args() processes the argument list
        Then args.sites is ["leanpub", "amazon"]; and a --site goodreads value causes SystemExit.

    Boundaries:
        - Pure argparse; choices come from cli.SITE_KEYS, so an off-list site is rejected at parse
          time (a site with no provider yet is caught later by resolve_search_targets, not here)
        - Omitting --site leaves args.sites None so the command falls back to SITES_TO_SCRAPE

    On failure, first check:
        - Whether action="append", dest="sites", and choices=list(SITE_KEYS) are still set on --site
        - Whether default=None is still set on --site in the search subparser in cli.py
    """
    args = build_parser().parse_args(["search", "--store-backend", "json", "--site", "leanpub", "--site", "amazon"])
    assert args.sites == ["leanpub", "amazon"]

    default_args = build_parser().parse_args(["search", "--store-backend", "json"])
    assert default_args.sites is None

    with pytest.raises(SystemExit):
        build_parser().parse_args(["search", "--store-backend", "json", "--site", "goodreads"])


def test_site_keys_match_site_constants():
    """
    [Unit] cli: SITE_KEYS stays in sync with the top-level keys of site_constants.

    Scenario:
        Given cli.SITE_KEYS and scraping.parameters.site_constants
        When their key sets are compared
        Then every SITE_KEYS entry is a site_constants key and vice versa.

    Boundaries:
        - Pure data comparison; no argparse, no network
        - Guards the hardcoded SITE_KEYS tuple (cli.py imports only argparse) against drifting
          from the real site_constants

    On failure, first check:
        - Whether a site was added to or removed from site_constants without updating SITE_KEYS in
          cli.py
    """
    assert set(SITE_KEYS) == set(site_constants)


def test_max_search_pages_rejects_zero():
    """
    [Unit] cli search: --max-search-pages 0 (and a negative) causes argparse to exit.

    Scenario:
        Given a search invocation with --max-search-pages 0, then one with --max-search-pages -2
        When build_parser().parse_args() processes each argument list
        Then SystemExit is raised each time (the positive-int type rejects values below 1).

    Boundaries:
        - Pure argparse; exercises the shared _positive_int type= now applied to --max-search-pages

    On failure, first check:
        - Whether type=_positive_int is still set on --max-search-pages in the search subparser
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["search", "--store-backend", "json", "--max-search-pages", "0"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["search", "--store-backend", "json", "--max-search-pages", "-2"])


def test_search_query_and_queries_file_flags_parse():
    """
    [Unit] cli search: -q/--query is repeatable into args.queries and --queries-file binds its path.

    Scenario:
        Given a search invocation with -q Rust, --query Go, --queries-file q.txt, and --no-filters
        When build_parser().parse_args() processes the argument list
        Then args.queries is ["Rust", "Go"], args.queries_file is "q.txt", and args.no_filters is True.

    Boundaries:
        - Pure argparse; no file is opened (the queries-file is read later by resolve_search_targets)
        - Covers both the short -q and long --query forms accumulating into one list

    On failure, first check:
        - Whether action="append"/dest="queries" is still set on -q/--query and dest="queries_file"
          on --queries-file in cli.py
        - Whether --no-filters still maps to dest="no_filters" with action="store_true"
    """
    args = build_parser().parse_args(
        [
            "search",
            "--store-backend",
            "json",
            "-q",
            "Rust",
            "--query",
            "Go",
            "--queries-file",
            "q.txt",
            "--no-filters",
        ]
    )
    assert args.queries == ["Rust", "Go"]
    assert args.queries_file == "q.txt"
    assert args.no_filters is True


def test_search_targeting_flags_default_to_none_and_false():
    """
    [Unit] cli search: the new targeting flags default to None/False when not supplied.

    Scenario:
        Given a search invocation with only --store-backend
        When build_parser().parse_args() processes the argument list
        Then args.sites, args.queries, and args.queries_file are None and args.no_filters is False.

    Boundaries:
        - Pure argparse; verifies the defaults that signal fall-back to parameters.py constants

    On failure, first check:
        - Whether default=None is still set on --site/--query/--queries-file and default False
          (store_true) on --no-filters in cli.py
    """
    args = build_parser().parse_args(["search", "--store-backend", "json"])
    assert args.sites is None
    assert args.queries is None
    assert args.queries_file is None
    assert args.no_filters is False


def test_log_severity_defaults_to_info_on_both_subcommands():
    """
    [Unit] cli: --log-severity defaults to "info" on both scrape-urls and search.

    Scenario:
        Given scrape-urls and search invocations with no --log-severity flag
        When build_parser().parse_args() processes each argument list
        Then both args.log_severity values equal "info".

    Boundaries:
        - Pure argparse; tests the default on two subparsers independently
        - Does not test rotate-cert or detect-schema subparsers

    On failure, first check:
        - Whether default="info" is still set on --log-severity in both subparsers in cli.py
    """
    scrape_args = build_parser().parse_args(["scrape-urls", "-f", "x.csv", "--store-backend", "mongo"])
    search_args = build_parser().parse_args(["search", "--store-backend", "mongo"])
    assert scrape_args.log_severity == "info"
    assert search_args.log_severity == "info"


def test_missing_subcommand_rejected():
    """
    [Unit] cli: an empty argument list causes argparse to exit.

    Scenario:
        Given an invocation with no arguments at all
        When build_parser().parse_args() processes the empty list
        Then SystemExit is raised (argparse requires a subcommand).

    Boundaries:
        - Pure argparse; verifies the top-level parser requires a subcommand

    On failure, first check:
        - Whether required=True is still set on the subparsers group in cli.py
        - Whether a default subcommand was accidentally added
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_rotate_cert_parses_full_valid_arg_set():
    """
    [Unit] cli rotate-cert: parses --months, --output, and --log-severity into the expected dest= attributes.

    Scenario:
        Given a rotate-cert invocation with --months 12, --output /tmp/cert.pem, and --log-severity error
        When build_parser().parse_args() processes the argument list
        Then args.command, args.months, args.output, and args.log_severity hold the expected values and args.months is an int.

    Boundaries:
        - Pure argparse; no filesystem, no network, no Atlas API
        - Verifies both value and type for --months

    On failure, first check:
        - Whether dest= names on the rotate-cert subparser flags still match the asserted attribute names
        - Whether type=int is still set on --months in cli.py
    """
    args = build_parser().parse_args(
        ["rotate-cert", "--months", "12", "--output", "/tmp/cert.pem", "--log-severity", "error"]
    )
    assert args.command == "rotate-cert"
    assert args.months == 12
    assert isinstance(args.months, int)
    assert args.output == "/tmp/cert.pem"
    assert args.log_severity == "error"


def test_rotate_cert_needs_no_arguments_and_defaults_defer_to_env():
    """
    [Unit] cli rotate-cert: all optional flags default to None or "info" when not supplied.

    Scenario:
        Given a rotate-cert invocation with no flags
        When build_parser().parse_args() processes the argument list
        Then args.months and args.output are None, and args.log_severity is "info".

    Boundaries:
        - Pure argparse; confirms defaults so the command knows to read ATLAS_CERT_MONTHS/ATLAS_CERT_FILE from env
        - Does not test environment-variable resolution (that belongs to rotate_cert.py)

    On failure, first check:
        - Whether default=None is still set on --months and --output in cli.py
        - Whether default="info" is still set on --log-severity in cli.py
    """
    args = build_parser().parse_args(["rotate-cert"])
    assert args.months is None
    assert args.output is None
    assert args.log_severity == "info"


def test_rotate_cert_rejects_non_integer_months():
    """
    [Unit] cli rotate-cert: a non-integer --months value causes argparse to exit.

    Scenario:
        Given a rotate-cert invocation with --months soon
        When build_parser().parse_args() processes the argument list
        Then SystemExit is raised (argparse type conversion fails).

    Boundaries:
        - Pure argparse; verifies the type=int conversion on --months rejects non-numeric input

    On failure, first check:
        - Whether type=int is still set on --months in the rotate-cert subparser in cli.py
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["rotate-cert", "--months", "soon"])


def test_rotate_cert_check_flags():
    """
    [Unit] cli rotate-cert: --check and --warn-days parse into their expected dest= attributes.

    Scenario:
        Given a rotate-cert invocation with --check and --warn-days 14
        When build_parser().parse_args() processes the argument list
        Then args.check is True, args.warn_days is 14, and args.warn_days is an int.

    Boundaries:
        - Pure argparse; verifies both the flag and its integer companion
        - Does not test expiry-inspection logic

    On failure, first check:
        - Whether dest= for --check and --warn-days still match the asserted attribute names in cli.py
        - Whether type=int is still set on --warn-days
    """
    args = build_parser().parse_args(["rotate-cert", "--check", "--warn-days", "14"])
    assert args.check is True
    assert args.warn_days == 14
    assert isinstance(args.warn_days, int)


def test_rotate_cert_check_defaults():
    """
    [Unit] cli rotate-cert: --check defaults to False and --warn-days defaults to 30.

    Scenario:
        Given a rotate-cert invocation with no flags
        When build_parser().parse_args() processes the argument list
        Then args.check is False and args.warn_days is 30.

    Boundaries:
        - Pure argparse; verifies default values for the expiry-check pair

    On failure, first check:
        - Whether store_true action and default=False are still set on --check in cli.py
        - Whether default=30 is still set on --warn-days in cli.py
    """
    args = build_parser().parse_args(["rotate-cert"])
    assert args.check is False
    assert args.warn_days == 30


def test_rotate_cert_warn_days_rejects_non_integer():
    """
    [Unit] cli rotate-cert: a non-integer --warn-days value causes argparse to exit.

    Scenario:
        Given a rotate-cert invocation with --check --warn-days soon
        When build_parser().parse_args() processes the argument list
        Then SystemExit is raised (argparse type conversion fails).

    Boundaries:
        - Pure argparse; verifies the type=int conversion on --warn-days rejects non-numeric input

    On failure, first check:
        - Whether type=int is still set on --warn-days in the rotate-cert subparser in cli.py
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["rotate-cert", "--check", "--warn-days", "soon"])


def test_rotate_cert_verify_flags():
    """
    [Unit] cli rotate-cert: --no-verify and --verify-attempts parse into their expected dest= attributes.

    Scenario:
        Given a rotate-cert invocation with --no-verify and --verify-attempts 8
        When build_parser().parse_args() processes the argument list
        Then args.no_verify is True, args.verify_attempts is 8, and args.verify_attempts is an int.

    Boundaries:
        - Pure argparse; verifies both the boolean flag and its integer companion
        - Does not test actual certificate verification

    On failure, first check:
        - Whether dest= for --no-verify and --verify-attempts still match the asserted attribute names in cli.py
        - Whether type=int is still set on --verify-attempts
    """
    args = build_parser().parse_args(["rotate-cert", "--no-verify", "--verify-attempts", "8"])
    assert args.no_verify is True
    assert args.verify_attempts == 8
    assert isinstance(args.verify_attempts, int)


def test_rotate_cert_verify_defaults():
    """
    [Unit] cli rotate-cert: --no-verify defaults to False and --verify-attempts defaults to 5.

    Scenario:
        Given a rotate-cert invocation with no flags
        When build_parser().parse_args() processes the argument list
        Then args.no_verify is False and args.verify_attempts is 5.

    Boundaries:
        - Pure argparse; verifies default values for the verification-control pair

    On failure, first check:
        - Whether store_true action and default=False are still set on --no-verify in cli.py
        - Whether default=5 is still set on --verify-attempts in cli.py
    """
    args = build_parser().parse_args(["rotate-cert"])
    assert args.no_verify is False
    assert args.verify_attempts == 5


def test_rotate_cert_verify_attempts_rejects_non_integer():
    """
    [Unit] cli rotate-cert: a non-integer --verify-attempts value causes argparse to exit.

    Scenario:
        Given a rotate-cert invocation with --verify-attempts many
        When build_parser().parse_args() processes the argument list
        Then SystemExit is raised (argparse type conversion fails).

    Boundaries:
        - Pure argparse; verifies the type=int conversion on --verify-attempts rejects non-numeric input

    On failure, first check:
        - Whether type=int is still set on --verify-attempts in the rotate-cert subparser in cli.py
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["rotate-cert", "--verify-attempts", "many"])


def test_rotate_cert_prune_defaults():
    """
    [Unit] cli rotate-cert: --keep defaults to 2 and --no-prune defaults to False.

    Scenario:
        Given a rotate-cert invocation with no flags
        When build_parser().parse_args() processes the argument list
        Then args.keep is 2, args.keep is an int, and args.no_prune is False.

    Boundaries:
        - Pure argparse; verifies default values for the pruning-control pair

    On failure, first check:
        - Whether default=2 and type=int are still set on --keep in cli.py
        - Whether store_true and default=False are still set on --no-prune in cli.py
    """
    args = build_parser().parse_args(["rotate-cert"])
    assert args.keep == 2
    assert isinstance(args.keep, int)
    assert args.no_prune is False


def test_rotate_cert_keep_and_no_prune_flags():
    """
    [Unit] cli rotate-cert: --keep and --no-prune parse into their expected dest= attributes.

    Scenario:
        Given a rotate-cert invocation with --keep 5 --no-prune
        When build_parser().parse_args() processes the argument list
        Then args.keep is 5 and args.no_prune is True.

    Boundaries:
        - Pure argparse; verifies both the integer argument and the boolean flag

    On failure, first check:
        - Whether dest= for --keep and --no-prune still match the asserted attribute names in cli.py
    """
    args = build_parser().parse_args(["rotate-cert", "--keep", "5", "--no-prune"])
    assert args.keep == 5
    assert args.no_prune is True


def test_keep_zero_rejected_by_cli():
    """
    [Unit] cli rotate-cert: --keep 0 causes argparse to exit.

    Scenario:
        Given a rotate-cert invocation with --keep 0
        When build_parser().parse_args() processes the argument list
        Then SystemExit is raised (the custom type rejects 0 to prevent pruning all certs).

    Boundaries:
        - Pure argparse; exercises the positive-integer custom type= on --keep

    On failure, first check:
        - Whether the custom argparse type for --keep still rejects 0 in cli.py
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["rotate-cert", "--keep", "0"])


def test_keep_negative_rejected_by_cli():
    """
    [Unit] cli rotate-cert: negative --keep value causes argparse to exit.

    Scenario:
        Given a rotate-cert invocation with --keep -3
        When build_parser().parse_args() processes the argument list
        Then SystemExit is raised (the custom type rejects negative values).

    Boundaries:
        - Pure argparse; exercises the positive-integer custom type= on --keep

    On failure, first check:
        - Whether the custom argparse type for --keep still rejects negative values in cli.py
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["rotate-cert", "--keep", "-3"])


def test_keep_non_integer_rejected_by_cli():
    """
    [Unit] cli rotate-cert: non-integer --keep value causes argparse to exit.

    Scenario:
        Given a rotate-cert invocation with --keep lots
        When build_parser().parse_args() processes the argument list
        Then SystemExit is raised (the custom type rejects non-numeric input).

    Boundaries:
        - Pure argparse; exercises the integer-conversion step of the custom type= on --keep

    On failure, first check:
        - Whether the custom argparse type for --keep still validates integers in cli.py
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["rotate-cert", "--keep", "lots"])


# --------------------------------------------------------------------------- #
# detect-schema subparser
# --------------------------------------------------------------------------- #
def test_detect_schema_parses_full_valid_arg_set():
    """
    [Unit] cli detect-schema: parses all flags into their expected dest= attributes.

    Scenario:
        Given a detect-schema invocation with --site amazon, two --url values, --model,
              --max-input-tokens, --page-type search, --output, and --log-severity debug
        When build_parser().parse_args() processes the argument list
        Then all asserted dest= attributes hold the supplied values, and args.url is a list.

    Boundaries:
        - Pure argparse; no filesystem, no network, no Anthropic API
        - Covers --page-type search (non-default) and multiple --url repetitions

    On failure, first check:
        - Whether dest= names on the detect-schema subparser flags still match the asserted attribute names
        - Recent changes to the detect-schema subparser definition in cli.py
    """
    args = build_parser().parse_args(
        [
            "detect-schema",
            "--site",
            "amazon",
            "--url",
            "https://www.amazon.com/dp/1",
            "--url",
            "https://www.amazon.com/dp/2",
            "--model",
            "claude-sonnet-5",
            "--max-input-tokens",
            "25000",
            "--page-type",
            "search",
            "--output",
            "report.txt",
            "--log-severity",
            "debug",
        ]
    )
    assert args.command == "detect-schema"
    assert args.site == "amazon"
    assert args.url == ["https://www.amazon.com/dp/1", "https://www.amazon.com/dp/2"]
    assert args.model == "claude-sonnet-5"
    assert args.max_input_tokens == 25000
    assert args.page_type == "search"
    assert args.output == "report.txt"
    assert args.log_severity == "debug"


def test_cli_page_type_defaults_to_detail():
    """
    [Unit] cli detect-schema: --page-type defaults to "detail" when not supplied.

    Scenario:
        Given a detect-schema invocation with only the required --site and --url flags
        When build_parser().parse_args() processes the argument list
        Then args.page_type equals "detail".

    Boundaries:
        - Pure argparse; verifies the default value, not the set of valid choices

    On failure, first check:
        - Whether default="detail" is still set on --page-type in the detect-schema subparser in cli.py
    """
    args = build_parser().parse_args(["detect-schema", "--site", "amazon", "--url", "https://x/1"])
    assert args.page_type == "detail"


def test_detect_schema_rejects_unknown_page_type():
    """
    [Unit] cli detect-schema: an unrecognized --page-type value causes argparse to exit.

    Scenario:
        Given a detect-schema invocation with --page-type api (not in the choices list)
        When build_parser().parse_args() processes the argument list
        Then SystemExit is raised (argparse reports an invalid choice).

    Boundaries:
        - Pure argparse; verifies rejection of a value outside {detail, search}

    On failure, first check:
        - Whether the choices list on --page-type still excludes "api" in cli.py
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["detect-schema", "--site", "amazon", "--url", "https://x/1", "--page-type", "api"])


def test_detect_schema_model_defaults_to_none():
    """
    [Unit] cli detect-schema: --model defaults to None when not supplied.

    Scenario:
        Given a detect-schema invocation with no --model flag
        When build_parser().parse_args() processes the argument list
        Then args.model is None, signalling that schema_detection.py should use its own default.

    Boundaries:
        - Pure argparse; verifies the default, not model-name validation (done at runtime)

    On failure, first check:
        - Whether default=None is still set on --model in the detect-schema subparser in cli.py
    """
    args = build_parser().parse_args(["detect-schema", "--site", "amazon", "--url", "https://x/1"])
    assert args.model is None


def test_detect_schema_max_input_tokens_defaults_to_40000():
    """
    [Unit] cli detect-schema: --max-input-tokens defaults to 40000 when not supplied.

    Scenario:
        Given a detect-schema invocation with no --max-input-tokens flag
        When build_parser().parse_args() processes the argument list
        Then args.max_input_tokens equals 40000.

    Boundaries:
        - Pure argparse; verifies the default value only

    On failure, first check:
        - Whether default=40000 is still set on --max-input-tokens in the detect-schema subparser in cli.py
    """
    args = build_parser().parse_args(["detect-schema", "--site", "amazon", "--url", "https://x/1"])
    assert args.max_input_tokens == 40000


def test_detect_schema_url_is_repeatable_into_a_list():
    """
    [Unit] cli detect-schema: a single --url argument is stored as a one-element list.

    Scenario:
        Given a detect-schema invocation with exactly one --url
        When build_parser().parse_args() processes the argument list
        Then args.url is a list containing that one URL.

    Boundaries:
        - Pure argparse; verifies action="append" behavior with a single repetition

    On failure, first check:
        - Whether action="append" is still set on --url in the detect-schema subparser in cli.py
    """
    args = build_parser().parse_args(["detect-schema", "--site", "leanpub", "--url", "https://leanpub.com/a"])
    assert args.url == ["https://leanpub.com/a"]


def test_detect_schema_requires_site():
    """
    [Unit] cli detect-schema: omitting --site causes argparse to exit.

    Scenario:
        Given a detect-schema invocation with --url but no --site
        When build_parser().parse_args() processes the argument list
        Then SystemExit is raised (argparse reports the missing required argument).

    Boundaries:
        - Pure argparse; confirms the flag is required

    On failure, first check:
        - Whether required=True is still set on --site in the detect-schema subparser in cli.py
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["detect-schema", "--url", "https://x/1"])


def test_detect_schema_requires_url():
    """
    [Unit] cli detect-schema: omitting --url (and --html-file) causes argparse to exit.

    Scenario:
        Given a detect-schema invocation with --site but no --url or --html-file
        When build_parser().parse_args() processes the argument list
        Then SystemExit is raised (the mutually-exclusive required group is missing both options).

    Boundaries:
        - Pure argparse; confirms the input-source group is required

    On failure, first check:
        - Whether required=True is still set on the mutually_exclusive_group in cli.py
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["detect-schema", "--site", "amazon"])


def test_detect_schema_rejects_unknown_site():
    """
    [Unit] cli detect-schema: an unrecognized --site value causes argparse to exit.

    Scenario:
        Given a detect-schema invocation with --site goodreads (not in the choices list)
        When build_parser().parse_args() processes the argument list
        Then SystemExit is raised (argparse reports an invalid choice).

    Boundaries:
        - Pure argparse; verifies rejection of a site outside {amazon, packtpub, leanpub, oreilly}

    On failure, first check:
        - Whether the choices list on --site still excludes "goodreads" in cli.py
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["detect-schema", "--site", "goodreads", "--url", "https://x/1"])


def test_detect_schema_output_defaults_to_none():
    """
    [Unit] cli detect-schema: --output defaults to None when not supplied.

    Scenario:
        Given a detect-schema invocation with no --output flag
        When build_parser().parse_args() processes the argument list
        Then args.output is None (no file will be written).

    Boundaries:
        - Pure argparse; verifies the default value only

    On failure, first check:
        - Whether default=None is still set on --output in the detect-schema subparser in cli.py
    """
    args = build_parser().parse_args(["detect-schema", "--site", "oreilly", "--url", "https://x/1"])
    assert args.output is None


def test_detect_schema_log_severity_defaults_to_info():
    """
    [Unit] cli detect-schema: --log-severity defaults to "info" when not supplied.

    Scenario:
        Given a detect-schema invocation with no --log-severity flag
        When build_parser().parse_args() processes the argument list
        Then args.log_severity equals "info".

    Boundaries:
        - Pure argparse; verifies the default value

    On failure, first check:
        - Whether default="info" is still set on --log-severity in the detect-schema subparser in cli.py
    """
    args = build_parser().parse_args(["detect-schema", "--site", "packtpub", "--url", "https://x/1"])
    assert args.log_severity == "info"


def test_html_file_and_url_are_mutually_exclusive():
    """
    [Unit] cli: verifies that --url and --html-file are mutually exclusive and that one is required.

    Scenario:
        Given a detect-schema invocation that supplies both --url and --html-file
        When the parser processes the args
        Then SystemExit is raised; the same applies when neither is supplied.

    Boundaries:
        - Pure argparse; no filesystem, no network
        - Both conflict direction (both given) and missing direction (neither given) are checked

    On failure, first check:
        - Whether the mutually_exclusive_group(required=True) is still present in cli.py
        - Recent changes to detect-schema subparser argument definitions
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["detect-schema", "--site", "amazon", "--url", "https://x/1", "--html-file", "a.html"]
        )
    # Neither given is also an error (the group is required).
    with pytest.raises(SystemExit):
        build_parser().parse_args(["detect-schema", "--site", "amazon"])


def test_detect_schema_html_file_is_repeatable_into_a_list():
    """
    [Unit] cli detect-schema: multiple --html-file arguments accumulate into a list and args.url is None.

    Scenario:
        Given a detect-schema invocation with two --html-file flags
        When build_parser().parse_args() processes the argument list
        Then args.html_file is a list of both paths and args.url is None (the mutually-exclusive other side).

    Boundaries:
        - Pure argparse; verifies both action="append" behavior and the mutual-exclusion side effect

    On failure, first check:
        - Whether action="append" is still set on --html-file and whether it is in the mutually-exclusive group in cli.py
    """
    args = build_parser().parse_args(
        ["detect-schema", "--site", "amazon", "--html-file", "a.html", "--html-file", "b.html"]
    )
    assert args.html_file == ["a.html", "b.html"]
    assert args.url is None


def test_detect_schema_replay_and_record_default_to_none():
    """
    [Unit] cli detect-schema: --replay and --record default to None when not supplied.

    Scenario:
        Given a detect-schema invocation with no --replay or --record flags
        When build_parser().parse_args() processes the argument list
        Then args.replay and args.record are both None.

    Boundaries:
        - Pure argparse; verifies defaults for offline-mode flags

    On failure, first check:
        - Whether default=None is still set on --replay and --record in cli.py
    """
    args = build_parser().parse_args(["detect-schema", "--site", "amazon", "--url", "https://x/1"])
    assert args.replay is None
    assert args.record is None


def test_detect_schema_replay_and_record_parse():
    """
    [Unit] cli detect-schema: --replay and --record parse into their expected dest= attributes.

    Scenario:
        Given a detect-schema invocation with --html-file a.html, --replay r.json, and --record rec.json
        When build_parser().parse_args() processes the argument list
        Then args.replay equals "r.json" and args.record equals "rec.json".

    Boundaries:
        - Pure argparse; no filesystem access
        - Uses --html-file (not --url) to satisfy the required input-source group

    On failure, first check:
        - Whether dest= for --replay and --record still match the asserted attribute names in cli.py
    """
    args = build_parser().parse_args(
        [
            "detect-schema",
            "--site",
            "amazon",
            "--html-file",
            "a.html",
            "--replay",
            "r.json",
            "--record",
            "rec.json",
        ]
    )
    assert args.replay == "r.json"
    assert args.record == "rec.json"


def test_detect_schema_format_defaults_to_text():
    """
    [Unit] cli detect-schema: --format defaults to "text" and --check-current-only defaults to False.

    Scenario:
        Given a detect-schema invocation with no --format or --check-current-only flags
        When build_parser().parse_args() processes the argument list
        Then args.format equals "text" and args.check_current_only is False.

    Boundaries:
        - Pure argparse; verifies defaults for two flags together

    On failure, first check:
        - Whether default="text" is still set on --format and default=False on --check-current-only in cli.py
    """
    args = build_parser().parse_args(["detect-schema", "--site", "amazon", "--url", "https://x/1"])
    assert args.format == "text"
    assert args.check_current_only is False


def test_detect_schema_format_json_parses():
    """
    [Unit] cli detect-schema: --format json parses to "json".

    Scenario:
        Given a detect-schema invocation with --format json
        When build_parser().parse_args() processes the argument list
        Then args.format equals "json".

    Boundaries:
        - Pure argparse; verifies acceptance of the non-default choice

    On failure, first check:
        - Whether "json" is still in the choices list for --format in cli.py
    """
    args = build_parser().parse_args(["detect-schema", "--site", "amazon", "--url", "https://x/1", "--format", "json"])
    assert args.format == "json"


def test_detect_schema_rejects_unknown_format():
    """
    [Unit] cli detect-schema: an unrecognized --format value causes argparse to exit.

    Scenario:
        Given a detect-schema invocation with --format yaml (not in the choices list)
        When build_parser().parse_args() processes the argument list
        Then SystemExit is raised (argparse reports an invalid choice).

    Boundaries:
        - Pure argparse; verifies rejection of a value outside {text, json}

    On failure, first check:
        - Whether the choices list on --format still excludes "yaml" in cli.py
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["detect-schema", "--site", "amazon", "--url", "https://x/1", "--format", "yaml"])


def test_detect_schema_check_current_only_flag_sets_true():
    """
    [Unit] cli detect-schema: --check-current-only sets args.check_current_only to True.

    Scenario:
        Given a detect-schema invocation with --html-file a.html and --check-current-only
        When build_parser().parse_args() processes the argument list
        Then args.check_current_only is True.

    Boundaries:
        - Pure argparse; verifies the store_true action for --check-current-only

    On failure, first check:
        - Whether action="store_true" is still set on --check-current-only in cli.py
        - Whether dest= still maps to "check_current_only"
    """
    args = build_parser().parse_args(
        ["detect-schema", "--site", "amazon", "--html-file", "a.html", "--check-current-only"]
    )
    assert args.check_current_only is True


# --------------------------------------------------------------------------- #
# shared parent parsers (subtask 05.0/01)
# --------------------------------------------------------------------------- #
def test_log_severity_defined_once_in_source():
    """
    [Unit] cli: the "--log-severity" flag literal appears exactly once in cli.py.

    Scenario:
        Given the cli.py source file on disk
        When the "--log-severity" string literal is counted
        Then it appears exactly once (only in the shared _logging_parent parent parser).

    Boundaries:
        - Reads the cli.py source text directly; no argparse, no network
        - Guards the de-duplication: the flag was copy-pasted onto four subparsers before 05.0/01

    On failure, first check:
        - Whether a subparser re-declared --log-severity inline instead of using parents=[_logging_parent()]
        - Whether _logging_parent() still owns the single definition in cli.py
    """
    source = pathlib.Path(cli.__file__).read_text(encoding="utf-8")
    assert source.count('"--log-severity"') == 1


def test_store_backend_choices_match_resolver():
    """
    [Unit] cli: --store-backend choices come from the same STORE_BACKENDS tuple the resolver uses.

    Scenario:
        Given cli.STORE_BACKENDS and backends.storage's imported copy of it
        When each is compared and each listed backend is parsed as a --store-backend value
        Then both names refer to the identical tuple object, every listed backend is an accepted
             choice, and a backend outside the tuple is rejected.

    Boundaries:
        - Pure argparse plus an identity check; no MongoDB connection, no filesystem, no network
        - storage.py imports STORE_BACKENDS from cli.py, so the two lists cannot drift

    On failure, first check:
        - Whether backends/storage.py still imports STORE_BACKENDS from cli.py (not a local copy)
        - Whether _store_parent() still builds its choices from list(STORE_BACKENDS) in cli.py
    """
    assert storage.STORE_BACKENDS is STORE_BACKENDS
    for backend in STORE_BACKENDS:
        args = build_parser().parse_args(["scrape-urls", "-f", "x.csv", "--store-backend", backend])
        assert args.store_backend == backend
    with pytest.raises(SystemExit):
        build_parser().parse_args(["scrape-urls", "-f", "x.csv", "--store-backend", "sqlite"])
