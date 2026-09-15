"""Test-coverage analysis: which models have tests, and which are missing
the basics on their key column."""

from __future__ import annotations

from dataclasses import dataclass, field

from prguard.manifests import Manifest

KEY_COLUMN_TEST_NAMES = ("not_null", "unique")


@dataclass(frozen=True)
class KeyColumnIssue:
    model_id: str
    column: str
    missing_not_null: bool
    missing_unique: bool


@dataclass(frozen=True)
class CoverageReport:
    total_models: int
    models_with_tests: frozenset[str]
    models_without_tests: frozenset[str]

    @property
    def ratio(self) -> float:
        if self.total_models == 0:
            return 1.0
        return len(self.models_with_tests) / self.total_models


@dataclass(frozen=True)
class MissingTestFindings:
    """Missing-test findings scoped to a specific set of models (typically
    the changed/added models in a PR)."""

    models_without_any_test: frozenset[str] = field(default_factory=frozenset)
    key_column_issues: tuple[KeyColumnIssue, ...] = field(default_factory=tuple)


def _tests_by_model(manifest: Manifest) -> dict[str, list[dict]]:
    """Map model unique_id -> list of generic test nodes attached to it."""
    index: dict[str, list[dict]] = {}
    for test in manifest.tests().values():
        attached = test.get("attached_node")
        targets = [attached] if attached else test.get("depends_on", {}).get(
            "nodes", []
        )
        for target in targets:
            if target in manifest.models():
                index.setdefault(target, []).append(test)
    return index


def test_coverage(manifest: Manifest) -> CoverageReport:
    """Compute the fraction of models with >= 1 test attached."""
    models = manifest.models()
    tests_by_model = _tests_by_model(manifest)

    with_tests = {uid for uid in models if tests_by_model.get(uid)}
    without_tests = set(models) - with_tests

    return CoverageReport(
        total_models=len(models),
        models_with_tests=frozenset(with_tests),
        models_without_tests=frozenset(without_tests),
    )


def _guess_key_column(model_node: dict) -> str | None:
    """Best-effort guess at a model's primary/key column.

    Preference order: a column with a `primary_key` constraint (dbt column
    constraints), then a column literally named `id` or `<model>_id`, then
    the first declared column. Returns None if the model has no declared
    columns at all (can't check what we can't see without a catalog).
    """
    columns: dict[str, dict] = model_node.get("columns", {}) or {}
    if not columns:
        return None

    for name, col in columns.items():
        for constraint in col.get("constraints", []) or []:
            ctype = constraint.get("type") if isinstance(constraint, dict) else None
            if ctype == "primary_key":
                return name

    model_name = model_node.get("name", "")
    candidates = {"id", f"{model_name}_id"}
    for name in columns:
        if name.lower() in candidates:
            return name

    return next(iter(columns))


def _key_column_tests(
    model_id: str, key_column: str, tests_by_model: dict[str, list[dict]]
) -> tuple[bool, bool]:
    """Returns (has_not_null, has_unique) for the given model's key column."""
    has_not_null = False
    has_unique = False
    for test in tests_by_model.get(model_id, []):
        test_meta = test.get("test_metadata") or {}
        test_name = test_meta.get("name")
        column = test.get("column_name")
        if column != key_column:
            continue
        if test_name == "not_null":
            has_not_null = True
        elif test_name == "unique":
            has_unique = True
    return has_not_null, has_unique


def missing_test_findings(
    manifest: Manifest, model_ids: set[str]
) -> MissingTestFindings:
    """Missing-test / key-column findings restricted to `model_ids`
    (normally the changed+added models in a PR)."""
    models = manifest.models()
    tests_by_model = _tests_by_model(manifest)

    without_any_test = {
        uid for uid in model_ids if uid in models and not tests_by_model.get(uid)
    }

    key_column_issues: list[KeyColumnIssue] = []
    for uid in sorted(model_ids):
        node = models.get(uid)
        if node is None:
            continue
        key_column = _guess_key_column(node)
        if key_column is None:
            continue
        has_not_null, has_unique = _key_column_tests(uid, key_column, tests_by_model)
        if not has_not_null or not has_unique:
            key_column_issues.append(
                KeyColumnIssue(
                    model_id=uid,
                    column=key_column,
                    missing_not_null=not has_not_null,
                    missing_unique=not has_unique,
                )
            )

    return MissingTestFindings(
        models_without_any_test=frozenset(without_any_test),
        key_column_issues=tuple(key_column_issues),
    )
