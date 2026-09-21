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
