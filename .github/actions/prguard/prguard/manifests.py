"""Loading and light validation of dbt manifest.json files.

We deliberately parse the manifest as plain JSON/dict rather than depending on
`dbt-artifacts-parser` or dbt-core's own artifact schemas: the manifest schema
version drifts across dbt releases, and pinning a parser library would tie
this Action's compatibility matrix to dbt's release cadence. Instead we read
only the handful of fields documented in the spec (section 2) directly off
the dict, and fail with a clear error if a manifest looks structurally
unlike a dbt manifest at all.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

SUPPORTED_SCHEMA_PREFIXES = (
    "https://schemas.getdbt.com/dbt/manifest/",
)


class ManifestError(Exception):
    """Raised when a manifest.json cannot be loaded or looks unsupported."""


@dataclass(frozen=True)
class Manifest:
    """Thin, read-only wrapper around a parsed manifest.json dict."""

    raw: dict[str, Any]
    path: Path | None = None

    @property
    def nodes(self) -> dict[str, dict[str, Any]]:
        return self.raw.get("nodes", {})

    @property
    def sources(self) -> dict[str, dict[str, Any]]:
        return self.raw.get("sources", {})

    @property
    def exposures(self) -> dict[str, dict[str, Any]]:
        return self.raw.get("exposures", {})

    @property
    def parent_map(self) -> dict[str, list[str]]:
        return self.raw.get("parent_map", {})

    @property
    def child_map(self) -> dict[str, list[str]]:
        return self.raw.get("child_map", {})

    @property
    def schema_version(self) -> str | None:
        return self.raw.get("metadata", {}).get("dbt_schema_version")

    def models(self) -> dict[str, dict[str, Any]]:
        """All nodes with resource_type == 'model'."""
        return {
            uid: node
            for uid, node in self.nodes.items()
            if node.get("resource_type") == "model"
        }

    def tests(self) -> dict[str, dict[str, Any]]:
        """All nodes with resource_type == 'test'."""
        return {
            uid: node
            for uid, node in self.nodes.items()
            if node.get("resource_type") == "test"
        }

    def seeds_and_snapshots(self) -> dict[str, dict[str, Any]]:
        return {
            uid: node
            for uid, node in self.nodes.items()
            if node.get("resource_type") in ("seed", "snapshot")
        }

    def model_name(self, unique_id: str) -> str:
        node = self.nodes.get(unique_id)
        if node is not None:
            return node.get("name", unique_id)
        return unique_id

    def exposure_label(self, exposure_id: str) -> str:
        exp = self.exposures.get(exposure_id, {})
        return exp.get("label") or exp.get("name") or exposure_id


def load_manifest(path: str | Path) -> Manifest:
    """Load a manifest.json from disk into a Manifest wrapper.

    Raises ManifestError with a clear message on missing files, invalid JSON,
    or a payload that doesn't structurally look like a dbt manifest.
    """
    p = Path(path)
    if not p.exists():
        raise ManifestError(f"manifest not found at {p}")
    try:
        raw = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        raise ManifestError(f"manifest at {p} is not valid JSON: {exc}") from exc

    if not isinstance(raw, dict) or "nodes" not in raw:
        raise ManifestError(
            f"manifest at {p} does not look like a dbt manifest.json "
            "(missing top-level 'nodes' key)"
        )

    schema_version = raw.get("metadata", {}).get("dbt_schema_version", "")
    if schema_version and not any(
        schema_version.startswith(prefix) for prefix in SUPPORTED_SCHEMA_PREFIXES
    ):
        raise ManifestError(
            f"manifest at {p} has unrecognized dbt_schema_version "
            f"'{schema_version}'; PRGuard expects a schema under "
            f"{SUPPORTED_SCHEMA_PREFIXES}. Pin a supported dbt version or "
            "open an issue if this schema should be supported."
        )

    return Manifest(raw=raw, path=p)


def load_manifest_from_dict(raw: dict[str, Any]) -> Manifest:
    """Wrap an already-parsed manifest dict (mainly for tests)."""
    return Manifest(raw=raw, path=None)


# --- In-Action manifest generation (M9) --------------------------------
#
# `dbt parse` only needs an adapter class it can load to resolve macros; it
# never opens a connection. We exploit that: instead of trying to guess the
# project's real warehouse credentials (which we deliberately never want —
# see spec section 12), we generate a dummy profile pointed at DuckDB
# in-memory, using whatever `profile:` name the project's dbt_project.yml
# declares. This lets `dbt parse` succeed on any project, with zero
# warehouse credentials, as long as dbt-duckdb is installed alongside
# dbt-core in the Action's image.

def _read_profile_name(project_dir: Path) -> str:
    project_yml = project_dir / "dbt_project.yml"
    if not project_yml.exists():
        raise ManifestError(f"no dbt_project.yml found in {project_dir}")
    data = yaml.safe_load(project_yml.read_text()) or {}
    profile = data.get("profile")
    if not profile:
        raise ManifestError(f"{project_yml} has no top-level 'profile:' key")
    return profile


def write_dummy_profiles(profiles_dir: Path, profile_name: str) -> Path:
    """Write a profiles.yml that lets `dbt parse` run with zero warehouse
    credentials, using an in-memory DuckDB target."""
    profiles_dir.mkdir(parents=True, exist_ok=True)
    content = {
        profile_name: {
            "target": "prguard",
            "outputs": {
                "prguard": {
                    "type": "duckdb",
                    "path": ":memory:",
                    "threads": 1,
                }
            },
        }
    }
    profiles_path = profiles_dir / "profiles.yml"
    profiles_path.write_text(yaml.safe_dump(content))
    return profiles_path


def generate_manifest(project_dir: str | Path, work_dir: str | Path) -> Path:
    """Run `dbt deps` (if needed) + `dbt parse` against `project_dir`,
    writing a scratch profiles.yml under `work_dir`, and return the path to
    the resulting target/manifest.json.

    Raises ManifestError if dbt is not installed, the project has no
    dbt_project.yml, or dbt exits non-zero.
    """
    project_dir = Path(project_dir)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    profile_name = _read_profile_name(project_dir)
    profiles_dir = work_dir / "profiles"
    write_dummy_profiles(profiles_dir, profile_name)

    env = dict(os.environ)
    env["DBT_PROFILES_DIR"] = str(profiles_dir)

    if (project_dir / "packages.yml").exists() or (
        project_dir / "package-lock.yml"
    ).exists():
        _run_dbt(["deps"], project_dir, env)

    _run_dbt(["parse", "--profiles-dir", str(profiles_dir)], project_dir, env)

    manifest_path = project_dir / "target" / "manifest.json"
    if not manifest_path.exists():
        raise ManifestError(
            f"'dbt parse' did not produce a manifest at {manifest_path}"
        )
    return manifest_path


def _run_dbt(args: list[str], project_dir: Path, env: dict[str, str]) -> None:
    cmd = ["dbt", *args, "--project-dir", str(project_dir)]
    try:
        result = subprocess.run(
            cmd, cwd=project_dir, env=env, capture_output=True, text=True
        )
    except FileNotFoundError as exc:
        raise ManifestError(
            "the 'dbt' executable was not found; is dbt-core installed in "
            "this image?"
        ) from exc
    if result.returncode != 0:
        raise ManifestError(
            f"'{' '.join(cmd)}' failed (exit {result.returncode}):\n"
            f"{result.stdout}\n{result.stderr}"
        )


def prepare_base_and_head_manifests(
    repo_dir: str | Path,
    project_subdir: str,
    base_ref: str,
    work_dir: str | Path,
    base_manifest_path: str | Path | None = None,
) -> tuple[Path, Path]:
    """Produce (base_manifest_path, head_manifest_path) for a PR build.

    The head manifest is always generated from the checked-out working tree.
    The base manifest is either taken from `base_manifest_path` (the fast
    path — e.g. an artifact from the target branch's own CI run) or
    generated by checking out `base_ref` into a temporary git worktree and
    running `dbt parse` there.
    """
    repo_dir = Path(repo_dir)
    work_dir = Path(work_dir)

    head_project_dir = repo_dir / project_subdir
    head_manifest = generate_manifest(head_project_dir, work_dir / "head")

    if base_manifest_path is not None:
        return Path(base_manifest_path), head_manifest

    worktree_dir = work_dir / "base-worktree"
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(worktree_dir), base_ref],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        base_project_dir = worktree_dir / project_subdir
        generated_base_manifest = generate_manifest(base_project_dir, work_dir / "base")
        # Copy the manifest out of the worktree before it gets removed below —
        # `git worktree remove` deletes everything under worktree_dir,
        # including target/manifest.json.
        base_manifest = work_dir / "base" / "manifest.json"
        base_manifest.parent.mkdir(parents=True, exist_ok=True)
        base_manifest.write_bytes(generated_base_manifest.read_bytes())
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree_dir)],
            cwd=repo_dir,
            check=False,
            capture_output=True,
            text=True,
        )

    return base_manifest, head_manifest
