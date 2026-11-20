"""Unit tests for bookscraper.cli.build_parser().

The parser is the single translation layer between CLI flags and the attribute
names the rest of the app reads (main.py -> commands/*.py). A drifted dest= or a
silently-dropped required flag reroutes a whole run, so every dest and every
required/choice/default is asserted explicitly here.
"""

from pathlib import Path

import pytest

from bookscraper import cli
from bookscraper.cli import build_parser


def test_scrape_urls_parses_full_valid_arg_set() -> None:
    """
    [Unit] cli: verifies scrape urls parses full valid arg set.

    Scenario:
        Given test inputs and fixtures for scrape urls parses full valid arg set
        When test_scrape_urls_parses_full_valid_arg_set executes the target flow
        Then the expected outcome for scrape urls parses full valid arg set is confirmed.

    Boundaries:
        - Focus: scrape urls parses full valid arg set
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to scrape urls parses full valid arg set
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    args = build_parser().parse_args(
        [
            "scrape-urls",
            "-f",
            "content/urls.json",
            "--staging-file",
            "staging/review.json",
            "--check-mongo",
            "--log-severity",
            "debug",
        ]
    )
    assert args.command == "scrape-urls"
    assert args.input_file == "content/urls.json"
    assert args.staging_file == "staging/review.json"
    assert args.check_mongo is True
    assert args.log_severity == "debug"


def test_scrape_urls_long_input_file_flag() -> None:
    """
    [Unit] cli: verifies scrape urls long input file flag.

    Scenario:
        Given test inputs and fixtures for scrape urls long input file flag
        When test_scrape_urls_long_input_file_flag executes the target flow
        Then the expected outcome for scrape urls long input file flag is confirmed.

    Boundaries:
        - Focus: scrape urls long input file flag
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to scrape urls long input file flag
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    args = build_parser().parse_args(["scrape-urls", "--input-file", "x.json"])
    assert args.input_file == "x.json"


def test_search_parses_full_valid_arg_set() -> None:
    """
    [Unit] cli: verifies search parses full valid arg set.

    Scenario:
        Given test inputs and fixtures for search parses full valid arg set
        When test_search_parses_full_valid_arg_set executes the target flow
        Then the expected outcome for search parses full valid arg set is confirmed.

    Boundaries:
        - Focus: search parses full valid arg set
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to search parses full valid arg set
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    args = build_parser().parse_args(
        [
            "search",
            "--staging-file",
            "s.json",
            "--check-mongo",
            "--max-search-pages",
            "5",
            "--log-severity",
            "warning",
        ]
    )
    assert args.command == "search"
    assert args.staging_file == "s.json"
    assert args.check_mongo is True
    assert args.max_search_pages == 5
    assert args.log_severity == "warning"


def test_scrape_urls_staging_file_defaults_to_staging_dir() -> None:
    """Unit: step 1 writes to staging/books.json unless told otherwise (ADR 0003, resolved question 2).

    Scenario: parse scrape-urls and search with no --staging-file.
    Boundaries: argparse only.
    On failure: scraped books land somewhere other than the documented, gitignored staging/ directory.
    """
    scrape_args = build_parser().parse_args(["scrape-urls", "-f", "x.json"])
    search_args = build_parser().parse_args(["search"])
    assert scrape_args.staging_file == "staging/books.json"
    assert search_args.staging_file == "staging/books.json"


def test_cli_default_matches_staging_module_constant() -> None:
    """Unit: the CLI's literal default stays equal to staging.DEFAULT_STAGING_FILE.

    Scenario: compare cli.DEFAULT_STAGING_FILE with backends.local.staging.DEFAULT_STAGING_FILE.
    Boundaries: constants only.
    On failure: the parser and the staging module disagree about where the staging file lives.
    """
    from bookscraper.backends.local.staging import DEFAULT_STAGING_FILE

    assert Path(cli.DEFAULT_STAGING_FILE) == DEFAULT_STAGING_FILE


def test_check_mongo_defaults_false() -> None:
    """
    [Unit] cli: verifies check mongo defaults false.

    Scenario:
        Given test inputs and fixtures for check mongo defaults false
        When test_check_mongo_defaults_false executes the target flow
        Then the expected outcome for check mongo defaults false is confirmed.

    Boundaries:
        - Focus: check mongo defaults false
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to check mongo defaults false
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    assert build_parser().parse_args(["scrape-urls", "-f", "x.json"]).check_mongo is False
    assert build_parser().parse_args(["search"]).check_mongo is False


