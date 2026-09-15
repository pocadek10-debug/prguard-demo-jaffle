"""CLI entrypoint: orchestrates config -> manifests -> report -> render -> GitHub."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from prguard.analysis.report import build_report
from prguard.config import ConfigError, load_config
from prguard.github import GitHubAPIError, GitHubClient, check_conclusion
from prguard.manifests import ManifestError, load_manifest, prepare_base_and_head_manifests
from prguard.render import render_report, summary_line


def _ensure_utf8_stdout() -> None:
    """Make stdout/stderr able to carry the comment's emoji.

    The rendered Markdown contains 🛡️/⛔/⚠️; on Windows the default console
    encoding is cp1252, so `--dry-run` would die with a UnicodeEncodeError
    before printing anything. CI (Linux) is already UTF-8, so this is a no-op
    there.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def _input(name: str, default: str | None) -> str | None:
    """Read a GitHub Action input passed as an INPUT_<NAME> env var.

    Docker container actions expose `with:` inputs this way; an empty
    string (an input left blank in the workflow) is treated as unset.
    """
    value = os.environ.get(f"INPUT_{name}")
    return value if value else default


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prguard",
        description="Analyze a dbt PR's manifest.json for downstream impact, "
        "missing tests, breaking changes, and coverage delta.",
    )
    parser.add_argument("--base", help="path to a pre-built base manifest.json")
    parser.add_argument("--head", help="path to a pre-built head manifest.json")
    parser.add_argument(
        "--repo-dir",
        # `${{ github.workspace }}` in action.yml would resolve to the *runner*
        # path, which does not exist inside a Docker container action — the
        # checkout is mounted at $GITHUB_WORKSPACE (/github/workspace) instead.
        default=_input("REPO-DIR", None)
        or os.environ.get("GITHUB_WORKSPACE")
        or ".",
        help="path to the git repository checkout",
    )
    parser.add_argument(
        "--dbt-project-dir",
        default=_input("DBT-PROJECT-DIR", None),
        help="override prguard.yml's dbt_project_dir",
    )
    parser.add_argument(
        "--config",
        default=_input("CONFIG-PATH", None) or "prguard.yml",
        help="path to prguard.yml",
    )
    parser.add_argument(
        "--base-manifest-path",
        default=_input("BASE-MANIFEST-PATH", None),
        help="fast path: a pre-built base manifest (skips git worktree generation)",
    )
    parser.add_argument(
        "--base-ref", default=None, help="git ref for the PR's base/target branch"
    )
    parser.add_argument("--work-dir", default=None, help="scratch dir for generated manifests")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the rendered Markdown instead of posting to GitHub",
    )
    return parser


def _read_pr_event() -> tuple[int | None, str | None, str | None]:
    """Read the PR number, base ref and head SHA out of GITHUB_EVENT_PATH.

    The head SHA matters: on a `pull_request` event GITHUB_SHA points at the
    ephemeral merge commit, which belongs to no branch — a check run created
    against it never surfaces on the PR. `pull_request.head.sha` is the commit
    the PR actually shows.
    """
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path or not Path(event_path).exists():
        return None, None, None
    event = json.loads(Path(event_path).read_text())
    pr = event.get("pull_request")
    if not pr:
        return None, None, None
    return (
        pr.get("number"),
        (pr.get("base") or {}).get("ref"),
        (pr.get("head") or {}).get("sha"),
    )


def _resolve_manifests(args: argparse.Namespace, project_subdir: str, work_dir: Path):
    if args.base and args.head:
        return load_manifest(args.base), load_manifest(args.head)

    _, event_base_ref, _ = _read_pr_event()
    base_ref = args.base_ref or event_base_ref or "origin/main"
    base_path, head_path = prepare_base_and_head_manifests(
        repo_dir=args.repo_dir,
        project_subdir=project_subdir,
        base_ref=base_ref,
        work_dir=work_dir,
        base_manifest_path=args.base_manifest_path,
    )
    return load_manifest(base_path), load_manifest(head_path)


def run(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    args = build_arg_parser().parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"PRGuard: invalid config: {exc}", file=sys.stderr)
        return 2

    project_subdir = args.dbt_project_dir or config.dbt_project_dir
    work_dir = Path(args.work_dir) if args.work_dir else Path(tempfile.mkdtemp(prefix="prguard-"))

    try:
        base_manifest, head_manifest = _resolve_manifests(args, project_subdir, work_dir)
    except ManifestError as exc:
        print(f"PRGuard: {exc}", file=sys.stderr)
        return 2

    report = build_report(base_manifest, head_manifest, config)
    pr_number, _, pr_head_sha = _read_pr_event()
    commit_sha = pr_head_sha or os.environ.get("GITHUB_SHA", "")
    body = render_report(
        report,
        head_manifest,
        base_manifest,
        commit_sha=commit_sha,
        show_details=config.comment_show_details,
    )

    if args.dry_run:
        print(body)
        return 1 if (config.strict and report.has_breaking) else 0

    repo = os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GITHUB_TOKEN")
    if not repo or not token:
        print(
            "PRGuard: GITHUB_REPOSITORY / GITHUB_TOKEN not set; use --dry-run "
            "outside of a GitHub Actions job.",
            file=sys.stderr,
        )
        return 2

    if pr_number is None:
        print(
            "PRGuard: no pull_request context found in GITHUB_EVENT_PATH; "
            "nothing to comment on.",
            file=sys.stderr,
        )
        return 0

    try:
        client = GitHubClient(token=token, repo=repo)
        client.upsert_pr_comment(pr_number, body)
        conclusion = check_conclusion(report.has_breaking, config.strict)
        client.create_check_run(
            head_sha=commit_sha,
            conclusion=conclusion,
            title=f"PRGuard — {summary_line(report)}",
            summary=body,
        )
    except GitHubAPIError as exc:
        print(f"PRGuard: GitHub API error: {exc}", file=sys.stderr)
        return 2

    return 1 if conclusion == "failure" else 0


def main(argv: list[str] | None = None) -> None:
    sys.exit(run(argv))


if __name__ == "__main__":
    main()
