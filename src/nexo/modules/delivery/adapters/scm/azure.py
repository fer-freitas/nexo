"""Azure Repos como SCM: pull requests, status, revisões (votos) e conclusão, no formato normalizado."""

from __future__ import annotations

import base64
import urllib.parse
from pathlib import Path
from typing import Any

from nexo.kernel.runtime import http
from nexo.kernel.workspace import git

VERSION = "api-version=7.1"
WAITING_FOR_AUTHOR = -5  # voto "aguardando o autor" = pedido de mudança
REJECTED = -10


def _branch(ref: str | None) -> str:
    return str(ref or "").removeprefix("refs/heads/")


class AzureReposScm:
    provider = "azure"

    def __init__(
        self, org_url: str, project: str, repo: str, token: str, remote: str = "origin", bearer: bool = False
    ) -> None:
        self.repo = repo
        self.remote = remote
        self.web = f"{org_url.rstrip('/')}/{urllib.parse.quote(project)}/_git/{urllib.parse.quote(repo)}"
        self.api = (
            f"{org_url.rstrip('/')}/{urllib.parse.quote(project)}/_apis/git/repositories/{urllib.parse.quote(repo)}"
        )
        auth = f"Bearer {token}" if bearer else "Basic " + base64.b64encode(f":{token}".encode()).decode()
        self.headers = {"Authorization": auth}

    def _url(self, path: str) -> str:
        sep = "&" if "?" in path else "?"
        return f"{self.api}{path}{sep}{VERSION}"

    def _get(self, path: str) -> Any:
        return http.request("GET", self._url(path), self.headers).body

    def push(self, worktree: Path, branch: str) -> None:
        git.git(["push", "-q", "--force-with-lease", self.remote, f"HEAD:refs/heads/{branch}"], worktree)

    def default_branch(self) -> str:
        return _branch(self._get("").get("defaultBranch")) or "main"

    def _norm(self, pr: dict[str, Any]) -> dict[str, Any]:
        status = str(pr.get("status", "active"))
        merge = str(pr.get("mergeStatus") or "")
        number = int(pr["pullRequestId"])
        return {
            "number": number,
            "title": pr.get("title", ""),
            "body": pr.get("description") or "",
            "state": "open" if status == "active" else "closed",
            "merged": status == "completed",
            "mergeable": None if merge in ("", "notSet", "queued") else merge == "succeeded",
            "html_url": f"{self.web}/pullrequest/{number}",
            "head": {
                "sha": (pr.get("lastMergeSourceCommit") or {}).get("commitId"),
                "ref": _branch(pr.get("sourceRefName")),
            },
            "base": {
                "sha": (pr.get("lastMergeTargetCommit") or {}).get("commitId"),
                "ref": _branch(pr.get("targetRefName")),
            },
            "reviewers": pr.get("reviewers") or [],
        }

    def find_pr(self, branch: str) -> dict[str, Any] | None:
        ref = urllib.parse.quote(f"refs/heads/{branch}", safe="")
        found = self._get(f"/pullrequests?searchCriteria.sourceRefName={ref}&searchCriteria.status=active") or {}
        values = found.get("value") or []
        return self._norm(values[0]) if values else None

    def open_pr(
        self, branch: str, title: str, body: str, base: str | None = None, draft: bool = False
    ) -> dict[str, Any]:
        existing = self.find_pr(branch)
        if existing:
            self.update_pr(int(existing["number"]), {"title": title, "body": body})
            return existing
        pr = http.request(
            "POST",
            self._url("/pullrequests"),
            self.headers,
            {
                "sourceRefName": f"refs/heads/{branch}",
                "targetRefName": f"refs/heads/{base or self.default_branch()}",
                "title": title,
                "description": body[:4000],  # limite do Azure Repos
                "isDraft": draft,
            },
        ).body
        return self._norm(dict(pr))

    def pull(self, number: int) -> dict[str, Any]:
        return self._norm(dict(self._get(f"/pullrequests/{number}")))

    def update_pr(self, number: int, fields: dict[str, Any]) -> dict[str, Any]:
        body = {k: v for k, v in {"title": fields.get("title"), "description": fields.get("body")}.items() if v}
        if "description" in body:
            body["description"] = str(body["description"])[:4000]
        return self._norm(dict(http.request("PATCH", self._url(f"/pullrequests/{number}"), self.headers, body).body))

    def post_review(self, number: int, sha: str, body: str, event: str) -> dict[str, Any]:
        thread = {"comments": [{"parentCommentId": 0, "content": body, "commentType": 1}], "status": 1}
        return dict(http.request("POST", self._url(f"/pullrequests/{number}/threads"), self.headers, thread).body)

    def reviews(self, number: int) -> list[dict[str, Any]]:
        """Revisores com voto "aguardando o autor" ou "rejeitado"; o texto é o último comentário deles."""
        pr = self.pull(number)
        threads = (self._get(f"/pullrequests/{number}/threads") or {}).get("value") or []
        comments = [c for t in threads for c in t.get("comments") or [] if not t.get("isDeleted")]
        out = []
        for r in pr["reviewers"]:
            if int(r.get("vote") or 0) not in (WAITING_FOR_AUTHOR, REJECTED):
                continue
            mine = [c for c in comments if (c.get("author") or {}).get("id") == r.get("id")]
            note = mine[-1] if mine else {}
            out.append(
                {
                    "id": f"{r.get('id')}:{note.get('id', '')}:{r.get('vote')}",
                    "state": "CHANGES_REQUESTED",
                    "body": note.get("content", ""),
                    "user": {"login": r.get("uniqueName") or r.get("displayName", "?"), "type": "User"},
                }
            )
        return out

    def _statuses(self, sha: str) -> list[dict[str, Any]]:
        return list((self._get(f"/commits/{sha}/statuses?latestOnly=true") or {}).get("value") or [])

    def checks(self, sha: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for s in self._statuses(sha):
            state = str(s.get("state"))
            key = "success" if state == "succeeded" else "failure" if state in ("failed", "error") else "pending"
            out[key] = out.get(key, 0) + 1
        return out

    def failed_checks(self, sha: str) -> list[str]:
        return sorted(
            str((s.get("context") or {}).get("name") or s.get("description"))
            for s in self._statuses(sha)
            if s.get("state") in ("failed", "error")
        )

    def merge(self, number: int, sha: str, title: str) -> dict[str, Any]:
        """Conclui o PR por squash só se o head ainda for o commit avaliado; as políticas da branch valem."""
        resp = http.request(
            "PATCH",
            self._url(f"/pullrequests/{number}"),
            self.headers,
            {
                "status": "completed",
                "lastMergeSourceCommit": {"commitId": sha},
                "completionOptions": {
                    "mergeStrategy": "squash",
                    "mergeCommitMessage": title,
                    "deleteSourceBranch": True,
                },
            },
            expect=(200, 400, 409),
        )
        return {"merged": resp.status == 200 and resp.body.get("status") == "completed", "status": resp.status}

    def protection(self, branch: str) -> dict[str, Any]:
        base = self.api.split("/_apis/git/")[0]
        resp = http.request(
            "GET", f"{base}/_apis/policy/configurations?{VERSION}", self.headers, expect=(200, 401, 403)
        )
        if resp.status != 200:
            return {"protected": False}
        target = f"refs/heads/{branch}"
        rules = [
            p
            for p in resp.body.get("value") or []
            if p.get("isEnabled")
            and any(s.get("refName") == target for s in (p.get("settings") or {}).get("scope") or [])
        ]
        reviews = max([int((p.get("settings") or {}).get("minimumApproverCount") or 0) for p in rules] or [0])
        return {"protected": bool(rules), "required_reviews": reviews, "required_checks": []}

    def fetch_ref(self, number: int) -> str:
        return f"refs/heads/{self.pull(number)['head']['ref']}"
