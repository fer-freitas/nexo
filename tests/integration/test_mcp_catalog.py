"""MCPs oficiais dos produtos: catálogo, aprovação da Segurança, data_policy, credencial fora da linha de
comando e somente leitura por padrão."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nexo.kernel.models.agent import AgentRequest, Capabilities
from nexo.kernel.security.policy import Action, Policy, evaluate, is_read_tool
from nexo.modules.execution.adapters.agents.fake import FakeAdapter
from tests.conftest import Runner, dump_yaml, edit

DONE = {"progress": {"percent": 100}}


@pytest.mark.parametrize(
    "tool,read",
    [
        ("mcp__atlassian__getJiraIssue", True),
        ("mcp__atlassian__searchJiraIssuesUsingJql", True),
        ("mcp__github__list_pull_requests", True),
        ("mcp__github__get_file_contents", True),
        ("mcp__atlassian__createJiraIssue", False),
        ("mcp__atlassian__transitionJiraIssue", False),
        ("mcp__github__merge_pull_request", False),
        ("mcp__slack__send_message", False),
        ("mcp__github__get_and_update_issue", False),  # verbo de escrita em qualquer lugar → escrita
        ("mcp__x__issues", False),  # sem verbo conhecido → não é leitura
    ],
)
def test_read_tool_detection(tool: str, read: bool) -> None:
    assert is_read_tool(tool) is read


def test_external_servers_are_read_only_unless_approved_to_write(tmp_path: Path) -> None:
    base = {"mcp": {"allowed_servers": ["nexo", "atlassian", "github"], "write_allowed": ["github"]}}
    policy = Policy.from_mapping(base)
    ws = str(tmp_path)
    assert evaluate(Action("mcp", "mcp__atlassian__getJiraIssue", mcp_server="atlassian"), ws, policy).allow
    denied = evaluate(Action("mcp", "mcp__atlassian__createJiraIssue", mcp_server="atlassian"), ws, policy)
    assert not denied.allow and denied.rule == "SEC-35"
    assert evaluate(Action("mcp", "mcp__github__merge_pull_request", mcp_server="github"), ws, policy).allow
    assert evaluate(Action("mcp", "mcp__nexo__update_progress", mcp_server="nexo"), ws, policy).allow
    assert not evaluate(Action("mcp", "mcp__slack__search", mcp_server="slack"), ws, policy).allow  # SEC-34


def approve(home: Path, *servers: str, write: tuple[str, ...] = ()) -> None:
    dump_yaml(home / "brain/org/tools.yaml", {"external_mcp": list(servers), "external_mcp_write": list(write)})


def test_catalog_lists_official_servers(project: Path, nexo: Runner) -> None:
    rows = {r["id"]: r for r in json.loads(nexo("--json", "mcp", "catalog")[1])}
    assert {"atlassian", "github", "azure-devops", "gitlab", "linear", "servicenow", "slack", "teams"} <= set(rows)
    assert {"azure", "aws", "vault", "datadog", "dynatrace", "newrelic", "splunk", "sonarqube"} <= set(rows)
    assert rows["teams"]["status"] == "preview" and not rows["github"]["enabled"]
    assert all(not r["verified"] for r in rows.values())  # V-08: conferir com conta real


def test_check_explains_what_is_missing(project: Path, nexo: Runner, nexo_home: Path, memory_keyring) -> None:  # type: ignore[no-untyped-def]
    nexo("config", "set", "mcp.servers.github.enabled", "true", "--project")
    nexo("config", "set", "mcp.servers.servicenow.enabled", "true", "--project")
    nexo("config", "set", "mcp.servers.nao-existe.enabled", "true", "--project")
    code, out, _ = nexo("--json", "mcp", "check")
    reasons = {(i["id"], i["reason"].split(" ")[0]) for i in json.loads(out)["issues"]}
    assert code != 0
    assert ("github", "ligado") in reasons and ("github", "sem") in reasons  # sem aprovação e sem credencial
    assert ("servicenow", "falta") in reasons  # URL da instância
    assert ("nao-existe", "não") in reasons
    approve(nexo_home, "github")
    memory_keyring.store[("nexo-cli", "mcp_github")] = "ghp_teste"
    nexo("config", "unset", "mcp.servers.servicenow.enabled", "--project")
    nexo("config", "unset", "mcp.servers.nao-existe.enabled", "--project")
    code, out, _ = nexo("--json", "mcp", "check")
    assert code == 0 and json.loads(out)["ok"], out
    assert nexo("login", "mcp_github")[0] != 2  # nome de credencial aceito pelo login


def test_run_receives_servers_without_secret_in_the_command(  # type: ignore[no-untyped-def]
    project: Path, nexo: Runner, nexo_home: Path, memory_keyring, scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[AgentRequest] = []
    original = FakeAdapter.start

    def start(self: FakeAdapter, req: AgentRequest, profile: str = "code-writer") -> Any:
        seen.append(req)
        return original(self, req, profile)

    monkeypatch.setattr(
        FakeAdapter, "capabilities", lambda self: Capabilities(verified=True, guard_proven=True, mcp=True)
    )
    monkeypatch.setattr(FakeAdapter, "start", start)
    approve(nexo_home, "github", "atlassian", "sonarqube")
    memory_keyring.store[("nexo-cli", "mcp_github")] = "ghp_segredo"
    memory_keyring.store[("nexo-cli", "mcp_atlassian")] = "atl_segredo"
    memory_keyring.store[("nexo-cli", "mcp_sonarqube")] = "sq_segredo"
    for server in ("github", "atlassian", "sonarqube"):
        nexo("config", "set", f"mcp.servers.{server}.enabled", "true", "--project")
    nexo("config", "set", "mcp.servers.sonarqube.vars.url", "https://sonar.exemplo.com", "--project")
    nexo("config", "set", "mcp.servers.sonarqube.vars.digest", "sha256:" + "a" * 64, "--project")
    scenario([{"tool": "claude", "events": [edit(), DONE]}])
    code, out, err = nexo("--json", "run", "task", "T-1")  # classe confidential: atlassian/sonarsource fora
    assert code == 0 and json.loads(out)["state"] == "Concluido", err or out
    servers = seen[0].mcp_config["mcpServers"]
    assert set(servers) == {"nexo", "github"}
    assert servers["github"] == {
        "type": "http",
        "url": "https://api.githubcopilot.com/mcp/",
        "headers": {"Authorization": "Bearer ${GITHUB_MCP_TOKEN}"},
    }
    assert "ghp_segredo" not in json.dumps(seen[0].mcp_config)
    assert seen[0].env["GITHUB_MCP_TOKEN"] == "ghp_segredo"

    seen.clear()
    nexo("config", "set", "data_class", "public", "--project")
    dump_yaml(project / ".nexo/tasks/T-2.yaml", {**json.loads(json.dumps(_task())), "id": "T-2"})
    scenario([{"tool": "claude", "task": "T-2", "events": [edit(), DONE]}])
    nexo("--json", "run", "task", "T-2")
    servers = seen[0].mcp_config["mcpServers"]
    assert set(servers) == {"nexo", "github", "atlassian", "sonarqube"}
    assert servers["sonarqube"]["env"] == {
        "SONARQUBE_URL": "https://sonar.exemplo.com",
        "SONARQUBE_TOKEN": "${SONARQUBE_MCP_TOKEN}",
    }
    assert seen[0].env["SONARQUBE_MCP_TOKEN"] == "sq_segredo"


def _task() -> dict[str, Any]:
    from tests.conftest import DEFAULT_TASK

    return dict(DEFAULT_TASK)
