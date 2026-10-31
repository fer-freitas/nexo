"""Servidor HTTP simulado com estado para GitHub e webhooks (formato das APIs oficiais)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from nexo.kernel.runtime.http import Response


@dataclass
class Call:
    method: str
    url: str
    headers: dict[str, str]
    body: Any


@dataclass
class FakeCloud:
    github_token: str = "ghs_sandbox"
    issues: dict[int, dict[str, Any]] = field(default_factory=dict)
    prs: list[dict[str, Any]] = field(default_factory=list)
    webhooks: list[dict[str, Any]] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    fail_next: int = 0
    process_forbidden: bool = False
    github_users: dict[str, str] = field(default_factory=lambda: {"ghs_sandbox": "dev-sandbox"})
    check_runs: list[dict[str, Any]] | None = None  # sobrescreve o resultado do CI
    pr_reviews: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    posted_reviews: list[dict[str, Any]] = field(default_factory=list)
    refs: dict[str, str] = field(default_factory=dict)  # branches criadas pela API
    files: dict[tuple[str, str], str] = field(default_factory=dict)  # (branch, caminho) → conteúdo

    # ------------------------------------------------------------ transporte

    def __call__(self, method: str, url: str, headers: dict[str, str], data: bytes | None) -> Response:
        body = json.loads(data.decode()) if data else None
        self.calls.append(Call(method, url, headers, body))
        if self.fail_next:
            self.fail_next -= 1
            return Response(503, {"message": "indisponível"})
        for pattern, handler in self._routes():
            m = re.fullmatch(pattern, f"{method} {url}")
            if m:
                return handler(m, headers, body)
        return Response(404, {"message": f"rota simulada inexistente: {method} {url}"})

    def _routes(self) -> list[tuple[str, Any]]:
        return [
            (r"GET https://api\.github\.com/user", self.gh_user),
            (r"POST https://api\.github\.com/repos/([^/]+/[^/]+)/issues", self.gh_create),
            (r"GET https://api\.github\.com/repos/[^/]+/[^/]+/issues/(\d+)", self.gh_get),
            (r"GET https://api\.github\.com/repos/[^/]+/[^/]+/issues\?(.*)", self.gh_list),
            (r"PATCH https://api\.github\.com/repos/[^/]+/[^/]+/issues/(\d+)", self.gh_patch),
            (r"POST https://api\.github\.com/repos/[^/]+/[^/]+/issues/(\d+)/comments", self.gh_comment),
            (r"GET https://api\.github\.com/repos/[^/]+/[^/]+/git/ref/heads/(.+)", self.gh_get_ref),
            (r"POST https://api\.github\.com/repos/[^/]+/[^/]+/git/refs", self.gh_create_ref),
            (r"GET https://api\.github\.com/repos/[^/]+/[^/]+/contents/([^?]+)\?ref=(.+)", self.gh_get_content),
            (r"PUT https://api\.github\.com/repos/[^/]+/[^/]+/contents/(.+)", self.gh_put_content),
            (r"GET https://api\.github\.com/repos/([^/]+/[^/]+)", self.gh_repo),
            (r"GET https://api\.github\.com/repos/[^/]+/[^/]+/pulls\?head=([^&]+)&state=open", self.gh_find_pr),
            (r"POST https://api\.github\.com/repos/([^/]+/[^/]+)/pulls", self.gh_create_pr),
            (r"PATCH https://api\.github\.com/repos/[^/]+/[^/]+/pulls/(\d+)", self.gh_update_pr),
            (r"GET https://api\.github\.com/repos/[^/]+/[^/]+/pulls/(\d+)", self.gh_get_pr),
            (r"GET https://api\.github\.com/repos/[^/]+/[^/]+/pulls/(\d+)/reviews\?.*", self.gh_reviews),
            (r"POST https://api\.github\.com/repos/[^/]+/[^/]+/pulls/(\d+)/reviews", self.gh_post_review),
            (r"PUT https://api\.github\.com/repos/[^/]+/[^/]+/pulls/(\d+)/merge", self.gh_merge),
            (r"GET https://api\.github\.com/repos/[^/]+/[^/]+/commits/[^/]+/check-runs", self.gh_checks),
            (r"GET https://api\.github\.com/repos/[^/]+/[^/]+/branches/([^/]+)/protection", self.gh_protection),
            (r"POST https://hooks\.(teams)\.example/.*", self.webhook),
        ]

    # ------------------------------------------------------------ GitHub

    def gh_create(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        number = len(self.issues) + 1
        self.issues[number] = {
            "number": number,
            "state": "open",
            "title": body["title"],
            "body": body["body"],
            "labels": [{"name": n} for n in body["labels"]],
            "comments": [],
            "html_url": f"https://github.com/{m.group(1)}/issues/{number}",
            "updated_at": "x",
        }
        return Response(201, self.issues[number])

    def gh_user(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        login = self.github_users.get(headers.get("Authorization", "").removeprefix("Bearer "))
        return Response(200, {"login": login, "id": 1}) if login else Response(401, {"message": "Bad credentials"})

    def gh_get(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        return Response(200, self.issues[int(m.group(1))])

    def gh_list(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        params = dict(p.split("=", 1) for p in m.group(1).split("&"))
        wanted = [i for _, i in sorted(self.issues.items()) if params.get("state") in (None, "all", i["state"])]
        page, size = int(params.get("page", 1)), int(params.get("per_page", 30))
        return Response(200, wanted[(page - 1) * size : page * size])

    def gh_patch(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        issue = self.issues[int(m.group(1))]
        if "labels" in body:
            issue["labels"] = [{"name": n} for n in body["labels"]]
        if "state" in body:
            issue["state"] = body["state"]
        return Response(200, issue)

    def gh_comment(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        self.issues[int(m.group(1))]["comments"].append(body["body"])
        return Response(201, {"id": 1})

    def gh_get_ref(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        return Response(200, {"object": {"sha": self.refs.get(m.group(1), "sha-main")}})

    def gh_create_ref(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        self.refs[body["ref"].removeprefix("refs/heads/")] = body["sha"]
        return Response(201, {"ref": body["ref"]})

    def gh_get_content(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        if (m.group(2), m.group(1)) in self.files:
            return Response(200, {"sha": "sha-arquivo"})
        return Response(404, {"message": "Not Found"})

    def gh_put_content(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        import base64
        import urllib.parse

        path = urllib.parse.unquote(m.group(1))
        self.files[(body["branch"], path)] = base64.b64decode(body["content"]).decode("utf-8")
        return Response(201, {"content": {"path": path}})

    def gh_repo(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        return Response(200, {"full_name": m.group(1), "default_branch": "main"})

    def gh_find_pr(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        head = m.group(1).replace("%3A", ":").split(":", 1)[1]
        return Response(200, [p for p in self.prs if p["head"] == head])

    def gh_create_pr(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        pr = {
            **body,
            "number": len(self.prs) + 1,
            "html_url": f"https://github.com/{m.group(1)}/pull/{len(self.prs) + 1}",
        }
        self.prs.append(pr)
        return Response(201, pr)

    def gh_update_pr(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        pr = self.prs[int(m.group(1)) - 1]
        pr.update(body)
        return Response(200, pr)

    def gh_get_pr(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        pr = self.prs[int(m.group(1)) - 1]
        return Response(
            200,
            {
                "number": pr["number"],
                "state": pr.get("state", "open"),
                "title": pr.get("title"),
                "merged": pr.get("merged", False),
                "mergeable": True,
                "head": {"ref": pr["head"], "sha": pr.get("sha", "abc123")},
                "base": {"ref": pr.get("base", "main"), "sha": pr.get("base_sha", "base123")},
                "body": pr.get("body", ""),
            },
        )

    def gh_post_review(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        review = {"id": len(self.posted_reviews) + 1, "pr": int(m.group(1)), **dict(body or {})}
        self.posted_reviews.append(review)
        return Response(200, review)

    def gh_reviews(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        return Response(200, self.pr_reviews.get(int(m.group(1)), []))

    def gh_merge(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        pr = self.prs[int(m.group(1)) - 1]
        pr["merged"] = True
        pr["merge_body"] = body
        return Response(200, {"merged": True, "sha": body.get("sha")})

    def gh_checks(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        if self.check_runs is not None:
            return Response(200, {"check_runs": self.check_runs})
        return Response(
            200,
            {
                "check_runs": [
                    {"conclusion": "success"},
                    {"conclusion": "success"},
                    {"status": "in_progress", "conclusion": None},
                ]
            },
        )

    def gh_protection(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        if m.group(1) != "main":
            return Response(404, {"message": "Branch not protected"})
        return Response(
            200,
            {
                "required_pull_request_reviews": {"required_approving_review_count": 1},
                "required_status_checks": {"checks": [{"context": "nexo-quality"}]},
            },
        )

    # ------------------------------------------------------------ webhooks

    def webhook(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        self.webhooks.append({"kind": m.group(1), "body": body})
        return Response(202 if m.group(1) == "teams" else 200, None)

    # ------------------------------------------------------------ utilidades de teste
