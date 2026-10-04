"""Unit tests for bookscraper.cli.build_parser().

The parser is the single translation layer between CLI flags and the attribute
names the rest of the app reads (main.py -> commands/*.py). A drifted dest= or a
silently-dropped required flag reroutes a whole run, so every dest and every
required/choice/default is asserted explicitly here.
"""

import pytest

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
    args = build_parser().parse_args(["scrape-urls", "--input-file", "x.csv", "--store-backend", "json"])
    assert args.input_file == "x.csv"
    assert args.store_backend == "json"


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


def test_scrape_urls_requires_store_backend() -> None:
    """
    [Unit] cli: verifies scrape urls requires store backend.

    Scenario:
        Given test inputs and fixtures for scrape urls requires store backend
        When test_scrape_urls_requires_store_backend executes the target flow
        Then the expected outcome for scrape urls requires store backend is confirmed.

    Boundaries:
        - Focus: scrape urls requires store backend
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to scrape urls requires store backend
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["scrape-urls", "-f", "content/urls.csv"])


def test_search_requires_store_backend() -> None:
    """
    [Unit] cli: verifies search requires store backend.

    Scenario:
        Given test inputs and fixtures for search requires store backend
        When test_search_requires_store_backend executes the target flow
        Then the expected outcome for search requires store backend is confirmed.

    Boundaries:
        - Focus: search requires store backend
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to search requires store backend
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["search"])


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
        build_parser().parse_args(["scrape-urls", "--store-backend", "mongo"])


def test_invalid_store_backend_choice_rejected() -> None:
    """
    [Unit] cli: verifies invalid store backend choice rejected.

    Scenario:
        Given test inputs and fixtures for invalid store backend choice rejected
        When test_invalid_store_backend_choice_rejected executes the target flow
        Then the expected outcome for invalid store backend choice rejected is confirmed.

    Boundaries:
        - Focus: invalid store backend choice rejected
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to invalid store backend choice rejected
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["scrape-urls", "-f", "x.csv", "--store-backend", "sqlite"])


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
        build_parser().parse_args(["search", "--store-backend", "json", "--log-severity", "trace"])


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
    args = build_parser().parse_args(["search", "--store-backend", "json"])
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
    args = build_parser().parse_args(["search", "--store-backend", "json", "--max-search-pages", "7"])
    assert isinstance(args.max_search_pages, int)


def test_log_severity_defaults_to_info_on_both_subcommands() -> None:
    """
    [Unit] cli: verifies log severity defaults to info on both subcommands.

    Scenario:
        Given test inputs and fixtures for log severity defaults to info on both subcommands
        When test_log_severity_defaults_to_info_on_both_subcommands executes the target flow
        Then the expected outcome for log severity defaults to info on both subcommands is confirmed.

    Boundaries:
        - Focus: log severity defaults to info on both subcommands
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to log severity defaults to info on both subcommands
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    scrape_args = build_parser().parse_args(["scrape-urls", "-f", "x.csv", "--store-backend", "mongo"])
    search_args = build_parser().parse_args(["search", "--store-backend", "mongo"])
    assert scrape_args.log_severity == "info"
    assert search_args.log_severity == "info"


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


def test_rotate_cert_check_flags() -> None:
    """
    [Unit] cli: verifies rotate cert check flags.

    Scenario:
        Given test inputs and fixtures for rotate cert check flags
        When test_rotate_cert_check_flags executes the target flow
        Then the expected outcome for rotate cert check flags is confirmed.

    Boundaries:
        - Focus: rotate cert check flags
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to rotate cert check flags
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    args = build_parser().parse_args(["rotate-cert", "--check", "--warn-days", "14"])
    assert args.check is True
    assert args.warn_days == 14
    assert isinstance(args.warn_days, int)


def test_rotate_cert_check_defaults() -> None:
    """
    [Unit] cli: verifies rotate cert check defaults.

    Scenario:
        Given test inputs and fixtures for rotate cert check defaults
        When test_rotate_cert_check_defaults executes the target flow
        Then the expected outcome for rotate cert check defaults is confirmed.

    Boundaries:
        - Focus: rotate cert check defaults
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to rotate cert check defaults
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    args = build_parser().parse_args(["rotate-cert"])
    assert args.check is False
    assert args.warn_days == 30


