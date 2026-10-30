"""GitLab como SCM: merge requests, pipelines, revisões e merge, no formato normalizado do GitHub."""

from __future__ import annotations

import urllib.parse
from pathlib import Path
from typing import Any

from nexo.kernel.runtime import http
from nexo.kernel.workspace import git

STATE = {"opened": "open", "closed": "closed", "merged": "closed", "locked": "closed"}
FAILED = ("failed", "canceled")
PENDING = ("created", "waiting_for_resource", "preparing", "pending", "running", "scheduled", "manual")


class GitLabScm:
    provider = "gitlab"

    def __init__(self, project: str, token: str, remote: str = "origin", base_url: str = "https://gitlab.com") -> None:
        self.repo = project
        self.remote = remote
        self.api = f"{base_url.rstrip('/')}/api/v4/projects/{urllib.parse.quote(project, safe='')}"
        self.headers = {"PRIVATE-TOKEN": token}

    def _get(self, path: str) -> Any:
        return http.request("GET", f"{self.api}{path}", self.headers).body

    def push(self, worktree: Path, branch: str) -> None:
        git.git(["push", "-q", "--force-with-lease", self.remote, f"HEAD:refs/heads/{branch}"], worktree)

    def default_branch(self) -> str:
        return str(self._get("").get("default_branch", "main"))

    @staticmethod
    def _norm(mr: dict[str, Any]) -> dict[str, Any]:
        refs = mr.get("diff_refs") or {}
        status = str(mr.get("detailed_merge_status") or mr.get("merge_status") or "")
        return {
            "number": int(mr["iid"]),
            "title": mr.get("title", ""),
            "body": mr.get("description") or "",
            "state": STATE.get(str(mr.get("state")), "open"),
            "merged": mr.get("state") == "merged",
            "mergeable": None if not status else status in ("mergeable", "can_be_merged"),
            "html_url": mr.get("web_url", ""),
            "head": {"sha": refs.get("head_sha") or mr.get("sha"), "ref": mr.get("source_branch")},
            "base": {"sha": refs.get("base_sha"), "ref": mr.get("target_branch")},
        }

    def find_pr(self, branch: str) -> dict[str, Any] | None:
        q = urllib.parse.quote(branch, safe="")
        found = self._get(f"/merge_requests?source_branch={q}&state=opened")
        return self._norm(found[0]) if found else None

    def open_pr(
        self, branch: str, title: str, body: str, base: str | None = None, draft: bool = False
    ) -> dict[str, Any]:
        existing = self.find_pr(branch)
        if existing:
            self.update_pr(int(existing["number"]), {"title": title, "body": body})
            return existing
        mr = http.request(
            "POST",
            f"{self.api}/merge_requests",
            self.headers,
            {
                "source_branch": branch,
                "target_branch": base or self.default_branch(),
                "title": f"Draft: {title}" if draft else title,
                "description": body,
                "remove_source_branch": True,
            },
        ).body
        return self._norm(dict(mr))

    def pull(self, number: int) -> dict[str, Any]:
        return self._norm(dict(self._get(f"/merge_requests/{number}")))

    def update_pr(self, number: int, fields: dict[str, Any]) -> dict[str, Any]:
        body = {k: v for k, v in {"title": fields.get("title"), "description": fields.get("body")}.items() if v}
        return self._norm(dict(http.request("PUT", f"{self.api}/merge_requests/{number}", self.headers, body).body))

    def post_review(self, number: int, sha: str, body: str, event: str) -> dict[str, Any]:
        prefix = "**Pedido de mudança**\n\n" if event == "REQUEST_CHANGES" else ""
        return dict(
            http.request(
                "POST", f"{self.api}/merge_requests/{number}/notes", self.headers, {"body": prefix + body}
            ).body
        )

    def reviews(self, number: int) -> list[dict[str, Any]]:
        """Revisores com `requested_changes`; o texto é a última nota do revisor no MR."""
        reviewers = self._get(f"/merge_requests/{number}/reviewers") or []
        notes = self._get(f"/merge_requests/{number}/notes?sort=desc&per_page=100") or []
        out = []
        for r in reviewers:
            user = r.get("user") or {}
            if r.get("state") != "requested_changes":
                continue
            note: dict[str, Any] = next((n for n in notes if (n.get("author") or {}).get("id") == user.get("id")), {})
            out.append(
                {
                    "id": f"{user.get('id')}:{note.get('id', '')}",
                    "state": "CHANGES_REQUESTED",
                    "body": note.get("body", ""),
                    "user": {"login": user.get("username", "?"), "type": "Bot" if user.get("bot") else "User"},
                }
            )
        return out

    def _pipeline(self, sha: str) -> dict[str, Any] | None:
        found = self._get(f"/pipelines?sha={sha}&order_by=id&sort=desc&per_page=1")
        return dict(found[0]) if found else None

    def checks(self, sha: str) -> dict[str, int]:
        pipe = self._pipeline(sha)
        if pipe is None:
            return {}
        status = str(pipe.get("status"))
        key = "success" if status == "success" else "failure" if status in FAILED else "pending"
        return {key: 1}

    def failed_checks(self, sha: str) -> list[str]:
        pipe = self._pipeline(sha)
        if pipe is None or pipe.get("status") not in FAILED:
            return []
        jobs = self._get(f"/pipelines/{pipe['id']}/jobs?scope[]=failed&per_page=100") or []
        return sorted(str(j.get("name")) for j in jobs) or [f"pipeline {pipe['id']}"]

    def merge(self, number: int, sha: str, title: str) -> dict[str, Any]:
        """Squash só se o head ainda for o commit avaliado (sha); as regras do projeto continuam valendo."""
        resp = http.request(
            "PUT",
            f"{self.api}/merge_requests/{number}/merge",
            self.headers,
            {"sha": sha, "squash": True, "squash_commit_message": title},
            expect=(200, 401, 405, 406, 409, 422),
        )
        return {"merged": resp.status == 200 and resp.body.get("state") == "merged", "status": resp.status}

    def protection(self, branch: str) -> dict[str, Any]:
        resp = http.request(
            "GET",
            f"{self.api}/protected_branches/{urllib.parse.quote(branch, safe='')}",
            self.headers,
            expect=(200, 404),
        )
        if resp.status == 404:
            return {"protected": False}
        rules = http.request("GET", f"{self.api}/approval_rules", self.headers, expect=(200, 403, 404))
        required = sum(int(r.get("approvals_required") or 0) for r in rules.body) if rules.status == 200 else 0
        return {"protected": True, "required_reviews": required, "required_checks": []}

    def fetch_ref(self, number: int) -> str:
        return f"refs/merge-requests/{number}/head"