@pytest.mark.parametrize(
    "argv",
    [
        ["scrape-urls", "-f", "x.json", "--store-backend", "mongo"],
        ["search", "--store-backend", "json"],
    ],
)
def test_store_backend_flag_rejected(argv: list[str]) -> None:
    """Unit: the removed --store-backend flag is an argparse error, not a silent alias (ADR 0003).

    Scenario: pass --store-backend to either step 1 subcommand.
    Boundaries: argparse only.
    On failure: an old command line would run with a meaning it no longer has.
    """
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(argv)
    assert excinfo.value.code == 2


def test_scrape_urls_requires_input_file() -> None:
    """
    [Unit] cli: verifies scrape urls requires input file.

    Scenario:
        Given test inputs and fixtures for scrape urls requires input file
        When test_scrape_urls_requires_input_file executes the target flow
        Then the expected outcome for scrape urls requires input file is confirmed.

    Boundaries:
        - Focus: scrape urls requires input file
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to scrape urls requires input file
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["scrape-urls"])


def test_invalid_log_severity_choice_rejected() -> None:
    """
    [Unit] cli: verifies invalid log severity choice rejected.

    Scenario:
        Given test inputs and fixtures for invalid log severity choice rejected
        When test_invalid_log_severity_choice_rejected executes the target flow
        Then the expected outcome for invalid log severity choice rejected is confirmed.

    Boundaries:
        - Focus: invalid log severity choice rejected
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to invalid log severity choice rejected
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["search", "--log-severity", "trace"])


def test_search_max_search_pages_defaults_to_three() -> None:
    """
    [Unit] cli: verifies search max search pages defaults to three.

    Scenario:
        Given test inputs and fixtures for search max search pages defaults to three
        When test_search_max_search_pages_defaults_to_three executes the target flow
        Then the expected outcome for search max search pages defaults to three is confirmed.

    Boundaries:
        - Focus: search max search pages defaults to three
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to search max search pages defaults to three
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    args = build_parser().parse_args(["search"])
    assert args.max_search_pages == 3


def test_search_max_search_pages_type_is_int() -> None:
    """
    [Unit] cli: verifies search max search pages type is int.

    Scenario:
        Given test inputs and fixtures for search max search pages type is int
        When test_search_max_search_pages_type_is_int executes the target flow
        Then the expected outcome for search max search pages type is int is confirmed.

    Boundaries:
        - Focus: search max search pages type is int
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to search max search pages type is int
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    args = build_parser().parse_args(["search", "--max-search-pages", "7"])
    assert isinstance(args.max_search_pages, int)


def test_log_severity_defaults_to_info_on_every_subcommand() -> None:
    """
    [Unit] cli: verifies log severity defaults to info on every subcommand.

    Scenario:
        Given test inputs and fixtures for log severity defaults to info on every subcommand
        When test_log_severity_defaults_to_info_on_every_subcommand executes the target flow
        Then the expected outcome for log severity defaults to info on every subcommand is confirmed.

    Boundaries:
        - Focus: log severity defaults to info on every subcommand
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to log severity defaults to info on every subcommand
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    for argv in (["scrape-urls", "-f", "x.json"], ["search"], ["push"], ["pull", "-o", "x.json"]):
        assert build_parser().parse_args(argv).log_severity == "info"


def test_scrape_urls_help_names_push_as_next_step(capsys) -> None:
    """
    [Unit] cli: verifies scrape urls help names push as next step.

    Scenario:
        Given test inputs and fixtures for scrape urls help names push as next step
        When test_scrape_urls_help_names_push_as_next_step executes the target flow
        Then the expected outcome for scrape urls help names push as next step is confirmed.

    Boundaries:
        - Focus: scrape urls help names push as next step
        - Fixtures/params: capsys
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to scrape urls help names push as next step
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["scrape-urls", "--help"])
    assert "bookscraper push" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# push / pull subparsers
# --------------------------------------------------------------------------- #
def test_push_parser_defaults() -> None:
    """Unit: a bare `push` is the safe form - real run, inserts only, every non-rejected document.

    Scenario: parse `push` with no flags.
    Boundaries: argparse only.
    On failure: a default would silently allow updates, skip pending documents, or move the report.
    """
    args = build_parser().parse_args(["push"])
    assert args.command == "push"
    assert args.staging_file == "staging/books.json"
    assert args.dry_run is False
    assert args.allow_updates is False
    assert args.approved_only is False
    assert args.report_file == "push_report.json"


