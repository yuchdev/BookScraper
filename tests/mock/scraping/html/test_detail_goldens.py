"""Detail-page golden (characterization) tests (task 06.0, subtask 04).

Every non-edge-case fixture in ``tests/fixtures/`` is served through the offline harness
(subtask 03) and fed to the **unmodified** production scraper - ``scrape_book`` for the
Playwright sites (real headless Chromium), ``get_leanpub_book_details`` for the Leanpub
JSON:API - and the result is compared field-by-field against a committed
``<name>.expected.json`` golden.

These goldens are **characterization**: they record what the code does *today*, warts and
all. Where today's output is wrong or questionable (a book that scrapes as ``FAILED``, a
prose field reduced to the neutralization placeholder, etc.) the golden carries a
``"_known_issues"`` list naming the roadmap subtask that will fix it. That key is ignored by
the comparison, so the golden still locks in the current record and a deliberate 03.0/03.1
change surfaces as a reviewable diff.

Regenerate with ``uv run pytest --update-goldens`` (see ``tests/conftest.py``); that rewrites
the ``.expected.json`` files and then *fails* the run so the diff is never silently green.

The two legs are separate functions, each parametrized one id per fixture as ``<site>/<name>``:
``test_detail_golden`` drives the Playwright sites - it carries ``@pytest.mark.chromium``,
shares the one session-scoped browser via ``@pytest.mark.asyncio(loop_scope="session")``, and
skips cleanly when no Chromium is usable; ``test_detail_golden_leanpub`` drives the Leanpub JSON
path through ``httpx_corpus`` only, so it is unmarked and runs offline regardless of Chromium.
"""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

import pytest

from bookscraper.scraping import scrape_details
from bookscraper.scraping.scrape_details import get_leanpub_book_details, scrape_book

from .conftest import FIXTURES_ROOT, _fixture_for_meta, corpus_mapping

# Result normalization is NOT duplicated here: it lives in scripts/capture_fixtures.py as
# ``_normalize_scrape_result`` (the single source of truth, used by --refresh's golden-drift
# check), and this harness imports it so the two cannot drift. The script is a dev tool
# outside the package, loaded by path with importlib - the same pattern as
# tests/mock/scraping/test_capture_fixtures.py and tests/unit/scraping/test_fixture_drift.py.
_CAPTURE_SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "capture_fixtures.py"


