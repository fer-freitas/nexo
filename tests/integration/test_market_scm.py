"""repositório e PR de mercado: GitLab, Bitbucket e Azure Repos no mesmo formato normalizado."""

from __future__ import annotations

import base64
import json
import re
import urllib.parse
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from nexo.kernel.runtime import http
from nexo.modules.delivery.adapters.scm.azure import AzureReposScm
from nexo.modules.delivery.adapters.scm.bitbucket import BitbucketScm
from nexo.modules.delivery.adapters.scm.gitlab import GitLabScm
from tests.conftest import Runner, git_cmd
from tests.integration.test_story import OK_REVIEWS, QA, impl, run_json

HEAD, OTHER = "a" * 40, "b" * 40


@dataclass
class FakeScm:
    """Simula as três APIs; cada PR guarda o estado no formato do provedor."""

    prs: dict[str, dict[int, dict[str, Any]]] = field(
        default_factory=lambda: {"gitlab": {}, "bitbucket": {}, "azure": {}}
    )
    auth: list[str] = field(default_factory=list)
    calls: list[tuple[str, str]] = field(default_factory=list)
    ci: str = "success"

    def __call__(self, method: str, url: str, headers: dict[str, str], data: bytes | None) -> http.Response:
        self.auth.append(headers.get("PRIVATE-TOKEN") or headers.get("Authorization", ""))
        self.calls.append((method, url))
        body = json.loads(data or b"{}")
        if "/api/v4/" in url:
            return self.gitlab(method, url, body)
        if "api.bitbucket.org" in url:
            return self.bitbucket(method, url, body)
        return self.azure(method, url, body)

    # ---- GitLab
    def gitlab(self, method: str, url: str, b: dict[str, Any]) -> http.Response:
        mrs = self.prs["gitlab"]
        path = url.split("/api/v4/projects/", 1)[1]
        project, _, rest = path.partition("/")
        assert urllib.parse.unquote(project) == "pagamentos/estornos"
        if rest == "" and method == "GET":
            return http.Response(200, {"default_branch": "main"})
        if rest.startswith("merge_requests?source_branch="):
            branch = urllib.parse.unquote(rest.split("=", 1)[1].split("&")[0])
            return http.Response(
                200, [m for m in mrs.values() if m["source_branch"] == branch and m["state"] == "opened"]
            )
        if rest == "merge_requests" and method == "POST":
            n = len(mrs) + 1
            mrs[n] = {
                "iid": n,
                "title": b["title"],
                "description": b["description"],
                "state": "opened",
                "source_branch": b["source_branch"],
                "target_branch": b["target_branch"],
                "sha": HEAD,
                "web_url": f"https://gitlab.empresa.com/pagamentos/estornos/-/merge_requests/{n}",
                "diff_refs": {"head_sha": HEAD, "base_sha": OTHER},
                "detailed_merge_status": "mergeable",
                "notes": [],
                "reviewers": [],
            }
            return http.Response(201, mrs[n])
        m = re.match(r"merge_requests/(\d+)(/.*)?$", rest.split("?")[0])
        if m:
            mr = mrs[int(m.group(1))]
            sub = m.group(2) or ""
            if sub == "/notes" and method == "POST":
                mr["notes"].insert(0, {"id": len(mr["notes"]) + 1, "body": b["body"], "author": {"id": 99}})
                return http.Response(201, mr["notes"][0])
            if sub == "/notes":
                return http.Response(200, mr["notes"])
            if sub == "/reviewers":
                return http.Response(200, mr["reviewers"])
            if sub == "/merge":
                if b["sha"] != mr["sha"]:
                    return http.Response(409, {"message": "SHA does not match HEAD of source branch"})
                mr["state"] = "merged"
                return http.Response(200, mr)
            if method == "PUT":
                mr.update({k: v for k, v in b.items() if k in ("title", "description")})
            return http.Response(200, mr)
        if rest.startswith("pipelines?sha="):
            return http.Response(200, [{"id": 7, "status": self.ci}])
        if rest.startswith("pipelines/7/jobs"):
            return http.Response(200, [{"name": "unit-tests"}])
        if rest.startswith("protected_branches/"):
            return http.Response(200, {"name": "main"})
        if rest == "approval_rules":
            return http.Response(200, [{"approvals_required": 2}])
        return http.Response(404, {"message": f"não simulado: {method} {url}"})

    # ---- Bitbucket
    def bitbucket(self, method: str, url: str, b: dict[str, Any]) -> http.Response:
        prs = self.prs["bitbucket"]
        rest = url.split("/repositories/empresa/estornos", 1)[1]
        if rest == "":
            return http.Response(200, {"mainbranch": {"name": "main"}})
        if rest.startswith("/pullrequests?q="):
            q = urllib.parse.unquote(rest.split("=", 1)[1])
            branch = re.search(r'source.branch.name="([^"]+)"', q).group(1)  # type: ignore[union-attr]
            return http.Response(
                200,
                {
                    "values": [
                        p for p in prs.values() if p["source"]["branch"]["name"] == branch and p["state"] == "OPEN"
                    ]
                },
            )
        if rest == "/pullrequests" and method == "POST":
            n = len(prs) + 1
            prs[n] = {
                "id": n,
                "title": b["title"],
                "description": b["description"],
                "state": "OPEN",
                "source": {"branch": b["source"]["branch"], "commit": {"hash": HEAD[:12]}},
                "destination": {"branch": b["destination"]["branch"], "commit": {"hash": OTHER[:12]}},
                "links": {"html": {"href": f"https://bitbucket.org/empresa/estornos/pull-requests/{n}"}},
                "participants": [],
                "comments": [],
            }
            return http.Response(201, prs[n])
        m = re.match(r"/pullrequests/(\d+)(/[a-z]+)?", rest)
        if m:
            pr = prs[int(m.group(1))]
            sub = m.group(2) or ""
            if sub == "/comments" and method == "POST":
                pr["comments"].insert(
                    0, {"id": len(pr["comments"]) + 1, "content": b["content"], "user": {"uuid": "{bot}"}}
                )
                return http.Response(201, pr["comments"][0])
            if sub == "/comments":
                return http.Response(200, {"values": pr["comments"]})
            if sub == "/merge":
                pr["state"] = "MERGED"
                return http.Response(200, pr)
            if method == "PUT":
                pr.update({k: v for k, v in b.items() if k in ("title", "description")})
            return http.Response(200, pr)
        if rest.startswith("/commit/"):
            state = {"success": "SUCCESSFUL", "failed": "FAILED"}[self.ci]
            return http.Response(200, {"values": [{"state": state, "name": "pipeline", "key": "k"}]})
        if rest.startswith("/branch-restrictions"):
            return http.Response(200, {"values": [{"kind": "require_approvals_to_merge", "value": 1}]})
        return http.Response(404, {"message": f"não simulado: {method} {url}"})

    # ---- Azure Repos
    def azure(self, method: str, url: str, b: dict[str, Any]) -> http.Response:
        prs = self.prs["azure"]
        assert url.startswith("https://dev.azure.com/empresa/Pagamentos/_apis/") and "api-version=7.1" in url, url
        if "/_apis/policy/configurations" in url:
            return http.Response(
                200,
                {
                    "value": [
                        {
                            "isEnabled": True,
                            "settings": {"minimumApproverCount": 2, "scope": [{"refName": "refs/heads/main"}]},
                        }
                    ]
                },
            )
        rest = url.split("/repositories/estornos", 1)[1].split("?")[0]
        query = urllib.parse.parse_qs(url.split("?", 1)[1])
        if rest == "":
            return http.Response(200, {"defaultBranch": "refs/heads/main"})
        if rest == "/pullrequests" and method == "GET":
            ref = query["searchCriteria.sourceRefName"][0]
            return http.Response(
                200, {"value": [p for p in prs.values() if p["sourceRefName"] == ref and p["status"] == "active"]}
            )
        if rest == "/pullrequests" and method == "POST":
            n = len(prs) + 1
            prs[n] = {
                "pullRequestId": n,
                "title": b["title"],
                "description": b["description"],
                "status": "active",
                "sourceRefName": b["sourceRefName"],
                "targetRefName": b["targetRefName"],
                "mergeStatus": "succeeded",
                "lastMergeSourceCommit": {"commitId": HEAD},
                "lastMergeTargetCommit": {"commitId": OTHER},
                "reviewers": [],
                "threads": [],
            }
            return http.Response(201, prs[n])
        m = re.match(r"/pullrequests/(\d+)(/threads)?$", rest)
        if m:
            pr = prs[int(m.group(1))]
            if m.group(2) and method == "POST":
                pr["threads"].append({"comments": b["comments"]})
                return http.Response(200, pr["threads"][-1])
            if m.group(2):
                return http.Response(200, {"value": pr["threads"]})
            if method == "PATCH" and b.get("status") == "completed":
                if b["lastMergeSourceCommit"]["commitId"] != pr["lastMergeSourceCommit"]["commitId"]:
                    return http.Response(409, {"message": "TF401192: commit desatualizado"})
                pr["status"] = "completed"
            elif method == "PATCH":
                pr.update({k: v for k, v in b.items() if k in ("title", "description")})
            return http.Response(200, pr)
        if rest.startswith("/commits/"):
            state = {"success": "succeeded", "failed": "failed"}[self.ci]
            return http.Response(200, {"value": [{"state": state, "context": {"name": "build"}}]})
        return http.Response(404, {"message": f"não simulado: {method} {url}"})


