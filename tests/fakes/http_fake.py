"""Servidor HTTP simulado com estado para Azure DevOps, GitHub, Jira e webhooks (formato das APIs oficiais)."""

from __future__ import annotations

import base64
import json
import re
import urllib.parse
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
    org: str = "https://dev.azure.com/empresa"
    project: str = "Pagamentos"
    pat: str = "pat-sandbox"
    github_token: str = "ghs_sandbox"
    items: dict[int, dict[str, Any]] = field(default_factory=dict)
    comments: dict[int, list[str]] = field(default_factory=dict)
    issues: dict[int, dict[str, Any]] = field(default_factory=dict)
    jira: dict[str, dict[str, Any]] = field(default_factory=dict)
    prs: list[dict[str, Any]] = field(default_factory=list)
    webhooks: list[dict[str, Any]] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    fail_next: int = 0
    process_forbidden: bool = False
    wiki: dict[str, dict[str, Any]] = field(default_factory=dict)
    confluence: dict[str, dict[str, Any]] = field(default_factory=dict)
    github_users: dict[str, str] = field(default_factory=lambda: {"ghs_sandbox": "dev-sandbox"})
    azure_users: dict[str, str] = field(default_factory=lambda: {"pat-sandbox": "dev@empresa.com"})
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
        az = re.escape(f"{self.org}/{self.project}/_apis/wit")
        return [
            (rf"POST {az}/workitems/\$([^?]+)\?api-version=7\.1", self.az_create),
            (rf"GET {az}/workitems/(\d+)\?api-version=7\.1", self.az_get),
            (rf"PATCH {az}/workitems/(\d+)\?api-version=7\.1", self.az_update),
            (rf"POST {az}/workItems/(\d+)/comments\?.*", self.az_comment),
            (rf"POST {az}/workitemsbatch\?api-version=7\.1", self.az_batch),
            (rf"POST {az}/wiql\?api-version=7\.1", self.az_wiql),
            (rf"GET {re.escape(self.org)}/_apis/projects/[^/]+/properties\?.*", self.az_props),
            (rf"GET {re.escape(self.org)}/_apis/work/processes/([^/]+)/workitemtypes\?.*", self.az_types),
            (r"GET https://api\.github\.com/user", self.gh_user),
            (
                rf"GET {re.escape(self.org)}/[^/]+/_apis/wiki/wikis/[^/]+/pages\?path=([^&]+)&api-version=7\.1",
                self.wiki_get,
            ),
            (
                rf"PUT {re.escape(self.org)}/[^/]+/_apis/wiki/wikis/[^/]+/pages\?path=([^&]+)&api-version=7\.1",
                self.wiki_put,
            ),
            (r"GET https://empresa\.atlassian\.net/wiki/rest/api/content\?(.*)", self.conf_find),
            (r"POST https://empresa\.atlassian\.net/wiki/rest/api/content", self.conf_create),
            (r"PUT https://empresa\.atlassian\.net/wiki/rest/api/content/(\d+)", self.conf_update),
            (rf"GET {re.escape(self.org)}/_apis/connectionData\?api-version=7\.1", self.az_connection),
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
            (r"POST https://empresa\.atlassian\.net/rest/api/3/issue", self.jira_create),
            (r"GET https://empresa\.atlassian\.net/rest/api/3/issue/([A-Z]+-\d+)\?.*", self.jira_get),
            (r"PUT https://empresa\.atlassian\.net/rest/api/3/issue/([A-Z]+-\d+)", self.jira_put),
            (r"GET https://empresa\.atlassian\.net/rest/api/3/issue/([A-Z]+-\d+)/transitions", self.jira_transitions),
            (
                r"POST https://empresa\.atlassian\.net/rest/api/3/issue/([A-Z]+-\d+)/transitions",
                self.jira_do_transition,
            ),
            (r"POST https://empresa\.atlassian\.net/rest/api/3/issue/([A-Z]+-\d+)/comment", self.jira_comment),
            (r"POST https://hooks\.(teams|slack)\.example/.*", self.webhook),
        ]

    # ------------------------------------------------------------ Azure DevOps

    def _az_auth(self, headers: dict[str, str]) -> bool:
        auth = headers.get("Authorization")
        return auth == "Basic " + base64.b64encode(f":{self.pat}".encode()).decode() or auth == "Bearer sp-token"

    def _az_view(self, wid: int) -> dict[str, Any]:
        item = self.items[wid]
        return {
            "id": wid,
            "rev": item["rev"],
            "fields": dict(item["fields"]),
            "relations": list(item["relations"]),
            "_links": {"html": {"href": f"{self.org}/{self.project}/_workitems/edit/{wid}"}},
            "url": f"{self.org}/{self.project}/_apis/wit/workItems/{wid}",
        }

    def _apply_ops(self, item: dict[str, Any], ops: list[dict[str, Any]]) -> None:
        for op in ops:
            if op["path"] == "/relations/-":
                item["relations"].append(op["value"])
            else:
                item["fields"][op["path"].removeprefix("/fields/")] = op["value"]
        item["rev"] += 1
        item["fields"]["System.ChangedDate"] = f"2026-09-24T12:00:{item['rev']:02d}Z"

    def az_create(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        if not self._az_auth(headers):
            return Response(401, {"message": "TF400813: não autorizado"})
        assert headers.get("Content-Type") == "application/json-patch+json"
        wid = 100 + len(self.items) + 1
        item = {
            "rev": 0,
            "relations": [],
            "fields": {"System.WorkItemType": m.group(1).replace("%20", " "), "System.State": "New"},
        }
        self._apply_ops(item, body)
        self.items[wid] = item
        return Response(200, self._az_view(wid))

    def az_get(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        wid = int(m.group(1))
        return Response(200, self._az_view(wid)) if wid in self.items else Response(404, {"message": "não existe"})

    def az_update(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        wid = int(m.group(1))
        self._apply_ops(self.items[wid], body)
        return Response(200, self._az_view(wid))

    def az_comment(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        self.comments.setdefault(int(m.group(1)), []).append(body["text"])
        return Response(200, {"id": len(self.comments), "text": body["text"]})

    def az_batch(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        return Response(
            200, {"count": len(body["ids"]), "value": [self._az_view(i) for i in body["ids"] if i in self.items]}
        )

    def az_wiql(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        """Suporta a consulta da linha de base: tipos em IN (...) e ClosedDate >= / < datas."""
        query = str(body["query"])
        types = re.findall(r"'([^']+)'", query.split("IN (")[1].split(")")[0])
        since, until = re.findall(r"ClosedDate\] [<>]=? '([\d-]+)'", query)
        ids = [
            {"id": wid}
            for wid, item in sorted(self.items.items())
            if item["fields"].get("System.WorkItemType") in types
            and since <= str(item["fields"].get("Microsoft.VSTS.Common.ClosedDate", ""))[:10] < until
        ]
        return Response(200, {"workItems": ids})

    def az_props(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        if self.process_forbidden:
            return Response(403, {"message": "sem permissão"})
        return Response(
            200,
            {
                "value": [
                    {"name": "System.ProcessTemplateType", "value": "adcc42ab"},
                    {"name": "System.Process Template", "value": "Agile-Pagamentos"},
                ]
            },
        )

    def az_types(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        states = [
            ("New", "Proposed"),
            ("Active", "InProgress"),
            ("Resolved", "Resolved"),
            ("Closed", "Completed"),
            ("Removed", "Removed"),
        ]
        types = ["Epic", "Feature", "User Story", "Task", "Bug"]
        return Response(
            200, {"value": [{"name": t, "states": [{"name": s, "stateCategory": c} for s, c in states]} for t in types]}
        )

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

    # ------------------------------------------------------------ wiki do Azure DevOps e Confluence

    def wiki_get(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        page = self.wiki.get(urllib.parse.unquote(m.group(1)))
        if page is None:
            return Response(404, {"message": "página não existe"})
        return Response(200, {"path": m.group(1), "content": page["content"]}, {"ETag": page["etag"]})

    def wiki_put(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        path = urllib.parse.unquote(m.group(1))
        page = self.wiki.get(path)
        if page is not None and headers.get("If-Match") != page["etag"]:
            return Response(412, {"message": "versão desatualizada (If-Match)"})
        etag = f'"{len(self.wiki)}-{(page or {}).get("rev", 0) + 1}"'
        self.wiki[path] = {"content": body["content"], "etag": etag, "rev": (page or {}).get("rev", 0) + 1}
        return Response(201 if page is None else 200, {"path": path, "remoteUrl": f"{self.org}/_wiki?pagePath={path}"})

    def conf_find(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        q = dict(urllib.parse.parse_qsl(m.group(1)))
        found = [p for p in self.confluence.values() if p["title"] == q.get("title")]
        return Response(200, {"results": found})

    def conf_create(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        pid = str(1000 + len(self.confluence))
        page = {**body, "id": pid, "version": {"number": 1}, "_links": {"webui": f"/spaces/X/pages/{pid}"}}
        self.confluence[pid] = page
        return Response(200, page)

    def conf_update(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        current = self.confluence[m.group(1)]
        if body["version"]["number"] != current["version"]["number"] + 1:
            return Response(409, {"message": "versão fora de ordem"})
        current.update({**body, "_links": current["_links"]})
        return Response(200, current)

    def gh_user(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        login = self.github_users.get(headers.get("Authorization", "").removeprefix("Bearer "))
        return Response(200, {"login": login, "id": 1}) if login else Response(401, {"message": "Bad credentials"})

    def az_connection(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        for pat, email in self.azure_users.items():
            if headers.get("Authorization") == "Basic " + base64.b64encode(f":{pat}".encode()).decode():
                user = {
                    "providerDisplayName": email,
                    "properties": {"Account": {"$type": "System.String", "$value": email}},
                }
                return Response(200, {"authenticatedUser": user})
        return Response(401, {"message": "TF400813: não autorizado"})

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

    # ------------------------------------------------------------ Jira

    def jira_create(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        key = f"{body['fields']['project']['key']}-{len(self.jira) + 1}"
        self.jira[key] = {"status": "To Do", "labels": body["fields"]["labels"], "comments": []}
        return Response(201, {"id": "1", "key": key})

    def jira_get(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        issue = self.jira[m.group(1)]
        return Response(
            200,
            {
                "key": m.group(1),
                "fields": {"status": {"name": issue["status"]}, "labels": issue["labels"], "updated": "x"},
            },
        )

    def jira_put(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        self.jira[m.group(1)]["labels"] = body["fields"]["labels"]
        return Response(204)

    def jira_transitions(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        return Response(
            200,
            {
                "transitions": [
                    {"id": str(i), "name": f"Ir para {n}", "to": {"name": n}}
                    for i, n in enumerate(["To Do", "In Progress", "In Review", "Done"])
                ]
            },
        )

    def jira_do_transition(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        self.jira[m.group(1)]["status"] = ["To Do", "In Progress", "In Review", "Done"][int(body["transition"]["id"])]
        return Response(204)

    def jira_comment(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        self.jira[m.group(1)]["comments"].append(body["body"])
        return Response(201, {"id": "1"})

    # ------------------------------------------------------------ webhooks

    def webhook(self, m: re.Match[str], headers: dict[str, str], body: Any) -> Response:
        self.webhooks.append({"kind": m.group(1), "body": body})
        return Response(202 if m.group(1) == "teams" else 200, None)

    # ------------------------------------------------------------ utilidades de teste

    def az_by_title(self, title_prefix: str) -> dict[str, Any]:
        return next(v for v in self.items.values() if str(v["fields"]["System.Title"]).startswith(title_prefix))

    def az_id(self, title_prefix: str) -> int:
        return next(k for k, v in self.items.items() if str(v["fields"]["System.Title"]).startswith(title_prefix))
