"""Bitbucket Cloud como SCM: pull requests, status de build, revisões e merge, no formato normalizado."""

from __future__ import annotations

import urllib.parse
from pathlib import Path
from typing import Any

from nexo.kernel.runtime import http
from nexo.kernel.workspace import git

API = "https://api.bitbucket.org/2.0"


class BitbucketScm:
    provider = "bitbucket"

    def __init__(self, repo: str, token: str, remote: str = "origin") -> None:
        self.repo = repo  # workspace/repositorio
        self.remote = remote
        self.api = f"{API}/repositories/{repo}"
        self.headers = {"Authorization": f"Bearer {token}"}  # token de acesso do repositório ou do workspace

    def _get(self, path: str) -> Any:
        return http.request("GET", f"{self.api}{path}", self.headers).body

    def push(self, worktree: Path, branch: str) -> None:
        git.git(["push", "-q", "--force-with-lease", self.remote, f"HEAD:refs/heads/{branch}"], worktree)

    def default_branch(self) -> str:
        return str((self._get("").get("mainbranch") or {}).get("name", "main"))

    @staticmethod
    def _norm(pr: dict[str, Any]) -> dict[str, Any]:
        src, dst = pr.get("source") or {}, pr.get("destination") or {}
        state = str(pr.get("state", "OPEN"))
        return {
            "number": int(pr["id"]),
            "title": pr.get("title", ""),
            "body": pr.get("description") or "",
            "state": "open" if state == "OPEN" else "closed",
            "merged": state == "MERGED",
            "mergeable": None,
            "html_url": ((pr.get("links") or {}).get("html") or {}).get("href", ""),
            "head": {"sha": (src.get("commit") or {}).get("hash"), "ref": (src.get("branch") or {}).get("name")},
            "base": {"sha": (dst.get("commit") or {}).get("hash"), "ref": (dst.get("branch") or {}).get("name")},
            "participants": pr.get("participants") or [],
        }

    def find_pr(self, branch: str) -> dict[str, Any] | None:
        q = urllib.parse.quote(f'source.branch.name="{branch}" AND state="OPEN"')
        found = (self._get(f"/pullrequests?q={q}") or {}).get("values") or []
        return self._norm(found[0]) if found else None

    def open_pr(
        self, branch: str, title: str, body: str, base: str | None = None, draft: bool = False
    ) -> dict[str, Any]:
        existing = self.find_pr(branch)
        if existing:
            self.update_pr(int(existing["number"]), {"title": title, "body": body})
            return existing
        pr = http.request(
            "POST",
            f"{self.api}/pullrequests",
            self.headers,
            {
                "title": title,
                "description": body,
                "source": {"branch": {"name": branch}},
                "destination": {"branch": {"name": base or self.default_branch()}},
                "close_source_branch": True,
                "draft": draft,
            },
        ).body
        return self._norm(dict(pr))

    def pull(self, number: int) -> dict[str, Any]:
        return self._norm(dict(self._get(f"/pullrequests/{number}")))

    def update_pr(self, number: int, fields: dict[str, Any]) -> dict[str, Any]:
        body = {k: v for k, v in {"title": fields.get("title"), "description": fields.get("body")}.items() if v}
        return self._norm(dict(http.request("PUT", f"{self.api}/pullrequests/{number}", self.headers, body).body))

    def post_review(self, number: int, sha: str, body: str, event: str) -> dict[str, Any]:
        prefix = "**Pedido de mudança**\n\n" if event == "REQUEST_CHANGES" else ""
        return dict(
            http.request(
                "POST",
                f"{self.api}/pullrequests/{number}/comments",
                self.headers,
                {"content": {"raw": prefix + body}},
            ).body
        )

    def reviews(self, number: int) -> list[dict[str, Any]]:
        """Participantes com `changes_requested`; o texto é o último comentário deles no PR."""
        pr = self.pull(number)
        comments = (self._get(f"/pullrequests/{number}/comments?pagelen=100&sort=-created_on") or {}).get(
            "values"
        ) or []
        out = []
        for p in pr["participants"]:
            user = p.get("user") or {}
            if p.get("state") != "changes_requested":
                continue
            uid = user.get("uuid")
            note: dict[str, Any] = next((c for c in comments if (c.get("user") or {}).get("uuid") == uid), {})
            out.append(
                {
                    "id": f"{uid}:{note.get('id', '')}",
                    "state": "CHANGES_REQUESTED",
                    "body": (note.get("content") or {}).get("raw", ""),
                    "user": {
                        "login": user.get("nickname", "?"),
                        "type": "Bot" if user.get("type") == "app_user" else "User",
                    },
                }
            )
        return out

    def _statuses(self, sha: str) -> list[dict[str, Any]]:
        return list((self._get(f"/commit/{sha}/statuses?pagelen=100") or {}).get("values") or [])

    def checks(self, sha: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for s in self._statuses(sha):
            state = str(s.get("state"))
            key = "success" if state == "SUCCESSFUL" else "failure" if state in ("FAILED", "STOPPED") else "pending"
            out[key] = out.get(key, 0) + 1
        return out

    def failed_checks(self, sha: str) -> list[str]:
        return sorted(
            str(s.get("name") or s.get("key")) for s in self._statuses(sha) if s.get("state") in ("FAILED", "STOPPED")
        )

    def merge(self, number: int, sha: str, title: str) -> dict[str, Any]:
        """O Bitbucket não aceita o sha na chamada: confere o head (hash curto) antes do squash."""
        head = str(self.pull(number)["head"]["sha"] or "")
        if not head or not (sha.startswith(head) or head.startswith(sha)):
            return {"merged": False, "status": 409}
        resp = http.request(
            "POST",
            f"{self.api}/pullrequests/{number}/merge",
            self.headers,
            {"merge_strategy": "squash", "message": title, "close_source_branch": True},
            expect=(200, 400, 409, 555),
        )
        return {"merged": resp.status == 200 and resp.body.get("state") == "MERGED", "status": resp.status}

    def protection(self, branch: str) -> dict[str, Any]:
        q = urllib.parse.quote(branch, safe="")
        rules = (self._get(f"/branch-restrictions?pattern={q}") or {}).get("values") or []
        approvals = [int(r.get("value") or 0) for r in rules if r.get("kind") == "require_approvals_to_merge"]
        return {"protected": bool(rules), "required_reviews": max(approvals or [0]), "required_checks": []}

    def fetch_ref(self, number: int) -> str:
        return f"refs/heads/{self.pull(number)['head']['ref']}"
