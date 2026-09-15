"""Downstream impact analysis: which models/exposures a change ripples into."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from prguard.manifests import Manifest


@dataclass(frozen=True)
class ImpactSet:
    """Downstream footprint of a set of changed models."""

    changed_model_ids: frozenset[str] = field(default_factory=frozenset)
    downstream_model_ids: frozenset[str] = field(default_factory=frozenset)
    affected_exposure_ids: frozenset[str] = field(default_factory=frozenset)

    @property
    def total_affected_models(self) -> int:
        return len(self.downstream_model_ids)

    @property
    def total_affected_exposures(self) -> int:
        return len(self.affected_exposure_ids)

    def downstream_model_names(self, manifest: Manifest) -> list[str]:
        return sorted(manifest.model_name(uid) for uid in self.downstream_model_ids)

    def affected_exposure_names(self, manifest: Manifest) -> list[str]:
        return sorted(
            manifest.exposure_label(uid) for uid in self.affected_exposure_ids
        )


def _bfs_downstream(child_map: dict[str, list[str]], start_ids: set[str]) -> set[str]:
    """BFS over child_map, returning every node reachable from start_ids
    (not including the start ids themselves)."""
    visited: set[str] = set()
    queue: deque[str] = deque(start_ids)
    while queue:
        current = queue.popleft()
        for child in child_map.get(current, []):
            if child in visited or child in start_ids:
                continue
            visited.add(child)
            queue.append(child)
    return visited


def downstream_impact(head: Manifest, changed_model_ids: set[str]) -> ImpactSet:
    """Compute the transitive downstream footprint of `changed_model_ids`.

    Uses `child_map` (BFS) instead of hand-building reverse edges, as
    recommended by the spec. Descendants that are `model` nodes are reported
    as downstream models; exposures whose `depends_on.nodes` intersects the
    changed set or its downstream models are reported as affected.
    """
    changed_model_ids = set(changed_model_ids)
    all_nodes = head.nodes

    reachable = _bfs_downstream(head.child_map, changed_model_ids)
    downstream_models = {
        uid
        for uid in reachable
        if all_nodes.get(uid, {}).get("resource_type") == "model"
    }

    blast_radius = changed_model_ids | downstream_models
    affected_exposures = {
        exposure_id
        for exposure_id, exposure in head.exposures.items()
        if set(exposure.get("depends_on", {}).get("nodes", [])) & blast_radius
    }

    return ImpactSet(
        changed_model_ids=frozenset(changed_model_ids),
        downstream_model_ids=frozenset(downstream_models),
        affected_exposure_ids=frozenset(affected_exposures),
    )