def test_rotate_cert_warn_days_rejects_non_integer() -> None:
    """
    [Unit] cli: verifies rotate cert warn days rejects non integer.

    Scenario:
        Given test inputs and fixtures for rotate cert warn days rejects non integer
        When test_rotate_cert_warn_days_rejects_non_integer executes the target flow
        Then the expected outcome for rotate cert warn days rejects non integer is confirmed.

    Boundaries:
        - Focus: rotate cert warn days rejects non integer
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to rotate cert warn days rejects non integer
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["rotate-cert", "--check", "--warn-days", "soon"])


def test_rotate_cert_verify_flags() -> None:
    """
    [Unit] cli: verifies rotate cert verify flags.

    Scenario:
        Given test inputs and fixtures for rotate cert verify flags
        When test_rotate_cert_verify_flags executes the target flow
        Then the expected outcome for rotate cert verify flags is confirmed.

    Boundaries:
        - Focus: rotate cert verify flags
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to rotate cert verify flags
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    args = build_parser().parse_args(["rotate-cert", "--no-verify", "--verify-attempts", "8"])
    assert args.no_verify is True
    assert args.verify_attempts == 8
    assert isinstance(args.verify_attempts, int)


def test_rotate_cert_verify_defaults() -> None:
    """
    [Unit] cli: verifies rotate cert verify defaults.

    Scenario:
        Given test inputs and fixtures for rotate cert verify defaults
        When test_rotate_cert_verify_defaults executes the target flow
        Then the expected outcome for rotate cert verify defaults is confirmed.

    Boundaries:
        - Focus: rotate cert verify defaults
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to rotate cert verify defaults
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    args = build_parser().parse_args(["rotate-cert"])
    assert args.no_verify is False
    assert args.verify_attempts == 5


def test_rotate_cert_verify_attempts_rejects_non_integer() -> None:
    """
    [Unit] cli: verifies rotate cert verify attempts rejects non integer.

    Scenario:
        Given test inputs and fixtures for rotate cert verify attempts rejects non integer
        When test_rotate_cert_verify_attempts_rejects_non_integer executes the target flow
        Then the expected outcome for rotate cert verify attempts rejects non integer is confirmed.

    Boundaries:
        - Focus: rotate cert verify attempts rejects non integer
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to rotate cert verify attempts rejects non integer
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["rotate-cert", "--verify-attempts", "many"])


def test_rotate_cert_prune_defaults() -> None:
    """
    [Unit] cli: verifies rotate cert prune defaults.

    Scenario:
        Given test inputs and fixtures for rotate cert prune defaults
        When test_rotate_cert_prune_defaults executes the target flow
        Then the expected outcome for rotate cert prune defaults is confirmed.

    Boundaries:
        - Focus: rotate cert prune defaults
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to rotate cert prune defaults
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    args = build_parser().parse_args(["rotate-cert"])
    assert args.keep == 2
    assert isinstance(args.keep, int)
    assert args.no_prune is False


def test_rotate_cert_keep_and_no_prune_flags() -> None:
    """
    [Unit] cli: verifies rotate cert keep and no prune flags.

    Scenario:
        Given test inputs and fixtures for rotate cert keep and no prune flags
        When test_rotate_cert_keep_and_no_prune_flags executes the target flow
        Then the expected outcome for rotate cert keep and no prune flags is confirmed.

    Boundaries:
        - Focus: rotate cert keep and no prune flags
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to rotate cert keep and no prune flags
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    args = build_parser().parse_args(["rotate-cert", "--keep", "5", "--no-prune"])
    assert args.keep == 5
    assert args.no_prune is True


