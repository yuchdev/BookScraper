"""Reference specimen for /document-tests — the docstring format, one per class.

This is a *teaching example*, not a collected test (it lives under `.claude/`,
outside `tests/`, so pytest never runs it). Each function below shows two things
at once:

1. the structural signal `scripts/document_tests.py` keys on to classify it, and
2. the exact docstring the codemod emits and that
   [../references/classification-guide.md](../references/classification-guide.md)
   describes.

Docstring contract (enforced by the codemod's title regex
``^\\[(Unit|Local|Integration|E2E)\\] .+: verifies .+\\.$``):

    [<Class>] <short name>: verifies <one sentence ending in a period>.

    Scenario: <what is set up and exercised>
    Boundaries: <what is real vs mocked; scope of the test>
    On-failure-first-check: <where to look first when this fails>

The imports point at ``shopfront``, a deliberately fictional order-management
package, so nothing here looks like it should resolve against your own code.
Read the *shape* — a real test importing the module under test, asserting on real
behaviour — and copy the docstrings, not the package name.

Note on classification: the codemod decides by **directory first, then body
markers**. This file is in an unrecognised directory, so the codemod would tag
each case `ambiguous` and fall back to the body signal — exactly the situation
where a human uses the semantic definitions in the classification guide. Note
that in an unrecognised directory a `CliRunner` body does *not* promote a test to
[E2E]; only the mock markers are consulted. The body of each function is written
so the *intended* class is unambiguous to a human reader.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

# noinspection unresolved-references
from shopfront.cli.commands import app

# noinspection unresolved-references
from shopfront.orders.models import Order

# noinspection unresolved-references
from shopfront.orders.pricing import DiscountTier, resolve_carrier

# noinspection unresolved-references
from shopfront.orders.store.filesystem import FilesystemOrderStore
from typer.testing import CliRunner


class StubPaymentBackend:
    """Minimal stand-in for the real payment backend: always authorises."""

    def authorize(self, order: Order) -> str:
        return "confirmed"


# --------------------------------------------------------------------------- #
# [Unit] — pure logic, no mocks, no I/O. Signal: no mock/runner markers.
# --------------------------------------------------------------------------- #
def test_discount_tier_below_threshold_is_none() -> None:
    """[Unit] discount tier low: verifies a subtotal under 50.00 maps to NONE.

    Scenario: Call DiscountTier.from_subtotal(49.99) with a plain float, no mocks or I/O.
    Boundaries: Pure function; exercises only the <50.00 branch of the tier ladder.
    On-failure-first-check: If this fails, inspect the 50.00 / 100.00 cutoffs in
        DiscountTier.from_subtotal.
    """
    assert DiscountTier.from_subtotal(49.99) is DiscountTier.NONE


# --------------------------------------------------------------------------- #
# [Local] — unit-scoped, but a collaborator is patched. Signal: mock./monkeypatch.
# --------------------------------------------------------------------------- #
def test_carrier_resolution_uses_lookup_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """[Local] carrier resolution: verifies resolve_carrier delegates to its lookup table.

    Scenario: monkeypatch the module's _CARRIER_MAP to a controlled entry, then resolve a zone.
    Boundaries: Only the one function under test runs for real; the table it reads is a mock.
    On-failure-first-check: If this fails, check that resolve_carrier reads _CARRIER_MAP
        rather than hard-coding the mapping.
    """
    monkeypatch.setattr(
        "shopfront.orders.pricing._CARRIER_MAP",
        {"EU-WEST": "eu-express"},
    )
    assert resolve_carrier("EU-WEST") == "eu-express"
    # An unmapped zone still falls back to "standard" through the patched table.
    assert resolve_carrier("ZZ-UNKNOWN") == "standard"


# --------------------------------------------------------------------------- #
# [Integration] — two+ real components wired together, external edge excluded.
# Signal: real collaborators, no mock markers, no e2e runner.
# --------------------------------------------------------------------------- #
def test_order_store_round_trip(tmp_path: Path) -> None:
    """[Integration] store round-trip: verifies a saved order reloads by order_id.

    Scenario: Persist an order to a real filesystem store rooted at tmp_path,
        then load it back through the store's find_by_id.
    Boundaries: Real store + real serialisation wired together; only the on-disk
        location is a test fixture (tmp_path). No payment backend, no network.
    On-failure-first-check: If this fails, compare the serialised order_id written
        to disk against the value find_by_id queries with.
    """
    store = FilesystemOrderStore(root=tmp_path)
    order = Order(order_id="abc123", subtotal=120.00, zone="EU-WEST")

    store.save(order)
    reloaded = store.find_by_id("abc123")

    assert reloaded.order_id == "abc123"
    assert reloaded.subtotal == pytest.approx(120.00)
    assert reloaded.zone == "EU-WEST"


# --------------------------------------------------------------------------- #
# [E2E] — a full user entry point driven end to end. Signal: CliRunner/TestClient.
# --------------------------------------------------------------------------- #
def test_process_order_cli_reports_status(tmp_path: Path) -> None:
    """[E2E] process-order CLI: verifies `orders process` prints the resolved order status.

    Scenario: Invoke the Typer app through CliRunner with a fixture order file and a
        stubbed payment backend, then assert on the rendered CLI output.
    Boundaries: The whole CLI entry point runs (parsing → processing → view); only
        the external payment backend is replaced at the process edge.
    On-failure-first-check: If this fails, run the same command manually and compare
        the order_status line in the output to the fixture's expected status.
    """
    order_file = tmp_path / "order.json"
    order_file.write_text(
        json.dumps({"order_id": "abc123", "subtotal": 120.00, "zone": "EU-WEST"}),
        encoding="utf-8",
    )

    with mock.patch(
        "shopfront.backends.payment.factory.build_payment_backend",
        return_value=StubPaymentBackend(),
    ):
        result = CliRunner().invoke(app, ["process", "--order", str(order_file)])

    assert result.exit_code == 0
    assert "confirmed" in result.stdout
