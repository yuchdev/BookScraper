"""Unit tests for bookscraper.cli.build_parser().

The parser is the single translation layer between CLI flags and the attribute
names the rest of the app reads (main.py -> commands/*.py). A drifted dest= or a
silently-dropped required flag reroutes a whole run, so every dest and every
required/choice/default is asserted explicitly here.
"""

import pytest

from bookscraper.cli import build_parser


def test_scrape_urls_parses_full_valid_arg_set() -> None:
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
    args = build_parser().parse_args(["scrape-urls", "--input-file", "x.csv", "--store-backend", "json"])
    assert args.input_file == "x.csv"
    assert args.store_backend == "json"


def test_search_parses_full_valid_arg_set() -> None:
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
    with pytest.raises(SystemExit):
        build_parser().parse_args(["scrape-urls", "-f", "content/urls.csv"])


def test_search_requires_store_backend() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["search"])


def test_scrape_urls_requires_input_file() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["scrape-urls", "--store-backend", "mongo"])


def test_invalid_store_backend_choice_rejected() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["scrape-urls", "-f", "x.csv", "--store-backend", "sqlite"])


def test_invalid_log_severity_choice_rejected() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["search", "--store-backend", "json", "--log-severity", "trace"])


def test_search_max_search_pages_defaults_to_three() -> None:
    args = build_parser().parse_args(["search", "--store-backend", "json"])
    assert args.max_search_pages == 3


def test_search_max_search_pages_type_is_int() -> None:
    args = build_parser().parse_args(["search", "--store-backend", "json", "--max-search-pages", "7"])
    assert isinstance(args.max_search_pages, int)


def test_log_severity_defaults_to_info_on_both_subcommands() -> None:
    scrape_args = build_parser().parse_args(["scrape-urls", "-f", "x.csv", "--store-backend", "mongo"])
    search_args = build_parser().parse_args(["search", "--store-backend", "mongo"])
    assert scrape_args.log_severity == "info"
    assert search_args.log_severity == "info"


def test_missing_subcommand_rejected() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_rotate_cert_parses_full_valid_arg_set() -> None:
    args = build_parser().parse_args(
        ["rotate-cert", "--months", "12", "--output", "/tmp/cert.pem", "--log-severity", "error"]
    )
    assert args.command == "rotate-cert"
    assert args.months == 12
    assert isinstance(args.months, int)
    assert args.output == "/tmp/cert.pem"
    assert args.log_severity == "error"


def test_rotate_cert_needs_no_arguments_and_defaults_defer_to_env() -> None:
    args = build_parser().parse_args(["rotate-cert"])
    assert args.months is None
    assert args.output is None
    assert args.log_severity == "info"


def test_rotate_cert_rejects_non_integer_months() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["rotate-cert", "--months", "soon"])


def test_rotate_cert_check_flags() -> None:
    args = build_parser().parse_args(["rotate-cert", "--check", "--warn-days", "14"])
    assert args.check is True
    assert args.warn_days == 14
    assert isinstance(args.warn_days, int)


def test_rotate_cert_check_defaults() -> None:
    args = build_parser().parse_args(["rotate-cert"])
    assert args.check is False
    assert args.warn_days == 30


def test_rotate_cert_warn_days_rejects_non_integer() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["rotate-cert", "--check", "--warn-days", "soon"])


def test_rotate_cert_verify_flags() -> None:
    args = build_parser().parse_args(["rotate-cert", "--no-verify", "--verify-attempts", "8"])
    assert args.no_verify is True
    assert args.verify_attempts == 8
    assert isinstance(args.verify_attempts, int)


def test_rotate_cert_verify_defaults() -> None:
    args = build_parser().parse_args(["rotate-cert"])
    assert args.no_verify is False
    assert args.verify_attempts == 5


def test_rotate_cert_verify_attempts_rejects_non_integer() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["rotate-cert", "--verify-attempts", "many"])


def test_rotate_cert_prune_defaults() -> None:
    args = build_parser().parse_args(["rotate-cert"])
    assert args.keep == 2
    assert isinstance(args.keep, int)
    assert args.no_prune is False


def test_rotate_cert_keep_and_no_prune_flags() -> None:
    args = build_parser().parse_args(["rotate-cert", "--keep", "5", "--no-prune"])
    assert args.keep == 5
    assert args.no_prune is True


def test_keep_zero_rejected_by_cli() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["rotate-cert", "--keep", "0"])


def test_keep_negative_rejected_by_cli() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["rotate-cert", "--keep", "-3"])


def test_keep_non_integer_rejected_by_cli() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["rotate-cert", "--keep", "lots"])


# --------------------------------------------------------------------------- #
# detect-schema subparser
# --------------------------------------------------------------------------- #
def test_detect_schema_parses_full_valid_arg_set() -> None:
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
    args = build_parser().parse_args(["detect-schema", "--site", "leanpub", "--url", "https://leanpub.com/a"])
    assert args.url == ["https://leanpub.com/a"]


def test_detect_schema_requires_site() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["detect-schema", "--url", "https://x/1"])


def test_detect_schema_requires_url() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["detect-schema", "--site", "amazon"])


def test_detect_schema_rejects_unknown_site() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["detect-schema", "--site", "goodreads", "--url", "https://x/1"])


def test_detect_schema_output_defaults_to_none() -> None:
    args = build_parser().parse_args(["detect-schema", "--site", "oreilly", "--url", "https://x/1"])
    assert args.output is None


def test_detect_schema_log_severity_defaults_to_info() -> None:
    args = build_parser().parse_args(["detect-schema", "--site", "packtpub", "--url", "https://x/1"])
    assert args.log_severity == "info"
