"""Loading and validating prguard.yml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

_ALLOWED_TOP_LEVEL = {"version", "dbt_project_dir", "strict", "coverage", "ignore", "comment"}
_ALLOWED_COVERAGE = {"warn_below"}
_ALLOWED_IGNORE = {"models", "paths"}
_ALLOWED_COMMENT = {"show_details"}


class ConfigError(Exception):
    """Raised when prguard.yml is malformed or has unknown keys."""


@dataclass(frozen=True)
class PRGuardConfig:
    version: int = 1
    dbt_project_dir: str = "."
    strict: bool = False
    coverage_warn_below: float = 0.7
    ignore_models: tuple[str, ...] = field(default_factory=tuple)
    ignore_paths: tuple[str, ...] = field(default_factory=tuple)
    comment_show_details: bool = True


def _check_unknown_keys(data: dict, allowed: set[str], context: str) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise ConfigError(
            f"unknown key(s) in {context}: {', '.join(sorted(unknown))}. "
            f"Allowed keys: {', '.join(sorted(allowed))}"
        )


def parse_config(data: dict) -> PRGuardConfig:
    if not isinstance(data, dict):
        raise ConfigError("prguard.yml must contain a mapping at the top level")

    _check_unknown_keys(data, _ALLOWED_TOP_LEVEL, "prguard.yml")

    coverage = data.get("coverage", {}) or {}
    _check_unknown_keys(coverage, _ALLOWED_COVERAGE, "prguard.yml: coverage")

    ignore = data.get("ignore", {}) or {}
    _check_unknown_keys(ignore, _ALLOWED_IGNORE, "prguard.yml: ignore")

    comment = data.get("comment", {}) or {}
    _check_unknown_keys(comment, _ALLOWED_COMMENT, "prguard.yml: comment")

    warn_below = coverage.get("warn_below", 0.7)
    if not isinstance(warn_below, (int, float)) or not 0 <= warn_below <= 1:
        raise ConfigError("coverage.warn_below must be a number between 0 and 1")

    return PRGuardConfig(
        version=data.get("version", 1),
        dbt_project_dir=data.get("dbt_project_dir", "."),
        strict=bool(data.get("strict", False)),
        coverage_warn_below=float(warn_below),
        ignore_models=tuple(ignore.get("models", []) or []),
        ignore_paths=tuple(ignore.get("paths", []) or []),
        comment_show_details=bool(comment.get("show_details", True)),
    )


def load_config(path: str | Path) -> PRGuardConfig:
    """Load prguard.yml from `path`. Returns defaults if the file doesn't exist."""
    p = Path(path)
    if not p.exists():
        return PRGuardConfig()
    try:
        data = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"could not parse {p}: {exc}") from exc
    return parse_config(data)
