"""Mock tests for bookscraper.commands.staging_output - step 1's legacy hint, save, and summary (02.0/04-05).

Real staging files under tmp_path (also the working directory, so the legacy repo-root
books.json check sees tmp_path, never the repository); print_log is captured.
"""

from unittest.mock import patch

import pytest

from bookscraper.backends.local.staging import AppendSummary, load_staging
from bookscraper.commands import staging_output
from bookscraper.commands.staging_output import (
    print_step_one_summary,
    save_to_staging,
    warn_about_legacy_store,
)


@pytest.fixture
def staging_file(isolated_staging_file):
    return isolated_staging_file


def _printed(log) -> str:
    return "\n".join(c.args[0] for c in log.call_args_list)


def test_legacy_store_hint_printed_once_conditions_met(tmp_path, staging_file) -> None:
    """
    [Unit] staging output: verifies legacy store hint printed once conditions met.

    Scenario:
        Given test inputs and fixtures for legacy store hint printed once conditions met
        When test_legacy_store_hint_printed_once_conditions_met executes the target flow
        Then the expected outcome for legacy store hint printed once conditions met is confirmed.

    Boundaries:
        - Focus: legacy store hint printed once conditions met
        - Fixtures/params: tmp_path, staging_file
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to legacy store hint printed once conditions met
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging output
    """
    (tmp_path / "books.json").write_text("[]", encoding="utf-8")
    with patch.object(staging_output, "print_log") as log:
        warn_about_legacy_store(staging_file)
    assert "mv books.json staging/books.json" in _printed(log).replace(str(tmp_path) + "/", "")
    assert (tmp_path / "books.json").exists()  # never moved automatically


@pytest.mark.parametrize("scenario", ["no-legacy", "staging-exists", "staging-is-legacy"])
def test_legacy_store_hint_suppressed(tmp_path, staging_file, scenario) -> None:
    """
    [Unit] staging output: verifies legacy store hint suppressed.

    Scenario:
        Given test inputs and fixtures for legacy store hint suppressed
        When test_legacy_store_hint_suppressed executes the target flow
        Then the expected outcome for legacy store hint suppressed is confirmed.

    Boundaries:
        - Focus: legacy store hint suppressed
        - Fixtures/params: tmp_path, staging_file, scenario (parametrized, 3 cases)
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to legacy store hint suppressed
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging output
    """
    if scenario != "no-legacy":
        (tmp_path / "books.json").write_text("[]", encoding="utf-8")
    target = staging_file
    if scenario == "staging-exists":
        staging_file.parent.mkdir(parents=True)
        staging_file.write_text("[]", encoding="utf-8")
    if scenario == "staging-is-legacy":
        target = tmp_path / "books.json"
        target = target.relative_to(tmp_path)
    with patch.object(staging_output, "print_log") as log:
        warn_about_legacy_store(target)
    log.assert_not_called()


def test_save_appends_with_source(staging_file) -> None:
    """
    [Unit] staging output: verifies save appends with source.

    Scenario:
        Given test inputs and fixtures for save appends with source
        When test_save_appends_with_source executes the target flow
        Then the expected outcome for save appends with source is confirmed.

    Boundaries:
        - Focus: save appends with source
        - Fixtures/params: staging_file
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to save appends with source
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging output
    """
    summary = save_to_staging(staging_file, [{"title": "A", "authors": [], "hash": "h"}], source="search")
    assert summary.appended == 1
    assert load_staging(staging_file, missing_ok=False)[0]["_staging"]["source"] == "search"