def test_keep_zero_rejected_by_cli() -> None:
    """
    [Unit] cli: verifies keep zero rejected by cli.

    Scenario:
        Given test inputs and fixtures for keep zero rejected by cli
        When test_keep_zero_rejected_by_cli executes the target flow
        Then the expected outcome for keep zero rejected by cli is confirmed.

    Boundaries:
        - Focus: keep zero rejected by cli
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to keep zero rejected by cli
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["rotate-cert", "--keep", "0"])


def test_keep_negative_rejected_by_cli() -> None:
    """
    [Unit] cli: verifies keep negative rejected by cli.

    Scenario:
        Given test inputs and fixtures for keep negative rejected by cli
        When test_keep_negative_rejected_by_cli executes the target flow
        Then the expected outcome for keep negative rejected by cli is confirmed.

    Boundaries:
        - Focus: keep negative rejected by cli
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to keep negative rejected by cli
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["rotate-cert", "--keep", "-3"])


def test_keep_non_integer_rejected_by_cli() -> None:
    """
    [Unit] cli: verifies keep non integer rejected by cli.

    Scenario:
        Given test inputs and fixtures for keep non integer rejected by cli
        When test_keep_non_integer_rejected_by_cli executes the target flow
        Then the expected outcome for keep non integer rejected by cli is confirmed.

    Boundaries:
        - Focus: keep non integer rejected by cli
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to keep non integer rejected by cli
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["rotate-cert", "--keep", "lots"])


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


def test_cli_page_type_defaults_to_detail() -> None:
    """
    [Unit] cli: verifies cli page type defaults to detail.

    Scenario:
        Given test inputs and fixtures for cli page type defaults to detail
        When test_cli_page_type_defaults_to_detail executes the target flow
        Then the expected outcome for cli page type defaults to detail is confirmed.

    Boundaries:
        - Focus: cli page type defaults to detail
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to cli page type defaults to detail
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    args = build_parser().parse_args(["detect-schema", "--site", "amazon", "--url", "https://x/1"])
    assert args.page_type == "detail"


def test_detect_schema_rejects_unknown_page_type() -> None:
    """
    [Unit] cli: verifies detect schema rejects unknown page type.

    Scenario:
        Given test inputs and fixtures for detect schema rejects unknown page type
        When test_detect_schema_rejects_unknown_page_type executes the target flow
        Then the expected outcome for detect schema rejects unknown page type is confirmed.

    Boundaries:
        - Focus: detect schema rejects unknown page type
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to detect schema rejects unknown page type
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["detect-schema", "--site", "amazon", "--url", "https://x/1", "--page-type", "api"])


def test_detect_schema_model_defaults_to_none() -> None:
    """
    [Unit] cli: verifies detect schema model defaults to none.

    Scenario:
        Given test inputs and fixtures for detect schema model defaults to none
        When test_detect_schema_model_defaults_to_none executes the target flow
        Then the expected outcome for detect schema model defaults to none is confirmed.

    Boundaries:
        - Focus: detect schema model defaults to none
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to detect schema model defaults to none
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    args = build_parser().parse_args(["detect-schema", "--site", "amazon", "--url", "https://x/1"])
    assert args.model is None


def test_detect_schema_max_input_tokens_defaults_to_40000() -> None:
    """
    [Unit] cli: verifies detect schema max input tokens defaults to 40000.

    Scenario:
        Given test inputs and fixtures for detect schema max input tokens defaults to 40000
        When test_detect_schema_max_input_tokens_defaults_to_40000 executes the target flow
        Then the expected outcome for detect schema max input tokens defaults to 40000 is confirmed.

    Boundaries:
        - Focus: detect schema max input tokens defaults to 40000
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to detect schema max input tokens defaults to 40000
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    args = build_parser().parse_args(["detect-schema", "--site", "amazon", "--url", "https://x/1"])
    assert args.max_input_tokens == 40000


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


def test_html_file_and_url_are_mutually_exclusive() -> None:
    """Scenario: --url and --html-file cannot be combined; one of them is required.

    Boundaries: pure argparse; the mutually exclusive group enforces both halves.
    On failure: a run could mix live and offline sources, or neither, with no clear source.
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["detect-schema", "--site", "amazon", "--url", "https://x/1", "--html-file", "a.html"]
        )
    # Neither given is also an error (the group is required).
    with pytest.raises(SystemExit):
        build_parser().parse_args(["detect-schema", "--site", "amazon"])


