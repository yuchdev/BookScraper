"""
The pure half of `bookscraper push`: classify every staging document before anything is written.

`push --dry-run` runs exactly this code and stops, so the preview can never disagree with a real
run. No I/O happens here - the caller hands in what it fetched from MongoDB (push_writer.fetch_existing).
See ADR 0003 for the rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from ..local.staging import (
    RESERVED_ID_KEY,
    STATUS_APPROVED,
    STATUS_REJECTED,
    PreparedDocument,
    hash_inputs_equal,
)

Action = Literal["insert", "duplicate", "update", "unchanged", "skipped_rejected", "skipped_unapproved"]
ACTIONS: tuple[Action, ...] = (
    "insert",
    "update",
    "duplicate",
    "unchanged",
    "skipped_rejected",
    "skipped_unapproved",
)


@dataclass
class PlannedItem:
    index: int
    action: Action
    title: str
    hash: str
    hash_changed: bool
    object_id: Optional[str]
    changed_fields: list[str] = field(default_factory=list)


@dataclass
class PushPlan:
    items: list[PlannedItem]
    errors: list[str]

    def count(self, action: Action) -> int:
        return sum(1 for item in self.items if item.action == action)

    @property
    def counts(self) -> dict[str, int]:
        return {action: self.count(action) for action in ACTIONS}

    @property
    def requires_allow_updates(self) -> bool:
        return self.count("update") > 0

    @property
    def has_writes(self) -> bool:
        return self.count("insert") + self.count("update") > 0


def changed_fields(staged: dict, live: dict) -> list[str]:
    """Top-level fields that differ (added, removed, or changed), sorted."""
    keys = set(staged) | set(live)
    return sorted(
        k for k in keys if k != RESERVED_ID_KEY and (k not in staged or k not in live or staged[k] != live[k])
    )


def build_push_plan(
    prepared_docs: list[PreparedDocument],
    *,
    existing_hashes: dict[str, str],
    existing_by_id: dict[str, dict],
    approved_only: bool = False,
) -> PushPlan:
    """
    Classifies each prepared document.

    Args:
        prepared_docs: Output of staging.prepare_for_push, in file order.
        existing_hashes: hash -> `_id` hex string, for every candidate hash that exists in MongoDB.
        existing_by_id: `_id` hex string -> live document in staging-file form (export.normalize_document),
            for every `_id` referenced by the file that exists in MongoDB.
        approved_only: push only documents whose `_staging.status` is "approved".

    Mutates a pulled document's PreparedDocument to keep its stored hash when none of its hash
    inputs were edited, so documents hashed under an older rule never become spurious updates.
    """
    items: list[PlannedItem] = []
    errors: list[str] = []
    hashes_in_file: dict[str, int] = {}

    for prepared in prepared_docs:
        live = existing_by_id.get(prepared.object_id) if prepared.object_id else None
        if live is not None and hash_inputs_equal(prepared.document, live) and isinstance(live.get("hash"), str):
            prepared.document["hash"] = live["hash"]
            prepared.hash = live["hash"]
            prepared.hash_changed = prepared.previous_hash is not None and prepared.previous_hash != prepared.hash

        def item(action: Action, fields: Optional[list[str]] = None, p: PreparedDocument = prepared) -> PlannedItem:
            return PlannedItem(p.index, action, p.title, p.hash, p.hash_changed, p.object_id, fields or [])

        if prepared.status == STATUS_REJECTED:
            items.append(item("skipped_rejected"))
            continue
        if approved_only and prepared.status != STATUS_APPROVED:
            items.append(item("skipped_unapproved"))
            continue

        if prepared.object_id is None:
            if prepared.hash in existing_hashes or prepared.hash in hashes_in_file:
                items.append(item("duplicate"))
            else:
                items.append(item("insert"))
                hashes_in_file[prepared.hash] = prepared.index
            continue

        if live is None:
            errors.append(
                f"document #{prepared.index} ('{prepared.title}'): _id {prepared.object_id} does not exist in "
                "MongoDB (deleted there, or the _id was edited)."
            )
            continue

        owner = existing_hashes.get(prepared.hash)
        if owner is not None and owner != prepared.object_id:
            errors.append(
                f"document #{prepared.index} ('{prepared.title}'): its edited title/authors/year now hash the same as "
                f"existing document {owner}; pushing it would create a duplicate."
            )
            continue
        if prepared.hash in hashes_in_file:
            errors.append(
                f"document #{prepared.index} ('{prepared.title}'): hashes the same as document "
                f"#{hashes_in_file[prepared.hash]} earlier in this file."
            )
            continue
        hashes_in_file[prepared.hash] = prepared.index

        diff = changed_fields(prepared.document, live)
        items.append(item("update", diff) if diff else item("unchanged"))

    return PushPlan(items=items, errors=errors)


def format_plan(plan: PushPlan) -> str:
    """Human-readable plan: counts, then one line per document that isn't `unchanged`."""
    counts = plan.counts
    lines = [
        "Push plan: "
        + ", ".join(f"{counts[a]} {a.replace('_', ' ')}" for a in ACTIONS)
        + f" ({len(plan.items)} documents)."
    ]
    for planned in plan.items:
        if planned.action == "unchanged":
            continue
        detail = f"  #{planned.index:<4} {planned.action:<18} {planned.title or '<no title>'}"
        if planned.object_id:
            detail += f"  (_id {planned.object_id})"
        if planned.changed_fields:
            detail += f"  [changed: {', '.join(planned.changed_fields)}]"
        if planned.hash_changed:
            detail += "  (hash changed)"
        lines.append(detail)
    return "\n".join(lines)
