"""Rendering a Report into the Markdown PR comment body."""

from __future__ import annotations

from prguard.analysis.breaking import BreakingChange
from prguard.analysis.report import Report
from prguard.manifests import Manifest

COMMENT_MARKER = "<!-- prguard:comment:v1 -->"

_KIND_LABELS = {
    "removed_model": "removed model",
    "removed_column": "removed column",
    "type_changed_column": "column type change",
    "possible_renamed_column": "possible column rename",
}


def _pct(ratio: float) -> str:
    return f"{ratio * 100:.0f}%"


def summary_line(report: Report) -> str:
    n_breaking = sum(1 for c in report.breaking if c.severity == "breaking")
    n_warnings = (
        sum(1 for c in report.breaking if c.severity == "warning")
        + len(report.missing_tests.models_without_any_test)
        + len(report.missing_tests.key_column_issues)
    )
    if n_breaking == 0 and n_warnings == 0:
        return "no issues"
    if n_breaking == 0:
        return f"{n_warnings} warning{'s' if n_warnings != 1 else ''}"
    return (
        f"{n_breaking} breaking, {n_warnings} warning"
        f"{'s' if n_warnings != 1 else ''}"
    )


def _breaking_line(change: BreakingChange, head: Manifest, base: Manifest) -> str:
    icon = "⛔" if change.severity == "breaking" else "⚠️"
    downstream_models = sorted(
        head.model_name(uid) if uid in head.nodes else base.model_name(uid)
        for uid in change.downstream_model_ids
    )
    exposure_labels = sorted(
        head.exposure_label(uid) for uid in change.affected_exposure_ids
    )
    parts = [f"{icon} {change.detail}"]
    if downstream_models or exposure_labels:
        bits = []
        if downstream_models:
            n = len(downstream_models)
            bits.append(f"**{n} model{'s' if n != 1 else ''}**")
        if exposure_labels:
            n = len(exposure_labels)
            bits.append(f"**{n} exposure{'s' if n != 1 else ''}**")
        names = ", ".join(f"`{n}`" for n in downstream_models[:5])
        if exposure_labels:
            quoted = ", ".join(f'"{n}"' for n in exposure_labels[:3])
            names = f"{names}, {quoted}" if names else quoted
        suffix = f" ({names}{', …' if len(downstream_models) + len(exposure_labels) > 8 else ''})" if names else ""
        parts.append(f"→ affects {', '.join(bits)}{suffix}")
    return " ".join(parts)


def _missing_test_lines(report: Report, head: Manifest, base: Manifest) -> list[str]:
    lines: list[str] = []
    key_issue_by_model = {
        issue.model_id: issue for issue in report.missing_tests.key_column_issues
    }
    all_model_ids = sorted(
        report.missing_tests.models_without_any_test
        | set(key_issue_by_model.keys()),
        key=lambda uid: head.model_name(uid),
    )
    for uid in all_model_ids:
        name = head.model_name(uid)
        is_new = uid in report.diff.added
        no_tests = uid in report.missing_tests.models_without_any_test
        issue = key_issue_by_model.get(uid)
        bits = []
        if no_tests:
            bits.append("has no tests")
        if issue is not None:
            missing = []
            if issue.missing_not_null:
                missing.append("not_null")
            if issue.missing_unique:
                missing.append("unique")
            bits.append(
                f"key column `{issue.column}` has no {'/'.join(missing)}"
            )
        suffix = " (new)" if is_new else ""
        lines.append(f"⚠️ `{name}`{suffix} {'; '.join(bits)}")
    return lines


def render_report(
    report: Report,
    head: Manifest,
    base: Manifest,
    *,
    commit_sha: str = "",
    show_details: bool = True,
) -> str:
    """Render `report` into the full Markdown PR comment body.

    Deterministic: given the same report/manifests/commit_sha, always
    produces the same string (everything iterated over is pre-sorted by the
    analysis layer or sorted again here).
    """
    lines: list[str] = [COMMENT_MARKER, f"## 🛡️ PRGuard — {summary_line(report)}", ""]

    if report.breaking:
        lines.append("**Breaking changes**")
        for change in report.breaking:
            lines.append(f"- {_breaking_line(change, head, base)}")
        lines.append("")

    missing_test_lines = _missing_test_lines(report, head, base)
    if missing_test_lines:
        lines.append("**Missing tests**")
        lines.extend(f"- {line}" for line in missing_test_lines)
        lines.append("")

    lines.append("**Downstream impact**")
    n_changed = len(report.diff.changed)
    n_downstream = report.impact.total_affected_models
    n_exposures = report.impact.total_affected_exposures
    if n_changed == 0:
        lines.append("- no model changes detected")
    else:
        lines.append(
            f"- {n_changed} changed model{'s' if n_changed != 1 else ''} → "
            f"**{n_downstream} downstream model{'s' if n_downstream != 1 else ''}**, "
            f"**{n_exposures} exposure{'s' if n_exposures != 1 else ''}** affected"
        )
    lines.append("")

    lines.append("**Coverage**")
    base_pct = _pct(report.coverage_base.ratio)
    head_pct = _pct(report.coverage_head.ratio)
    delta_pct = report.coverage_delta * 100
    sign = "+" if delta_pct >= 0 else "−"
    lines.append(
        f"- {base_pct} → {head_pct} ({sign}{abs(delta_pct):.0f}%) of models have ≥ 1 test"
    )
    lines.append("")

    if show_details and (
        report.impact.downstream_model_ids or report.impact.affected_exposure_ids
    ):
        lines.append("<details><summary>Full impact list</summary>")
        lines.append("")
        for name in report.impact.downstream_model_names(head):
            lines.append(f"- model `{name}`")
        for name in report.impact.affected_exposure_names(head):
            lines.append(f'- exposure "{name}"')
        lines.append("")
        lines.append("</details>")
        lines.append("")

    footer = f"PRGuard ran on {report.total_models_head} models · metadata-only"
    if commit_sha:
        footer += f" · `{commit_sha[:12]}`"
    lines.append(f"<sub>{footer}</sub>")

    return "\n".join(lines).rstrip() + "\n"