def _load_capture_module() -> ModuleType:
    """Import capture_fixtures lazily so tests that skip it don't fail on import."""
    spec = importlib.util.spec_from_file_location("capture_fixtures", _CAPTURE_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


normalize_result = _load_capture_module()._normalize_scrape_result

# A low per-page default timeout keeps the Chromium leg fast: the only unbounded wait in
# scrape_book is the DESCRIPTION text_content() (every other locator sets an explicit timeout),
# and on static local fixtures a present element resolves in <100 ms, so lowering this never
# flips a passing fixture - it only shortens the deterministic timeout the Amazon fixture
# already hits. The recorded golden is identical either way.
DETAIL_TIMEOUT_MS = 2000


# ---------------------------------------------------------------------------
# Fixture discovery
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GoldenCase:
    """One corpus fixture to characterize."""

    kind: str  # "html" (Playwright) or "json" (Leanpub API)
    site: str
    name: str
    url: str
    fixture: Path
    expected: Path


def _discover_cases() -> list[GoldenCase]:
    """Collect every non-edge-case fixture (no leading ``_``) as a ``GoldenCase``."""
    cases: list[GoldenCase] = []
    for kind in ("html", "json"):
        root = FIXTURES_ROOT / kind
        if not root.exists():
            continue
        for meta in sorted(root.rglob("*.meta.json")):
            name = meta.name[: -len(".meta.json")]
            # ``_``-prefixed edge-case fixtures ARE characterized here (subtask 06); only the
            # ``search*`` page fixtures are deferred to subtask 05's own golden tier.
            if name.startswith("search"):
                continue
            fixture = _fixture_for_meta(meta)
            if fixture is None:
                continue
            data = json.loads(meta.read_text(encoding="utf-8"))
            cases.append(
                GoldenCase(
                    kind=kind,
                    site=data["site"],
                    name=name,
                    url=data["url"],
                    fixture=fixture,
                    expected=meta.parent / f"{name}.expected.json",
                )
            )
    return cases


def _params_for(kind: str) -> list[Any]:
    """Parametrize entries for one corpus kind, ids as ``<site>/<name>``."""
    return [pytest.param(case, id=f"{case.site}/{case.name}") for case in _discover_cases() if case.kind == kind]


_HTML_PARAMS = _params_for("html")
_JSON_PARAMS = _params_for("json")


# ---------------------------------------------------------------------------
# Comparison / update helpers (shared with the pytester contract test)
# ---------------------------------------------------------------------------


def diff_fields(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """Field-by-field diff lines, ignoring the golden's ``_known_issues`` annotation key."""
    exp = {k: v for k, v in expected.items() if k != "_known_issues"}
    lines: list[str] = []
    for key in sorted(set(exp) | set(actual)):
        if key not in actual:
            lines.append(f"  {key}: in golden but MISSING from scrape (golden={exp[key]!r})")
        elif key not in exp:
            lines.append(f"  {key}: produced by scrape but ABSENT from golden (scrape={actual[key]!r})")
        elif exp[key] != actual[key]:
            lines.append(f"  {key}: golden={exp[key]!r} != scrape={actual[key]!r}")
    return lines


def write_golden(expected_path: Path, actual: dict[str, Any], *, prior: Optional[dict[str, Any]]) -> None:
    """Rewrite a golden from ``actual``, preserving a hand-curated ``_known_issues`` list."""
    payload = dict(actual)
    if prior and "_known_issues" in prior:
        payload["_known_issues"] = prior["_known_issues"]
    expected_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def compare_or_update(expected_path: Path, actual: dict[str, Any], *, update: bool, label: str) -> None:
    """Either rewrite the golden (then fail), or assert ``actual`` matches it field-by-field."""
    prior: Optional[dict[str, Any]] = None
    if expected_path.exists():
        prior = json.loads(expected_path.read_text(encoding="utf-8"))

    if update:
        write_golden(expected_path, actual, prior=prior)
        pytest.fail(f"goldens updated - review the diff ({label})", pytrace=False)

    assert prior is not None, f"missing golden {expected_path} for {label}; generate it with `pytest --update-goldens`"
    diff = diff_fields(prior, actual)
    if diff:
        pytest.fail(f"golden mismatch for {label}:\n" + "\n".join(diff), pytrace=False)


# ---------------------------------------------------------------------------
# The golden test
# ---------------------------------------------------------------------------


@pytest.mark.chromium
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("case", _HTML_PARAMS)
async def test_detail_golden(
    case: GoldenCase,
    chromium_browser: Any,
    corpus_router: Any,
    update_goldens: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    [Local] detail goldens: verifies detail golden.

    Scenario:
        the unmodified ``scrape_book`` runs a Playwright fixture into its golden.

    Boundaries:
        - every request is served from committed bytes or default-denied (offline); the exact return shape - a full record dict, or a ``(None, "FAILED")`` tuple - is characterized. These share the one session Chromium (``chromium`` marker) and skip cleanly when none is usable.

    On failure, first check:
        - a field drifted (a selector stopped matching, a date/ISBN/hash changed, or a dict/tuple flip would reroute a real book into the failed CSV); the diff names the field.
    """
    label = f"{case.site}/{case.name}"
    browser = corpus_router(chromium_browser, corpus_mapping("html"))
    # Zero the inter-retry backoff (output-neutral) so the deterministically-FAILED
    # Playwright fixtures (packtpub/oreilly, missing BASE_URL) don't each sleep 2-4 s per retry.
    monkeypatch.setattr(scrape_details.random, "uniform", lambda _a, _b: 0)
    original_new_page = browser.new_page

    async def _timed_new_page(*args: Any, **kwargs: Any) -> Any:
        """Open a new page and apply the reduced DETAIL_TIMEOUT_MS timeout."""
        page = await original_new_page(*args, **kwargs)
        page.set_default_timeout(DETAIL_TIMEOUT_MS)
        return page

    browser.new_page = _timed_new_page  # type: ignore[method-assign]
    result = await scrape_book(case.url, browser, case.site, storage_backend=None)

    actual = normalize_result(result)
    compare_or_update(case.expected, actual, update=update_goldens, label=label)


@pytest.mark.parametrize("case", _JSON_PARAMS)
async def test_detail_golden_leanpub(
    case: GoldenCase,
    httpx_corpus: Any,
    update_goldens: bool,
) -> None:
    """
    [Local] detail goldens: verifies detail golden leanpub.

    Scenario:
        the unmodified ``get_leanpub_book_details`` runs a JSON fixture into its golden.

    Boundaries:
        - the Leanpub API response is served from committed bytes via ``httpx_corpus`` (an ``httpx.MockTransport``); no browser is needed, so this runs offline regardless of Chromium. One parametrized id per fixture, kept in ``<site>/<name>`` form.

    On failure, first check:
        - a parsed field drifted (title/authors/slug/book_id/categories/date/hash), which would quietly corrupt the Leanpub half of the corpus and its dedup identity.
    """
    label = f"{case.site}/{case.name}"
    httpx_corpus(corpus_mapping("json"))
    result = await get_leanpub_book_details(case.url)

    actual = normalize_result(result)
    compare_or_update(case.expected, actual, update=update_goldens, label=label)


# ---------------------------------------------------------------------------
# The --update-goldens contract (isolated; never touches the real goldens)
# ---------------------------------------------------------------------------


def test_update_goldens_option_rewrites_and_fails(pytester: pytest.Pytester) -> None:
    """
    [Local] detail goldens: verifies update goldens option rewrites and fails.

    Scenario:
        ``--update-goldens`` rewrites a golden and then fails the run.

    Boundaries:
        - proven in an isolated inline pytest run against a throwaway golden file, so the real ``tests/fixtures/**/*.expected.json`` are never written; asserts both halves of the contract - the file is rewritten AND the run fails with the review message.

    On failure, first check:
        - a regeneration could silently pass (goldens drift into the repo unreviewed) or fail to write (goldens never update) - both defeat the characterization workflow.
    """
    pytester.makeconftest(
        """
        import pytest

        def pytest_addoption(parser):
            parser.addoption("--update-goldens", action="store_true", default=False)

        @pytest.fixture
        def update_goldens(request):
            return bool(request.config.getoption("--update-goldens"))
        """
    )
    # The inline test mirrors compare_or_update's contract exactly (rewrite-then-fail on update,
    # field compare otherwise). It is self-contained rather than importing the repo so the
    # subprocess needs no path wiring; the real compare_or_update is exercised by the 12
    # test_detail_golden cases above.
    pytester.makepyfile(
        """
        import json
        from pathlib import Path

        import pytest

        GOLDEN = Path(__file__).parent / "sample.expected.json"

        def _compare_or_update(path, actual, *, update, label):
            prior = json.loads(path.read_text()) if path.exists() else None
            if update:
                payload = dict(actual)
                if prior and "_known_issues" in prior:
                    payload["_known_issues"] = prior["_known_issues"]
                path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\\n")
                pytest.fail(f"goldens updated - review the diff ({label})", pytrace=False)
            assert prior is not None
            exp = {k: v for k, v in prior.items() if k != "_known_issues"}
            diff = [k for k in set(exp) | set(actual) if exp.get(k) != actual.get(k)]
            if diff:
                pytest.fail(f"golden mismatch for {label}: {diff}", pytrace=False)

        def test_golden(update_goldens):
            _compare_or_update(GOLDEN, {"title": "New Title", "hash": "abc"},
                               update=update_goldens, label="sample")
        """
    )
    golden = pytester.path / "sample.expected.json"
    golden.write_text(
        json.dumps({"title": "Old Title", "hash": "abc", "_known_issues": ["keep me"]}) + "\n",
        encoding="utf-8",
    )

    # With the flag: the golden is rewritten (preserving _known_issues) and the run fails.
    update_run = pytester.runpytest_subprocess("--update-goldens", "-p", "no:cacheprovider")
    update_run.assert_outcomes(failed=1)
    update_run.stdout.fnmatch_lines(["*goldens updated - review the diff (sample)*"])
    rewritten = json.loads(golden.read_text(encoding="utf-8"))
    assert rewritten["title"] == "New Title"  # characterized the current output
    assert rewritten["_known_issues"] == ["keep me"]  # hand-curated annotation survives

    # Without the flag: the now-current golden matches, so the same test passes.
    pass_run = pytester.runpytest_subprocess("-p", "no:cacheprovider")
    pass_run.assert_outcomes(passed=1)
