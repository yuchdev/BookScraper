"""Mock-tier tests for bookscraper.main - argument parsing, logging setup, and
dispatch to the correct command module's run() coroutine.

All externals (parser, logging config, command run() coroutines, asyncio.run) are
mocked; no real parsing, logging, or event loop work happens here.
"""

import inspect
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from bookscraper import main


def _fake_parser(args) -> Mock:
    """A build_parser() stand-in whose parse_args() yields the supplied namespace."""
    parser = Mock()
    parser.parse_args = Mock(return_value=args)
    return parser


@pytest.mark.asyncio
async def test_main_dispatches_to_scrape_urls_run() -> None:
    """
    [Unit] main: verifies main dispatches to scrape urls run.

    Scenario:
        Given test inputs and fixtures for main dispatches to scrape urls run
        When test_main_dispatches_to_scrape_urls_run executes the target flow
        Then the expected outcome for main dispatches to scrape urls run is confirmed.

    Boundaries:
        - Focus: main dispatches to scrape urls run
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to main dispatches to scrape urls run
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by main
    """
    args = SimpleNamespace(command="scrape-urls", log_severity="info")

    with (
        patch.object(main, "build_parser", return_value=_fake_parser(args)),
        patch.object(main, "configure_logging") as mock_configure,
        patch.object(main.scrape_urls, "run", new_callable=AsyncMock) as mock_scrape_run,
        patch.object(main.search, "run", new_callable=AsyncMock) as mock_search_run,
    ):
        await main.main()

    mock_configure.assert_called_once_with(logging.INFO)
    mock_scrape_run.assert_awaited_once_with(args)
    mock_search_run.assert_not_called()


@pytest.mark.asyncio
async def test_main_dispatches_to_search_run() -> None:
    """
    [Unit] main: verifies main dispatches to search run.

    Scenario:
        Given test inputs and fixtures for main dispatches to search run
        When test_main_dispatches_to_search_run executes the target flow
        Then the expected outcome for main dispatches to search run is confirmed.

    Boundaries:
        - Focus: main dispatches to search run
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to main dispatches to search run
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by main
    """
    args = SimpleNamespace(command="search", log_severity="debug")

    with (
        patch.object(main, "build_parser", return_value=_fake_parser(args)),
        patch.object(main, "configure_logging") as mock_configure,
        patch.object(main.scrape_urls, "run", new_callable=AsyncMock) as mock_scrape_run,
        patch.object(main.search, "run", new_callable=AsyncMock) as mock_search_run,
    ):
        await main.main()

    mock_configure.assert_called_once_with(logging.DEBUG)
    mock_search_run.assert_awaited_once_with(args)
    mock_scrape_run.assert_not_called()


@pytest.mark.asyncio
async def test_main_dispatches_to_rotate_cert_run() -> None:
    """
    [Unit] main: verifies main dispatches to rotate cert run.

    Scenario:
        Given test inputs and fixtures for main dispatches to rotate cert run
        When test_main_dispatches_to_rotate_cert_run executes the target flow
        Then the expected outcome for main dispatches to rotate cert run is confirmed.

    Boundaries:
        - Focus: main dispatches to rotate cert run
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to main dispatches to rotate cert run
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by main
    """
    args = SimpleNamespace(command="rotate-cert", log_severity="info")

    with (
        patch.object(main, "build_parser", return_value=_fake_parser(args)),
        patch.object(main, "configure_logging") as mock_configure,
        patch.object(main.scrape_urls, "run", new_callable=AsyncMock) as mock_scrape_run,
        patch.object(main.search, "run", new_callable=AsyncMock) as mock_search_run,
        patch.object(main.rotate_cert, "run", new_callable=AsyncMock) as mock_rotate_run,
    ):
        await main.main()

    mock_configure.assert_called_once_with(logging.INFO)
    mock_rotate_run.assert_awaited_once_with(args)
    mock_scrape_run.assert_not_called()
    mock_search_run.assert_not_called()


