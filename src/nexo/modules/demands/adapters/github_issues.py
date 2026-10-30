"""GitHub Issues como tracker (labels para estado e tipo)."""

from __future__ import annotations

from typing import Any

from nexo.kernel.runtime import http
from nexo.kernel.tracker.mapping import Mapping, ProcessInfo
from nexo.modules.demands.adapters.base import ExternalRef, ExternalState, HistoricItem, TrackerItem

API = "https://api.github.com"


def _ext(mapping: Mapping, kind: str, default: str) -> str:
    """Tipo externo pelo de-para; sem de-para para o tipo, o nome padrão da ferramenta."""
    try:
        return mapping.external_type(kind)
    except KeyError:
        return default


class GitHubIssues:
    name = "github"

    def __init__(self, repo: str, token: str, mapping: Mapping) -> None:
        self.repo = repo
        self.mapping = mapping
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def create(self, item: TrackerItem) -> ExternalRef:
        target = self.mapping.target(item.state)
        body = item.description + (f"\n\nParte de #{item.parent_ref}" if item.parent_ref else "")
        labels = ["nexo", f"type:{self.mapping.external_type(item.type)}", *target.tags]
        resp = http.request(
            "POST",
            f"{API}/repos/{self.repo}/issues",
            self.headers,
            {"title": item.title, "body": body, "labels": labels},
        )
        number = str(resp.body["number"])
        if target.state == "closed":
            self.transition(number, item.state)
        return ExternalRef(number, str(resp.body.get("html_url", "")))

    def _issue(self, ref: str) -> dict[str, Any]:
        return dict(http.request("GET", f"{API}/repos/{self.repo}/issues/{ref}", self.headers).body)

    def transition(self, ref: str, nexo_state: str, reason: str = "") -> None:
        issue = self._issue(ref)
        labels = [str(label["name"]) for label in issue.get("labels", [])]
        target = self.mapping.target(nexo_state)
        patch: dict[str, Any] = {"labels": self.mapping.merge_tags(labels, nexo_state)}
        if target.state and target.state != issue.get("state"):
            patch["state"] = target.state
        http.request("PATCH", f"{API}/repos/{self.repo}/issues/{ref}", self.headers, patch)
        if reason:
            self.comment(ref, f"Nexo: {reason}")

    def comment(self, ref: str, text: str) -> None:
        http.request("POST", f"{API}/repos/{self.repo}/issues/{ref}/comments", self.headers, {"body": text})

    def fetch(self, refs: list[str]) -> list[ExternalState]:
        out = []
        for ref in refs:
            issue = self._issue(ref)
            labels = tuple(str(label["name"]) for label in issue.get("labels", []))
            out.append(ExternalState(ref, str(issue.get("state", "")), labels, str(issue.get("updated_at", ""))))
        return out

    def history(self, since: str, until: str) -> list[HistoricItem]:
        """Issues fechadas em [since, until); bug = label `bug` ou `type:bug`. GitHub não tem data de ativação."""
        out: list[HistoricItem] = []
        page = 1
        bug_label = f"type:{_ext(self.mapping, 'bug', 'bug')}"
        while True:
            batch = http.request(
                "GET",
                f"{API}/repos/{self.repo}/issues?state=closed&since={since}&per_page=100&page={page}",
                self.headers,
            ).body
            if not batch:
                break
            for issue in batch:
                closed = str(issue.get("closed_at") or "")
                if "pull_request" in issue or not (since <= closed < until):
                    continue
                labels = {str(label["name"]) for label in issue.get("labels", [])}
                out.append(
                    HistoricItem(
                        str(issue["number"]),
                        "bug" if labels & {"bug", bug_label} else "story",
                        str(issue.get("created_at", "")),
                        closed,
                        None,
                        None,
                        "nexo" in labels,
                    )
                )
            if len(batch) < 100:
                break
            page += 1
        return out

    def describe_process(self) -> ProcessInfo | None:
        return ProcessInfo("github", {t: ["open", "closed"] for t in self.mapping.types.values()})