@pytest.fixture
def fake() -> Iterator[FakeScm]:
    f = FakeScm()
    http.set_transport(f)
    yield f
    http.set_transport(None)


def adapters() -> dict[str, Any]:
    return {
        "gitlab": GitLabScm("pagamentos/estornos", "glpat-x", base_url="https://gitlab.empresa.com"),
        "bitbucket": BitbucketScm("empresa/estornos", "bb-token"),
        "azure": AzureReposScm("https://dev.azure.com/empresa", "Pagamentos", "estornos", "pat-x"),
    }


@pytest.mark.parametrize("provider", ["gitlab", "bitbucket", "azure"])
def test_pr_lifecycle_in_the_normalized_shape(fake: FakeScm, provider: str) -> None:
    scm = adapters()[provider]
    pr = scm.open_pr("nexo/story/US-1-1", "US-1-1: Estornar", "corpo")
    assert pr["state"] == "open" and pr["head"]["ref"] == "nexo/story/US-1-1" and pr["base"]["ref"] == "main"
    assert int(pr["html_url"].rstrip("/").rsplit("/", 1)[-1]) == pr["number"]  # o número sai do link, como no GitHub
    again = scm.open_pr("nexo/story/US-1-1", "US-1-1: Estornar (rev 2)", "corpo novo")
    assert again["number"] == pr["number"] and len(fake.prs[provider]) == 1  # reaproveita o PR aberto
    got = scm.pull(pr["number"])
    assert got["body"] == "corpo novo" and got["title"].endswith("(rev 2)") and not got["merged"]
    assert scm.checks(HEAD) == {"success": 1} and scm.failed_checks(HEAD) == []
    fake.ci = "failed"
    assert scm.checks(HEAD) == {"failure": 1} and scm.failed_checks(HEAD)
    fake.ci = "success"
    scm.post_review(pr["number"], HEAD, "achado de segurança", "REQUEST_CHANGES")
    assert scm.merge(pr["number"], OTHER, "squash")["merged"] is False  # outro commit: não faz merge
    assert scm.merge(pr["number"], HEAD, "US-1-1: Estornar")["merged"] is True
    assert scm.pull(pr["number"])["merged"] and scm.pull(pr["number"])["state"] == "closed"
    prot = scm.protection("main")
    assert prot["protected"] and prot["required_reviews"] >= 1
    assert scm.fetch_ref(pr["number"]) in (f"refs/merge-requests/{pr['number']}/head", "refs/heads/nexo/story/US-1-1")


