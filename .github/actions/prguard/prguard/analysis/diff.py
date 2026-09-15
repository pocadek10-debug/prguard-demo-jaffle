"""Diffing model nodes between a base and a head dbt manifest."""

from __future__ import annotations

from dataclasses import dataclass, field

from prguard.manifests import Manifest

MODEL_RESOURCE_TYPE = "model"
SECONDARY_RESOURCE_TYPES = ("seed", "snapshot")


@dataclass(frozen=True)
class NodeDiff:
    """Result of diffing one resource-type's nodes between two manifests."""

    added: frozenset[str] = field(default_factory=frozenset)
    removed: frozenset[str] = field(default_factory=frozenset)
    modified: frozenset[str] = field(default_factory=frozenset)
    unchanged: frozenset[str] = field(default_factory=frozenset)

    @property
    def changed(self) -> frozenset[str]:
        """Nodes that are new or whose code changed (added ∪ modified)."""
        return self.added | self.modified


def _checksum(node: dict) -> str | None:
    checksum = node.get("checksum")
    if not isinstance(checksum, dict):
        return None
    return checksum.get("checksum")


def _diff_ids(base_nodes: dict[str, dict], head_nodes: dict[str, dict]) -> NodeDiff:
    base_ids = set(base_nodes)
    head_ids = set(head_nodes)

    added = head_ids - base_ids
    removed = base_ids - head_ids
    common = base_ids & head_ids

    modified = {
        uid
        for uid in common
        if _checksum(base_nodes[uid]) != _checksum(head_nodes[uid])
    }
    unchanged = common - modified

    return NodeDiff(
        added=frozenset(added),
        removed=frozenset(removed),
        modified=frozenset(modified),
        unchanged=frozenset(unchanged),
    )


def diff_nodes(base: Manifest, head: Manifest) -> NodeDiff:
    """Diff `model` nodes between base and head manifests.

    This is the primary diff used for downstream impact / breaking-change
    analysis, mirroring how `dbt build --select state:modified` decides a
    model changed: compare `checksum.checksum` for nodes present in both,
    and treat unique_ids present in only one side as added/removed.
    """
    return _diff_ids(base.models(), head.models())


def diff_secondary_nodes(base: Manifest, head: Manifest) -> NodeDiff:
    """Diff seeds and snapshots, kept as a secondary set per the spec."""
    return _diff_ids(base.seeds_and_snapshots(), head.seeds_and_snapshots())