def test_detect_schema_html_file_is_repeatable_into_a_list() -> None:
    """
    [Unit] cli: verifies detect schema html file is repeatable into a list.

    Scenario:
        Given test inputs and fixtures for detect schema html file is repeatable into a list
        When test_detect_schema_html_file_is_repeatable_into_a_list executes the target flow
        Then the expected outcome for detect schema html file is repeatable into a list is confirmed.

    Boundaries:
        - Focus: detect schema html file is repeatable into a list
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to detect schema html file is repeatable into a list
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    args = build_parser().parse_args(
        ["detect-schema", "--site", "amazon", "--html-file", "a.html", "--html-file", "b.html"]
    )
    assert args.html_file == ["a.html", "b.html"]
    assert args.url is None


def test_detect_schema_replay_and_record_default_to_none() -> None:
    """
    [Unit] cli: verifies detect schema replay and record default to none.

    Scenario:
        Given test inputs and fixtures for detect schema replay and record default to none
        When test_detect_schema_replay_and_record_default_to_none executes the target flow
        Then the expected outcome for detect schema replay and record default to none is confirmed.

    Boundaries:
        - Focus: detect schema replay and record default to none
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to detect schema replay and record default to none
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    args = build_parser().parse_args(["detect-schema", "--site", "amazon", "--url", "https://x/1"])
    assert args.replay is None
    assert args.record is None


def test_detect_schema_replay_and_record_parse() -> None:
    """
    [Unit] cli: verifies detect schema replay and record parse.

    Scenario:
        Given test inputs and fixtures for detect schema replay and record parse
        When test_detect_schema_replay_and_record_parse executes the target flow
        Then the expected outcome for detect schema replay and record parse is confirmed.

    Boundaries:
        - Focus: detect schema replay and record parse
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to detect schema replay and record parse
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
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


def test_detect_schema_format_defaults_to_text() -> None:
    """
    [Unit] cli: verifies detect schema format defaults to text.

    Scenario:
        Given test inputs and fixtures for detect schema format defaults to text
        When test_detect_schema_format_defaults_to_text executes the target flow
        Then the expected outcome for detect schema format defaults to text is confirmed.

    Boundaries:
        - Focus: detect schema format defaults to text
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to detect schema format defaults to text
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    args = build_parser().parse_args(["detect-schema", "--site", "amazon", "--url", "https://x/1"])
    assert args.format == "text"
    assert args.check_current_only is False


def test_detect_schema_format_json_parses() -> None:
    """
    [Unit] cli: verifies detect schema format json parses.

    Scenario:
        Given test inputs and fixtures for detect schema format json parses
        When test_detect_schema_format_json_parses executes the target flow
        Then the expected outcome for detect schema format json parses is confirmed.

    Boundaries:
        - Focus: detect schema format json parses
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to detect schema format json parses
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    args = build_parser().parse_args(
        ["detect-schema", "--site", "amazon", "--url", "https://x/1", "--format", "json"]
    )
    assert args.format == "json"


def test_detect_schema_rejects_unknown_format() -> None:
    """
    [Unit] cli: verifies detect schema rejects unknown format.

    Scenario:
        Given test inputs and fixtures for detect schema rejects unknown format
        When test_detect_schema_rejects_unknown_format executes the target flow
        Then the expected outcome for detect schema rejects unknown format is confirmed.

    Boundaries:
        - Focus: detect schema rejects unknown format
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to detect schema rejects unknown format
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["detect-schema", "--site", "amazon", "--url", "https://x/1", "--format", "yaml"]
        )


def test_detect_schema_check_current_only_flag_sets_true() -> None:
    """
    [Unit] cli: verifies detect schema check current only flag sets true.

    Scenario:
        Given test inputs and fixtures for detect schema check current only flag sets true
        When test_detect_schema_check_current_only_flag_sets_true executes the target flow
        Then the expected outcome for detect schema check current only flag sets true is confirmed.

    Boundaries:
        - Focus: detect schema check current only flag sets true
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to detect schema check current only flag sets true
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by cli
    """
    args = build_parser().parse_args(
        ["detect-schema", "--site", "amazon", "--html-file", "a.html", "--check-current-only"]
    )
    assert args.check_current_only is True