def test_push_parses_full_valid_arg_set() -> None:
    """
    [Unit] cli: verifies push parses full valid arg set.

    Scenario:
        Given test inputs and fixtures for push parses full valid arg set
        When test_push_parses_full_valid_arg_set executes the target flow
        Then the expected outcome for push parses full valid arg set is confirmed.

    Boundaries:
        - Focus: push parses full valid arg set
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to push parses full valid arg set
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    args = build_parser().parse_args(
        [
            "push",
            "--staging-file",
            "staging/books_mongo.json",
            "--dry-run",
            "--allow-updates",
            "--approved-only",
            "--report-file",
            "r.json",
            "--log-severity",
            "error",
        ]
    )
    assert (args.staging_file, args.dry_run, args.allow_updates, args.approved_only, args.report_file) == (
        "staging/books_mongo.json",
        True,
        True,
        True,
        "r.json",
    )
    assert args.log_severity == "error"


def test_pull_requires_output() -> None:
    """
    [Unit] cli: verifies pull requires output.

    Scenario:
        Given test inputs and fixtures for pull requires output
        When test_pull_requires_output executes the target flow
        Then the expected outcome for pull requires output is confirmed.

    Boundaries:
        - Focus: pull requires output
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to pull requires output
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["pull"])


def test_pull_parses_output_and_force() -> None:
    """
    [Unit] cli: verifies pull parses output and force.

    Scenario:
        Given test inputs and fixtures for pull parses output and force
        When test_pull_parses_output_and_force executes the target flow
        Then the expected outcome for pull parses output and force is confirmed.

    Boundaries:
        - Focus: pull parses output and force
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to pull parses output and force
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    args = build_parser().parse_args(["pull", "-o", "staging/books_mongo.json", "--force"])
    assert args.command == "pull"
    assert args.output == "staging/books_mongo.json"
    assert args.force is True
    assert build_parser().parse_args(["pull", "--output", "x.json"]).force is False


def test_missing_subcommand_rejected() -> None:
    """
    [Unit] cli: verifies missing subcommand rejected.

    Scenario:
        Given test inputs and fixtures for missing subcommand rejected
        When test_missing_subcommand_rejected executes the target flow
        Then the expected outcome for missing subcommand rejected is confirmed.

    Boundaries:
        - Focus: missing subcommand rejected
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to missing subcommand rejected
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_rotate_cert_parses_full_valid_arg_set() -> None:
    """
    [Unit] cli: verifies rotate cert parses full valid arg set.

    Scenario:
        Given test inputs and fixtures for rotate cert parses full valid arg set
        When test_rotate_cert_parses_full_valid_arg_set executes the target flow
        Then the expected outcome for rotate cert parses full valid arg set is confirmed.

    Boundaries:
        - Focus: rotate cert parses full valid arg set
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to rotate cert parses full valid arg set
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    args = build_parser().parse_args(
        ["rotate-cert", "--months", "12", "--output", "/tmp/cert.pem", "--log-severity", "error"]
    )
    assert args.command == "rotate-cert"
    assert args.months == 12
    assert isinstance(args.months, int)
    assert args.output == "/tmp/cert.pem"
    assert args.log_severity == "error"


def test_rotate_cert_needs_no_arguments_and_defaults_defer_to_env() -> None:
    """
    [Unit] cli: verifies rotate cert needs no arguments and defaults defer to env.

    Scenario:
        Given test inputs and fixtures for rotate cert needs no arguments and defaults defer to env
        When test_rotate_cert_needs_no_arguments_and_defaults_defer_to_env executes the target flow
        Then the expected outcome for rotate cert needs no arguments and defaults defer to env is confirmed.

    Boundaries:
        - Focus: rotate cert needs no arguments and defaults defer to env
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to rotate cert needs no arguments and defaults defer to env
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    args = build_parser().parse_args(["rotate-cert"])
    assert args.months is None
    assert args.output is None
    assert args.log_severity == "info"