def test_change_requests_become_reviews(fake: FakeScm) -> None:
    gl, bb, az = adapters()["gitlab"], adapters()["bitbucket"], adapters()["azure"]
    n = gl.open_pr("f", "t", "b")["number"]
    mr = fake.prs["gitlab"][n]
    mr["reviewers"] = [
        {"user": {"id": 5, "username": "ana"}, "state": "requested_changes"},
        {"user": {"id": 6, "username": "rui"}, "state": "approved"},
    ]
    mr["notes"] = [{"id": 11, "body": "falta teste de erro", "author": {"id": 5}}]
    assert [(r["state"], r["body"], r["user"]["login"]) for r in gl.reviews(n)] == [
        ("CHANGES_REQUESTED", "falta teste de erro", "ana")
    ]
    n = bb.open_pr("f", "t", "b")["number"]
    pr = fake.prs["bitbucket"][n]
    pr["participants"] = [{"user": {"uuid": "{u1}", "nickname": "bia"}, "state": "changes_requested"}]
    pr["comments"] = [{"id": 3, "content": {"raw": "renomear a classe"}, "user": {"uuid": "{u1}"}}]
    assert [(r["body"], r["user"]["login"]) for r in bb.reviews(n)] == [("renomear a classe", "bia")]
    n = az.open_pr("f", "t", "b")["number"]
    pr = fake.prs["azure"][n]
    pr["reviewers"] = [{"id": "r1", "uniqueName": "caio@empresa.com", "vote": -5}, {"id": "r2", "vote": 10}]
    pr["threads"] = [{"comments": [{"id": 1, "content": "tratar timeout", "author": {"id": "r1"}}]}]
    assert [(r["body"], r["user"]["login"]) for r in az.reviews(n)] == [("tratar timeout", "caio@empresa.com")]
    assert any(a.startswith("Basic ") and base64.b64decode(a[6:]) == b":pat-x" for a in fake.auth)


