"""Best-effort, metadata-only breaking-change detection.

Without `catalog.json` (which needs a live warehouse connection) we cannot
see a model's *actual* columns. Everything here is therefore based on
YAML-declared `columns` and dbt model contracts, and is labelled as
best-effort — see the spec, section 2 and section 12.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from prguard.analysis.diff import NodeDiff
from prguard.analysis.impact import ImpactSet, downstream_impact
from prguard.manifests import Manifest

BreakingKind = Literal[
    "removed_model",
    "removed_column",
    "type_changed_column",
    "possible_renamed_column",
]

Severity = Literal["breaking", "warning"]


@dataclass(frozen=True)
class BreakingChange:
    kind: BreakingKind
    model_id: str
    detail: str
    severity: Severity
    downstream_model_ids: frozenset[str] = field(default_factory=frozenset)
    affected_exposure_ids: frozenset[str] = field(default_factory=frozenset)
    best_effort: bool = True

    def sort_key(self) -> tuple:
        return (self.model_id, self.kind, self.detail)


def _is_contract_enforced(node: dict) -> bool:
    return bool(node.get("config", {}).get("contract", {}).get("enforced"))


def _removed_model_changes(base: Manifest, diff: NodeDiff) -> list[BreakingChange]:
    changes: list[BreakingChange] = []
    for uid in sorted(diff.removed):
        impact = downstream_impact(base, {uid})
        if not impact.downstream_model_ids and not impact.affected_exposure_ids:
            continue
        model_name = base.model_name(uid)
        changes.append(
            BreakingChange(
                kind="removed_model",
                model_id=uid,
                detail=f"model `{model_name}` was removed",
                severity="breaking",
                downstream_model_ids=impact.downstream_model_ids,
                affected_exposure_ids=impact.affected_exposure_ids,
            )
        )
    return changes


def _column_changes_for_model(
    uid: str, base_node: dict, head_node: dict, head: Manifest
) -> list[BreakingChange]:
    base_cols: dict[str, dict] = base_node.get("columns", {}) or {}
    head_cols: dict[str, dict] = head_node.get("columns", {}) or {}
    if base_cols == head_cols:
        return []

    removed = [c for c in base_cols if c not in head_cols]
    added = [c for c in head_cols if c not in base_cols]
    common = [c for c in base_cols if c in head_cols]

    changes: list[BreakingChange] = []
    contract_enforced = _is_contract_enforced(base_node) or _is_contract_enforced(
        head_node
    )

    impact = downstream_impact(head, {uid})
    referenced = bool(impact.downstream_model_ids or impact.affected_exposure_ids)
    model_name = head.model_name(uid) if uid in head.nodes else base_node.get(
        "name", uid
    )

    # Pair up equal counts of removed/added columns as "possible renames";
    # anything left over is reported as a straight removal.
    pair_count = min(len(removed), len(added))
    for old_col, new_col in zip(removed[:pair_count], added[:pair_count]):
        changes.append(
            BreakingChange(
                kind="possible_renamed_column",
                model_id=uid,
                detail=(
                    f"`{model_name}.{old_col}` disappeared while "
                    f"`{new_col}` appeared — possible rename (unverified)"
                ),
                severity="warning",
                downstream_model_ids=impact.downstream_model_ids,
                affected_exposure_ids=impact.affected_exposure_ids,
            )
        )

    for col in removed[pair_count:]:
        changes.append(
            BreakingChange(
                kind="removed_column",
                model_id=uid,
                detail=f"`{model_name}.{col}` was removed",
                severity="breaking" if referenced else "warning",
                downstream_model_ids=impact.downstream_model_ids,
                affected_exposure_ids=impact.affected_exposure_ids,
            )
        )

    if contract_enforced:
        for col in common:
            base_type = base_cols[col].get("data_type")
            head_type = head_cols[col].get("data_type")
            if base_type and head_type and base_type != head_type:
                changes.append(
                    BreakingChange(
                        kind="type_changed_column",
                        model_id=uid,
                        detail=(
                            f"`{model_name}.{col}` type changed from "
                            f"`{base_type}` to `{head_type}` (contract-enforced)"
                        ),
                        severity="breaking" if referenced else "warning",
                        downstream_model_ids=impact.downstream_model_ids,
                        affected_exposure_ids=impact.affected_exposure_ids,
                    )
                )

    return changes


def breaking_changes(base: Manifest, head: Manifest, diff: NodeDiff) -> list[BreakingChange]:
    """Best-effort breaking-change detection: removed models, and
    removed/renamed/retyped columns on models present in both manifests.

    Column-level checks run over every model common to base and head (not
    just those in `diff.modified`), because YAML column docs can change
    independently of the SQL file's checksum.
    """
    changes: list[BreakingChange] = []
    changes.extend(_removed_model_changes(base, diff))

    base_models = base.models()
    head_models = head.models()
    common_ids = set(base_models) & set(head_models)

    for uid in sorted(common_ids):
        changes.extend(
            _column_changes_for_model(uid, base_models[uid], head_models[uid], head)
        )

    changes.sort(key=lambda c: c.sort_key())
    return changes
