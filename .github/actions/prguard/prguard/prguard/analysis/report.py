"""Assembling the full PRGuard report from base + head manifests."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Literal

from prguard.analysis.breaking import BreakingChange, breaking_changes
from prguard.analysis.coverage import (
    CoverageReport,
    MissingTestFindings,
    missing_test_findings,
    test_coverage,
)
from prguard.analysis.diff import NodeDiff, diff_nodes
from prguard.analysis.impact import ImpactSet, downstream_impact
from prguard.config import PRGuardConfig
from prguard.manifests import Manifest

Severity = Literal["ok", "warnings", "breaking"]


@dataclass(frozen=True)
class Report:
    severity: Severity
    diff: NodeDiff
    impact: ImpactSet
    coverage_base: CoverageReport
    coverage_head: CoverageReport
    missing_tests: MissingTestFindings
    breaking: tuple[BreakingChange, ...]
    total_models_head: int
    ignored_model_ids: frozenset[str] = field(default_factory=frozenset)

    @property
    def coverage_delta(self) -> float:
        return self.coverage_head.ratio - self.coverage_base.ratio

    @property
    def has_breaking(self) -> bool:
        return any(c.severity == "breaking" for c in self.breaking)


def _ignored_model_ids(manifest: Manifest, config: PRGuardConfig) -> set[str]:
    if not config.ignore_models and not config.ignore_paths:
        return set()
    ignored: set[str] = set()
    for uid, node in manifest.models().items():
        name = node.get("name", "")
        path = node.get("original_file_path") or node.get("path") or ""
        if any(fnmatch.fnmatch(name, pat) for pat in config.ignore_models):
            ignored.add(uid)
            continue
        if any(fnmatch.fnmatch(path, pat) for pat in config.ignore_paths):
            ignored.add(uid)
    return ignored


def build_report(base: Manifest, head: Manifest, config: PRGuardConfig) -> Report:
    """Run the full analysis pipeline and assemble a deterministic Report.

    All sets are frozen and every rendering step sorts its inputs, so the
    same (base, head, config) always produces the same output — required for
    the idempotent PR comment not to churn across identical re-runs.
    """
    ignored = _ignored_model_ids(head, config) | _ignored_model_ids(base, config)

    raw_diff = diff_nodes(base, head)
    diff = NodeDiff(
        added=frozenset(raw_diff.added - ignored),
        removed=frozenset(raw_diff.removed - ignored),
        modified=frozenset(raw_diff.modified - ignored),
        unchanged=frozenset(raw_diff.unchanged - ignored),
    )

    changed_ids = diff.changed
    impact = downstream_impact(head, changed_ids)
    coverage_head = test_coverage(head)
    coverage_base = test_coverage(base)
    findings = missing_test_findings(head, changed_ids)
    breaking = breaking_changes(base, head, diff)
    # Column/removed-model findings should also respect ignore-list.
    breaking = [c for c in breaking if c.model_id not in ignored]

    severity: Severity = "ok"
    if any(c.severity == "breaking" for c in breaking):
        severity = "breaking"
    elif (
        breaking
        or findings.models_without_any_test
        or findings.key_column_issues
        or coverage_head.ratio < config.coverage_warn_below
    ):
        severity = "warnings"

    return Report(
        severity=severity,
        diff=diff,
        impact=impact,
        coverage_base=coverage_base,
        coverage_head=coverage_head,
        missing_tests=findings,
        breaking=tuple(breaking),
        total_models_head=len(head.models()),
        ignored_model_ids=frozenset(ignored),
    )