def test_story_publishes_merge_request_on_gitlab(  # type: ignore[no-untyped-def]
    planned, nexo: Runner, scenario, fake: FakeScm, memory_keyring, tmp_path: Path
) -> None:
    root = planned(scenario, [QA, impl("T-1-1"), impl("T-1-2", path="README.md"), *OK_REVIEWS])
    memory_keyring.store[("nexo-cli", "gitlab")] = "glpat-x"
    for key, value in (
        ("scm.provider", "gitlab"),
        ("scm.repo", "pagamentos/estornos"),
        ("scm.base_url", "https://gitlab.empresa.com"),
    ):
        assert nexo("config", "set", key, value, "--project")[0] == 0
    remote = tmp_path / "remote.git"
    git_cmd(tmp_path, "init", "-q", "--bare", str(remote))
    git_cmd(root, "remote", "add", "origin", str(remote))
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert code == 0 and rep["state"] == "Concluido", rep
    (mr,) = fake.prs["gitlab"].values()
    assert mr["source_branch"] == "nexo/story/US-1-1" and mr["title"].startswith("US-1-1")
    assert git_cmd(tmp_path, "--git-dir", str(remote), "branch", "--list", "nexo/story/US-1-1")
    logs = json.loads(nexo("--json", "logs", "-n", "300")[1])
    assert any(e["type"] == "scm.pr" and "merge_requests/1" in e["payload"]["pr"] for e in logs)


def test_incomplete_config_is_explained(project: Path, nexo: Runner) -> None:
    nexo("config", "set", "scm.provider", "azure", "--project")
    nexo("config", "set", "scm.repo", "estornos", "--project")
    from nexo.kernel.context import AppContext
    from nexo.kernel.errors import NexoError
    from nexo.modules.delivery.app import scm as delivery_scm

    ctx = AppContext(cwd=project)
    try:
        with pytest.raises(NexoError, match=r"scm\.org_url e scm\.project"):
            delivery_scm.build(ctx)
    finally:
        ctx.close()