def test_append_to_corrupted_file_writes_unsaved_dump(staging_file) -> None:
    """Mock: if the staging file was corrupted mid-run, scraped results go to a sibling dump, not the void.

    Scenario: the staging file holds invalid JSON when the run tries to append.
    Boundaries: real files under tmp_path; the process exits 1.
    On failure: a scrape run either destroys the (corrupted) review file or loses its own results.
    """
    staging_file.parent.mkdir(parents=True)
    staging_file.write_text("[{broken", encoding="utf-8")
    with patch.object(staging_output, "print_log") as log, pytest.raises(SystemExit) as excinfo:
        save_to_staging(staging_file, [{"title": "A", "hash": "h"}], source="scrape-urls")

    assert excinfo.value.code == 1
    assert staging_file.read_text(encoding="utf-8") == "[{broken"
    dumps = list(staging_file.parent.glob("books.unsaved-*.json"))
    assert len(dumps) == 1 and load_staging(dumps[0], missing_ok=False)[0]["title"] == "A"
    assert "nothing was lost" in _printed(log)


def test_corrupted_file_with_no_results_exits_without_dump(staging_file) -> None:
    """
    [Unit] staging output: verifies corrupted file with no results exits without dump.

    Scenario:
        Given test inputs and fixtures for corrupted file with no results exits without dump
        When test_corrupted_file_with_no_results_exits_without_dump executes the target flow
        Then the expected outcome for corrupted file with no results exits without dump is confirmed.

    Boundaries:
        - Focus: corrupted file with no results exits without dump
        - Fixtures/params: staging_file
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to corrupted file with no results exits without dump
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging output
    """
    staging_file.parent.mkdir(parents=True)
    staging_file.write_text("[{broken", encoding="utf-8")
    with pytest.raises(SystemExit):
        save_to_staging(staging_file, [], source="scrape-urls")
    assert not list(staging_file.parent.glob("*.unsaved-*"))


def test_dump_failure_reported(staging_file) -> None:
    """
    [Unit] staging output: verifies dump failure reported.

    Scenario:
        Given test inputs and fixtures for dump failure reported
        When test_dump_failure_reported executes the target flow
        Then the expected outcome for dump failure reported is confirmed.

    Boundaries:
        - Focus: dump failure reported
        - Fixtures/params: staging_file
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to dump failure reported
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging output
    """
    staging_file.parent.mkdir(parents=True)
    staging_file.write_text("[{broken", encoding="utf-8")
    with (
        patch.object(staging_output, "dump_unsaved", side_effect=OSError("disk full")),
        patch.object(staging_output, "print_log") as log,
        pytest.raises(SystemExit),
    ):
        save_to_staging(staging_file, [{"title": "A", "hash": "h"}], source="scrape-urls")
    assert "could not save scraped results anywhere" in _printed(log)


def test_summary_reports_counts_and_next_step(staging_file) -> None:
    """
    [Unit] staging output: verifies summary reports counts and next step.

    Scenario:
        Given test inputs and fixtures for summary reports counts and next step
        When test_summary_reports_counts_and_next_step executes the target flow
        Then the expected outcome for summary reports counts and next step is confirmed.

    Boundaries:
        - Focus: summary reports counts and next step
        - Fixtures/params: staging_file
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to summary reports counts and next step
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging output
    """
    summary = AppendSummary(appended=3, duplicates=1, total_in_file=10, pending_in_file=4)
    with patch.object(staging_output, "print_log") as log:
        print_step_one_summary(staging_file, summary, skipped_as_known=2)
    text = _printed(log)
    assert "3 new book(s)" in text and "(skipped at save): 1" in text and "already known: 2" in text
    assert "10 document(s), 4 pending review" in text
    assert f"bookscraper push --staging-file {staging_file} --dry-run" in text


def test_no_database_wording_in_step_one_output(staging_file) -> None:
    """
    [Unit] staging output: verifies no database wording in step one output.

    Scenario:
        Given test inputs and fixtures for no database wording in step one output
        When test_no_database_wording_in_step_one_output executes the target flow
        Then the expected outcome for no database wording in step one output is confirmed.

    Boundaries:
        - Focus: no database wording in step one output
        - Fixtures/params: staging_file
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to no database wording in step one output
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by staging output
    """
    with patch.object(staging_output, "print_log") as log:
        print_step_one_summary(staging_file, AppendSummary(0, 0, 0, 0), skipped_as_known=0)
    assert "saved to database" not in _printed(log).lower()