def test_rotate_cert_rejects_non_integer_months() -> None:
    """
    [Unit] cli: verifies rotate cert rejects non integer months.

    Scenario:
        Given test inputs and fixtures for rotate cert rejects non integer months
        When test_rotate_cert_rejects_non_integer_months executes the target flow
        Then the expected outcome for rotate cert rejects non integer months is confirmed.

    Boundaries:
        - Focus: rotate cert rejects non integer months
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to rotate cert rejects non integer months
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["rotate-cert", "--months", "soon"])


# --------------------------------------------------------------------------- #
# detect-schema subparser
# --------------------------------------------------------------------------- #
def test_detect_schema_parses_full_valid_arg_set() -> None:
    """
    [Unit] cli: verifies detect schema parses full valid arg set.

    Scenario:
        Given test inputs and fixtures for detect schema parses full valid arg set
        When test_detect_schema_parses_full_valid_arg_set executes the target flow
        Then the expected outcome for detect schema parses full valid arg set is confirmed.

    Boundaries:
        - Focus: detect schema parses full valid arg set
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to detect schema parses full valid arg set
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
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
            "--output",
            "report.txt",
            "--log-severity",
            "debug",
        ]
    )
    assert args.command == "detect-schema"
    assert args.site == "amazon"
    assert args.url == ["https://www.amazon.com/dp/1", "https://www.amazon.com/dp/2"]
    assert args.output == "report.txt"
    assert args.log_severity == "debug"


def test_detect_schema_url_is_repeatable_into_a_list() -> None:
    """
    [Unit] cli: verifies detect schema url is repeatable into a list.

    Scenario:
        Given test inputs and fixtures for detect schema url is repeatable into a list
        When test_detect_schema_url_is_repeatable_into_a_list executes the target flow
        Then the expected outcome for detect schema url is repeatable into a list is confirmed.

    Boundaries:
        - Focus: detect schema url is repeatable into a list
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to detect schema url is repeatable into a list
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    args = build_parser().parse_args(["detect-schema", "--site", "leanpub", "--url", "https://leanpub.com/a"])
    assert args.url == ["https://leanpub.com/a"]


def test_detect_schema_requires_site() -> None:
    """
    [Unit] cli: verifies detect schema requires site.

    Scenario:
        Given test inputs and fixtures for detect schema requires site
        When test_detect_schema_requires_site executes the target flow
        Then the expected outcome for detect schema requires site is confirmed.

    Boundaries:
        - Focus: detect schema requires site
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to detect schema requires site
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["detect-schema", "--url", "https://x/1"])


def test_detect_schema_requires_url() -> None:
    """
    [Unit] cli: verifies detect schema requires url.

    Scenario:
        Given test inputs and fixtures for detect schema requires url
        When test_detect_schema_requires_url executes the target flow
        Then the expected outcome for detect schema requires url is confirmed.

    Boundaries:
        - Focus: detect schema requires url
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to detect schema requires url
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["detect-schema", "--site", "amazon"])


def test_detect_schema_rejects_unknown_site() -> None:
    """
    [Unit] cli: verifies detect schema rejects unknown site.

    Scenario:
        Given test inputs and fixtures for detect schema rejects unknown site
        When test_detect_schema_rejects_unknown_site executes the target flow
        Then the expected outcome for detect schema rejects unknown site is confirmed.

    Boundaries:
        - Focus: detect schema rejects unknown site
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to detect schema rejects unknown site
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["detect-schema", "--site", "goodreads", "--url", "https://x/1"])


def test_detect_schema_output_defaults_to_none() -> None:
    """
    [Unit] cli: verifies detect schema output defaults to none.

    Scenario:
        Given test inputs and fixtures for detect schema output defaults to none
        When test_detect_schema_output_defaults_to_none executes the target flow
        Then the expected outcome for detect schema output defaults to none is confirmed.

    Boundaries:
        - Focus: detect schema output defaults to none
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to detect schema output defaults to none
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    args = build_parser().parse_args(["detect-schema", "--site", "oreilly", "--url", "https://x/1"])
    assert args.output is None


def test_detect_schema_log_severity_defaults_to_info() -> None:
    """
    [Unit] cli: verifies detect schema log severity defaults to info.

    Scenario:
        Given test inputs and fixtures for detect schema log severity defaults to info
        When test_detect_schema_log_severity_defaults_to_info executes the target flow
        Then the expected outcome for detect schema log severity defaults to info is confirmed.

    Boundaries:
        - Focus: detect schema log severity defaults to info
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to detect schema log severity defaults to info
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    args = build_parser().parse_args(["detect-schema", "--site", "packtpub", "--url", "https://x/1"])
    assert args.log_severity == "info"
