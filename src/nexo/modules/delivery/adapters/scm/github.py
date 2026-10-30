"""GitHub como SCM: push da branch da história, PR com `AB#id`, checks e proteção de branch (4.4)."""

from __future__ import annotations

import urllib.parse
from pathlib import Path
from typing import Any

from nexo.kernel.runtime import http
from nexo.kernel.workspace import git

API = "https://api.github.com"


class GitHubScm:
    provider = "github"

    def __init__(self, repo: str, token: str, remote: str = "origin") -> None:
        self.repo = repo
        self.remote = remote
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def push(self, worktree: Path, branch: str) -> None:
        git.git(["push", "-q", "--force-with-lease", self.remote, f"HEAD:refs/heads/{branch}"], worktree)

    def default_branch(self) -> str:
        return str(http.request("GET", f"{API}/repos/{self.repo}", self.headers).body.get("default_branch", "main"))

    def find_pr(self, branch: str) -> dict[str, Any] | None:
        owner = self.repo.split("/")[0]
        q = urllib.parse.quote(f"{owner}:{branch}")
        prs = http.request("GET", f"{API}/repos/{self.repo}/pulls?head={q}&state=open", self.headers).body
        return dict(prs[0]) if prs else None

    def open_pr(
        self, branch: str, title: str, body: str, base: str | None = None, draft: bool = False
    ) -> dict[str, Any]:
        existing = self.find_pr(branch)
        if existing:
            http.request(
                "PATCH",
                f"{API}/repos/{self.repo}/pulls/{existing['number']}",
                self.headers,
                {"title": title, "body": body},
            )
            return existing
        return dict(
            http.request(
                "POST",
                f"{API}/repos/{self.repo}/pulls",
                self.headers,
                {
                    "title": title,
                    "head": branch,
                    "base": base or self.default_branch(),
                    "body": body,
                    "draft": draft,
                },
            ).body
        )

    def commit_file(self, branch: str, path: str, content: str, message: str, base: str | None = None) -> None:
        """Cria a branch a partir da base e grava um arquivo nela pela API (sem clone local) — F35."""
        import base64

        base = base or self.default_branch()
        ref = http.request("GET", f"{API}/repos/{self.repo}/git/ref/heads/{base}", self.headers).body
        http.request(
            "POST",
            f"{API}/repos/{self.repo}/git/refs",
            self.headers,
            {"ref": f"refs/heads/{branch}", "sha": ref["object"]["sha"]},
        )
        body: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": branch,
        }
        quoted = urllib.parse.quote(path)
        try:
            current = http.request("GET", f"{API}/repos/{self.repo}/contents/{quoted}?ref={branch}", self.headers).body
            body["sha"] = current["sha"]
        except http.HttpError as exc:
            if exc.status != 404:
                raise
        http.request("PUT", f"{API}/repos/{self.repo}/contents/{quoted}", self.headers, body)

    def checks(self, sha: str) -> dict[str, int]:
        runs = http.request("GET", f"{API}/repos/{self.repo}/commits/{sha}/check-runs", self.headers).body
        summary: dict[str, int] = {}
        for run in runs.get("check_runs", []):
            key = str(run.get("conclusion") or run.get("status"))
            summary[key] = summary.get(key, 0) + 1
        return summary

    def protection(self, branch: str) -> dict[str, Any]:
        try:
            data = http.request("GET", f"{API}/repos/{self.repo}/branches/{branch}/protection", self.headers).body
        except http.HttpError as exc:
            if exc.status == 404:
                return {"protected": False}
            raise
        reviews = (data.get("required_pull_request_reviews") or {}).get("required_approving_review_count", 0)
        checks = [c.get("context") for c in (data.get("required_status_checks") or {}).get("checks", [])]
        return {"protected": True, "required_reviews": reviews, "required_checks": checks}

    # ---------------------------------------------------------------- reação ao PR e merge

    def pull(self, number: int) -> dict[str, Any]:
        return dict(http.request("GET", f"{API}/repos/{self.repo}/pulls/{number}", self.headers).body)

    def update_pr(self, number: int, fields: dict[str, Any]) -> dict[str, Any]:
        return dict(http.request("PATCH", f"{API}/repos/{self.repo}/pulls/{number}", self.headers, fields).body)

    def post_review(self, number: int, sha: str, body: str, event: str) -> dict[str, Any]:
        """Revisão no PR (COMMENT ou REQUEST_CHANGES) presa ao commit avaliado."""
        return dict(
            http.request(
                "POST",
                f"{API}/repos/{self.repo}/pulls/{number}/reviews",
                self.headers,
                {"commit_id": sha, "body": body, "event": event},
            ).body
        )

    def reviews(self, number: int) -> list[dict[str, Any]]:
        body = http.request("GET", f"{API}/repos/{self.repo}/pulls/{number}/reviews?per_page=100", self.headers).body
        return [dict(r) for r in body or []]

    def _failed_runs(self, sha: str) -> list[dict[str, Any]]:
        runs = http.request("GET", f"{API}/repos/{self.repo}/commits/{sha}/check-runs", self.headers).body
        bad = ("failure", "timed_out", "cancelled", "action_required")
        return [r for r in runs.get("check_runs", []) if r.get("conclusion") in bad]

    def _not_started(self, run: dict[str, Any]) -> bool:
        """Job que nem começou (orçamento/limite de gasto/cobrança do Actions): falha de infraestrutura, não de código
        (achado no teste real: o orçamento da conta esgotou e o PR aparecia com o check reprovado)."""
        if not (run.get("output") or {}).get("annotations_count"):
            return False
        notes = http.request("GET", f"{API}/repos/{self.repo}/check-runs/{run['id']}/annotations", self.headers).body
        text = " ".join(str(n.get("message", "")) for n in notes or []).lower()
        return "was not started" in text or "budget" in text or "spending limit" in text or "billing" in text

    def failed_checks(self, sha: str) -> list[str]:
        return sorted(str(r.get("name")) for r in self._failed_runs(sha) if not self._not_started(r))

    def blocked_checks(self, sha: str) -> list[str]:
        return sorted(str(r.get("name")) for r in self._failed_runs(sha) if self._not_started(r))

    def fetch_ref(self, number: int) -> str:
        return f"pull/{number}/head"

    def merge(self, number: int, sha: str, title: str) -> dict[str, Any]:
        """Merge por squash, só se o head ainda for o commit avaliado (sha) — a proteção de branch continua valendo."""
        return dict(
            http.request(
                "PUT",
                f"{API}/repos/{self.repo}/pulls/{number}/merge",
                self.headers,
                {"sha": sha, "merge_method": "squash", "commit_title": title},
            ).body
        )
