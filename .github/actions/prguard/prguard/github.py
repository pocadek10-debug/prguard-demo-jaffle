"""Minimal GitHub REST client: idempotent PR comment + check run.

Deliberately implemented with `requests` directly rather than PyGithub, to
keep the dependency footprint (and therefore the Docker image / attack
surface) small — see spec section 8.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import requests

from prguard.render import COMMENT_MARKER

Conclusion = Literal["success", "neutral", "failure"]

DEFAULT_API_URL = "https://api.github.com"
MAX_COMMENT_PAGES = 20


class GitHubAPIError(Exception):
    """Raised on unexpected GitHub API responses."""


@dataclass
class GitHubClient:
    token: str
    repo: str  # "owner/name"
    api_url: str = DEFAULT_API_URL

    def _session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(
            {
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )
        return session

    def find_existing_comment_id(self, pr_number: int) -> int | None:
        """Find PRGuard's own comment on a PR by its hidden marker, if any."""
        session = self._session()
        url = f"{self.api_url}/repos/{self.repo}/issues/{pr_number}/comments"
        page = 1
        while page <= MAX_COMMENT_PAGES:
            resp = session.get(url, params={"page": page, "per_page": 100}, timeout=30)
            if resp.status_code != 200:
                raise GitHubAPIError(
                    f"failed to list comments on PR #{pr_number}: "
                    f"{resp.status_code} {resp.text}"
                )
            comments = resp.json()
            if not comments:
                return None
            for comment in comments:
                if (comment.get("body") or "").startswith(COMMENT_MARKER):
                    return comment["id"]
            if len(comments) < 100:
                return None
            page += 1
        return None

    def upsert_pr_comment(self, pr_number: int, body: str) -> dict:
        """Create PRGuard's comment, or update it in place if it exists."""
        session = self._session()
        existing_id = self.find_existing_comment_id(pr_number)
        if existing_id is not None:
            url = f"{self.api_url}/repos/{self.repo}/issues/comments/{existing_id}"
            resp = session.patch(url, json={"body": body}, timeout=30)
        else:
            url = f"{self.api_url}/repos/{self.repo}/issues/{pr_number}/comments"
            resp = session.post(url, json={"body": body}, timeout=30)
        if resp.status_code not in (200, 201):
            raise GitHubAPIError(
                f"failed to upsert PR comment: {resp.status_code} {resp.text}"
            )
        return resp.json()

    def create_check_run(
        self,
        head_sha: str,
        conclusion: Conclusion,
        title: str,
        summary: str,
    ) -> dict:
        session = self._session()
        url = f"{self.api_url}/repos/{self.repo}/check-runs"
        payload = {
            "name": "PRGuard",
            "head_sha": head_sha,
            "status": "completed",
            "conclusion": conclusion,
            "output": {"title": title, "summary": summary},
        }
        resp = session.post(url, json=payload, timeout=30)
        if resp.status_code not in (200, 201):
            raise GitHubAPIError(
                f"failed to create check run: {resp.status_code} {resp.text}"
            )
        return resp.json()


def check_conclusion(has_breaking: bool, strict: bool) -> Conclusion:
    """Config-driven check-run conclusion, per spec section 6."""
    if strict and has_breaking:
        return "failure"
    return "neutral"