@pytest.mark.asyncio
async def test_main_dispatches_to_detect_schema_run() -> None:
    """
    [Unit] main: verifies main dispatches to detect schema run.

    Scenario:
        Given test inputs and fixtures for main dispatches to detect schema run
        When test_main_dispatches_to_detect_schema_run executes the target flow
        Then the expected outcome for main dispatches to detect schema run is confirmed.

    Boundaries:
        - Focus: main dispatches to detect schema run
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to main dispatches to detect schema run
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by main
    """
    args = SimpleNamespace(command="detect-schema", log_severity="info")

    with (
        patch.object(main, "build_parser", return_value=_fake_parser(args)),
        patch.object(main, "configure_logging") as mock_configure,
        patch.object(main.scrape_urls, "run", new_callable=AsyncMock) as mock_scrape_run,
        patch.object(main.search, "run", new_callable=AsyncMock) as mock_search_run,
        patch.object(main.detect_schema, "run", new_callable=AsyncMock) as mock_detect_run,
    ):
        await main.main()

    mock_configure.assert_called_once_with(logging.INFO)
    mock_detect_run.assert_awaited_once_with(args)
    mock_scrape_run.assert_not_called()
    mock_search_run.assert_not_called()


@pytest.mark.parametrize(("command", "module_name"), [("push", "push"), ("pull", "pull")])
@pytest.mark.asyncio
async def test_main_dispatches_push_and_pull(command: str, module_name: str) -> None:
    """
    [Unit] main: verifies main dispatches push and pull.

    Scenario:
        Given test inputs and fixtures for main dispatches push and pull
        When test_main_dispatches_push_and_pull executes the target flow
        Then the expected outcome for main dispatches push and pull is confirmed.

    Boundaries:
        - Focus: main dispatches push and pull
        - Fixtures/params: command, module_name (parametrized, 2 cases)
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to main dispatches push and pull
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by main
    """
    args = SimpleNamespace(command=command, log_severity="warning")
    other = "pull" if module_name == "push" else "push"

    with (
        patch.object(main, "build_parser", return_value=_fake_parser(args)),
        patch.object(main, "configure_logging") as mock_configure,
        patch.object(getattr(main, module_name), "run", new_callable=AsyncMock) as mock_run,
        patch.object(getattr(main, other), "run", new_callable=AsyncMock) as mock_other_run,
        patch.object(main.scrape_urls, "run", new_callable=AsyncMock) as mock_scrape_run,
    ):
        await main.main()

    mock_configure.assert_called_once_with(logging.WARNING)
    mock_run.assert_awaited_once_with(args)
    mock_other_run.assert_not_called()
    mock_scrape_run.assert_not_called()


@pytest.mark.asyncio
async def test_main_unknown_command_calls_parser_error() -> None:
    """The else branch delegates to parser.error() for an unrecognized command."""
    args = SimpleNamespace(command="bogus", log_severity="warning")
    parser = _fake_parser(args)

    with (
        patch.object(main, "build_parser", return_value=parser),
        patch.object(main, "configure_logging") as mock_configure,
        patch.object(main.scrape_urls, "run", new_callable=AsyncMock) as mock_scrape_run,
        patch.object(main.search, "run", new_callable=AsyncMock) as mock_search_run,
    ):
        await main.main()

    mock_configure.assert_called_once_with(logging.WARNING)
    parser.error.assert_called_once()
    assert "bogus" in parser.error.call_args.args[0]
    mock_scrape_run.assert_not_called()
    mock_search_run.assert_not_called()


def test_main_sync_wraps_main_in_asyncio_run() -> None:
    """main_sync() drives the main() coroutine through asyncio.run exactly once.

    main is `async def`, so patch.object auto-promotes it to an AsyncMock: calling it
    synchronously (as main_sync() does, passing the result into asyncio.run) returns a
    fresh coroutine object each time, not a comparable sentinel - assert on the call
    shape (one positional arg, itself a coroutine) rather than object identity.
    """
    with (
        patch.object(main, "main", new_callable=AsyncMock) as mock_main,
        patch.object(main.asyncio, "run") as mock_run,
    ):
        main.main_sync()

    mock_main.assert_called_once_with()
    mock_run.assert_called_once()
    (coro,) = mock_run.call_args.args
    assert inspect.iscoroutine(coro)
    coro.close()  # asyncio.run is mocked out, so nothing else awaits/closes this coroutine
