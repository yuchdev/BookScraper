"""Unit tests for bookscraper.runtime.

``runtime_options`` is the single place that resolves the run-wide knobs (headless, concurrency,
navigation timeout, ignore-robots) from parsed CLI arguments, applying the documented
flag -> env -> parameters.py precedence and converting ``--nav-timeout`` seconds to milliseconds.
These tests pin that precedence and conversion, and guard that no command or the schema-detection
engine re-imports the ``HEADLESS_BROWSER`` constant instead of taking the resolved value.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from bookscraper import runtime


def _args(**overrides) -> SimpleNamespace:
    """Build a Namespace with no runtime attributes set unless overridden.

    :param overrides: Attribute values to place on the Namespace.
    :returns: The populated :class:`types.SimpleNamespace`.
    """
    return SimpleNamespace(**overrides)


def test_headless_flag_beats_env_beats_parameter(monkeypatch):
    """
    [Unit] runtime: verifies the --headless flag wins over the env var, which wins over the parameter default.

    Scenario:
        Given the BOOKSCRAPER_HEADLESS env var and the HEADLESS_BROWSER parameter set to conflict with each other
        When runtime_options resolves headless with the flag set, then unset, then with the env var also removed
        Then the explicit flag wins first, the env var wins when the flag is unset, and the parameter default wins when both are absent.

    Boundaries:
        - Pure resolution logic; no argparse, no network
        - monkeypatch isolates the env var and the module-level HEADLESS_BROWSER reference

    On failure, first check:
        - The precedence ordering in runtime._resolve_headless
        - Whether runtime still reads BOOKSCRAPER_HEADLESS and the HEADLESS_BROWSER fallback
    """
    monkeypatch.setenv("BOOKSCRAPER_HEADLESS", "0")
    monkeypatch.setattr(runtime, "HEADLESS_BROWSER", False)

    # Flag beats both env (0 -> False) and parameter (False): explicit --headless wins.
    assert runtime.runtime_options(_args(headless=True)).headless is True

    # Flag unset: env var (1 -> True) beats the parameter default (False).
    monkeypatch.setenv("BOOKSCRAPER_HEADLESS", "1")
    assert runtime.runtime_options(_args(headless=None)).headless is True

    # Flag unset and env var removed: the parameter default (now True) is used.
    monkeypatch.delenv("BOOKSCRAPER_HEADLESS", raising=False)
    monkeypatch.setattr(runtime, "HEADLESS_BROWSER", True)
    assert runtime.runtime_options(_args(headless=None)).headless is True


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "  Yes ", "on"])
def test_env_headless_parsing_accepts_common_spellings(monkeypatch, raw):
    """
    [Unit] runtime: verifies BOOKSCRAPER_HEADLESS accepts common truthy/falsy spellings case-insensitively.

    Scenario:
        Given the HEADLESS_BROWSER parameter forced to the opposite of the env value and BOOKSCRAPER_HEADLESS set to a common spelling
        When runtime_options resolves headless with the flag unset
        Then a truthy spelling resolves True and its falsy counterpart resolves False, overriding the parameter default either way.

    Boundaries:
        - Covers whitespace and mixed case; an unrecognized value is tested separately
        - monkeypatch isolates the env var and HEADLESS_BROWSER

    On failure, first check:
        - The accepted token sets in runtime._parse_bool_env
        - Whether _resolve_headless strips and lowercases the env value
    """
    monkeypatch.setattr(runtime, "HEADLESS_BROWSER", False)
    monkeypatch.setenv("BOOKSCRAPER_HEADLESS", raw)
    assert runtime.runtime_options(_args(headless=None)).headless is True

    falsy = {"1": "0", "true": "false", "TRUE": "FALSE", "  Yes ": " no ", "on": "off"}[raw]
    monkeypatch.setattr(runtime, "HEADLESS_BROWSER", True)
    monkeypatch.setenv("BOOKSCRAPER_HEADLESS", falsy)
    assert runtime.runtime_options(_args(headless=None)).headless is False


def test_unrecognized_env_headless_falls_back_to_parameter(monkeypatch):
    """
    [Unit] runtime: verifies an unparseable BOOKSCRAPER_HEADLESS value is ignored in favour of the parameter default.

    Scenario:
        Given BOOKSCRAPER_HEADLESS set to a value that is neither truthy nor falsy and the flag unset
        When runtime_options resolves headless
        Then the configured HEADLESS_BROWSER default is used rather than guessing from the bad value.

    Boundaries:
        - Pure resolution logic; the warning is emitted through print_log (not asserted here)
        - monkeypatch isolates the env var and HEADLESS_BROWSER

    On failure, first check:
        - That runtime._parse_bool_env returns None for an unknown token
        - That _resolve_headless falls through to HEADLESS_BROWSER on None
    """
    monkeypatch.setattr(runtime, "HEADLESS_BROWSER", True)
    monkeypatch.setenv("BOOKSCRAPER_HEADLESS", "maybe")
    assert runtime.runtime_options(_args(headless=None)).headless is True


def test_concurrency_defaults_when_namespace_omits_it(monkeypatch):
    """
    [Unit] runtime: verifies a Namespace without a concurrency attribute falls back to the module default.

    Scenario:
        Given a Namespace carrying no concurrency attribute (as detect-schema has none)
        When runtime_options resolves the options
        Then concurrency is the DEFAULT_CONCURRENCY fallback and an explicit value is passed through unchanged.

    Boundaries:
        - Pure getattr fallback; no argparse bounds checking here (that lives in cli.py)
        - monkeypatch not needed beyond the module import

    On failure, first check:
        - The getattr fallback for concurrency in runtime_options
        - That DEFAULT_CONCURRENCY still matches the cli.py --concurrency default
    """
    assert runtime.runtime_options(_args()).concurrency == runtime.DEFAULT_CONCURRENCY
    assert runtime.runtime_options(_args(concurrency=16)).concurrency == 16


def test_nav_timeout_converted_to_ms():
    """
    [Unit] runtime: verifies --nav-timeout seconds are converted to whole milliseconds.

    Scenario:
        Given nav_timeout_s values (a fractional override and the omitted default)
        When runtime_options resolves the options
        Then nav_timeout_ms is the seconds value times 1000 rounded to an int, and the omitted case uses the 60 s default.

    Boundaries:
        - Pure arithmetic conversion; no argparse validation (> 0 is enforced in cli.py)
        - Covers a fractional value to prove rounding to whole ms

    On failure, first check:
        - The seconds-to-ms conversion in runtime_options
        - That DEFAULT_NAV_TIMEOUT_S still matches the cli.py --nav-timeout default
    """
    assert runtime.runtime_options(_args(nav_timeout_s=1.5)).nav_timeout_ms == 1500
    assert runtime.runtime_options(_args(nav_timeout_s=30)).nav_timeout_ms == 30000
    assert runtime.runtime_options(_args()).nav_timeout_ms == int(runtime.DEFAULT_NAV_TIMEOUT_S * 1000)


def test_ignore_robots_passthrough():
    """
    [Unit] runtime: verifies ignore_robots is carried through, defaulting to False when absent.

    Scenario:
        Given a Namespace with ignore_robots True, False, and absent
        When runtime_options resolves the options
        Then the resolved ignore_robots mirrors the attribute and defaults to False when it is missing.

    Boundaries:
        - Pure getattr passthrough; no side effects
        - detect-schema has no --ignore-robots, so the absent case matters

    On failure, first check:
        - The getattr fallback for ignore_robots in runtime_options
    """
    assert runtime.runtime_options(_args(ignore_robots=True)).ignore_robots is True
    assert runtime.runtime_options(_args(ignore_robots=False)).ignore_robots is False
    assert runtime.runtime_options(_args()).ignore_robots is False


def test_no_filters_passthrough():
    """
    [Unit] runtime: verifies no_filters is carried through, defaulting to False when absent.

    Scenario:
        Given a Namespace with no_filters True, False, and absent
        When runtime_options resolves the options
        Then the resolved no_filters mirrors the attribute and defaults to False when it is missing.

    Boundaries:
        - Pure getattr passthrough; only the search subcommand defines --no-filters.

    On failure, first check:
        - The getattr fallback for no_filters in runtime_options and the RuntimeOptions field.
    """
    assert runtime.runtime_options(_args(no_filters=True)).no_filters is True
    assert runtime.runtime_options(_args(no_filters=False)).no_filters is False
    assert runtime.runtime_options(_args()).no_filters is False


def test_dry_run_output_dir_and_json_store_resolution():
    """
    [Unit] runtime: verifies dry_run, output_dir and json_store resolve from the Namespace with sane defaults.

    Scenario:
        Given a Namespace omitting the three fields, then one setting all of them for the json backend
        When runtime_options resolves the options
        Then the omitted case yields dry_run False / output_dir "." / json_store None, and the set case
             carries dry_run True, the given output_dir, and json_store as a Path.

    Boundaries:
        - Pure resolution logic; the mongo-combination rejection is a separate test
        - json_store is only ever set alongside the json backend here

    On failure, first check:
        - The getattr fallbacks and the Path conversion in runtime_options / _resolve_json_store
    """
    defaults = runtime.runtime_options(_args())
    assert defaults.dry_run is False
    assert defaults.output_dir == "."
    assert defaults.json_store is None

    resolved = runtime.runtime_options(
        _args(dry_run=True, output_dir="reports", json_store="custom.json", store_backend="json")
    )
    assert resolved.dry_run is True
    assert resolved.output_dir == "reports"
    assert resolved.json_store == Path("custom.json")


def test_json_store_with_mongo_backend_is_usage_error():
    """
    [Unit] runtime: verifies --json-store combined with --store-backend mongo is a usage error (exit 2).

    Scenario:
        Given a Namespace pairing json_store with store_backend "mongo"
        When runtime_options resolves the options
        Then SystemExit(2) is raised; and json_store with the json backend, or an absent json_store
             with mongo, both resolve without error.

    Boundaries:
        - Pure resolution logic; exit code 2 mirrors argparse's usage-error convention
        - The json-backend and json_store-absent cases prove the guard is narrow

    On failure, first check:
        - The store_backend == "mongo" guard in runtime._resolve_json_store
    """
    with pytest.raises(SystemExit) as exc_info:
        runtime.runtime_options(_args(json_store="custom.json", store_backend="mongo"))
    assert exc_info.value.code == 2

    # The narrow guard: json backend + json_store is fine, and mongo without json_store is fine.
    assert runtime.runtime_options(_args(json_store="custom.json", store_backend="json")).json_store == Path(
        "custom.json"
    )
    assert runtime.runtime_options(_args(store_backend="mongo")).json_store is None


def test_commands_do_not_import_headless_browser_constant():
    """
    [Unit] runtime: verifies no command module or the schema-detection engine imports HEADLESS_BROWSER.

    Scenario:
        Given the source of every src/bookscraper/commands module and scraping/schema_detection.py
        When each file is scanned for the HEADLESS_BROWSER token
        Then none mentions it (they take the resolved runtime.RuntimeOptions.headless instead).

    Boundaries:
        - Static text grep over source files; mirrors the subtask's success-criteria grep
        - Only commands/ and schema_detection.py are in scope (parameters.py still defines it)

    On failure, first check:
        - Whether a command re-introduced a direct HEADLESS_BROWSER import
        - That the value is threaded via runtime_options(args).headless instead
    """
    src_root = Path(__file__).resolve().parents[2] / "src" / "bookscraper"
    targets = sorted((src_root / "commands").rglob("*.py"))
    targets.append(src_root / "scraping" / "schema_detection.py")
    offenders = [p.name for p in targets if "HEADLESS_BROWSER" in p.read_text(encoding="utf-8")]
    assert offenders == [], f"HEADLESS_BROWSER must not appear in {offenders}"
