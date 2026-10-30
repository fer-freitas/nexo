"""`nexo mcp serve` (JSON-RPC por stdio) e `nexo hook run pre-tool-policy` (protocolo de hook do Claude)."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from nexo.kernel.context import AppContext, Options
from nexo.modules.execution.app import agents as agents_app
from nexo.modules.execution.app.hooks import run_pre_tool_policy
from nexo.modules.execution.app.run import Orchestrator, RunOptions
from tests.conftest import Runner, edit


@pytest.fixture
def run_id(project: Path, nexo_home: Path, scenario) -> str:  # type: ignore[no-untyped-def]
    scenario([{"tool": "claude", "events": [edit(), {"progress": {"blockers": ["parar"], "percent": 5}}]}])
    ctx = AppContext(cwd=project, home=nexo_home, options=Options())
    rep = Orchestrator(ctx, RunOptions()).run_task("T-1")
    ctx.close()
    assert rep.run_id
    return rep.run_id


def mcp_session(nexo_home: Path, run_id: str, messages: list[dict]) -> list[dict]:  # type: ignore[type-arg]
    env = {**os.environ, "NX_HOME": str(nexo_home)}
    payload = "\n".join(json.dumps(m) for m in messages) + "\n"
    proc = subprocess.run(
        [sys.executable, "-m", "nexo", "mcp", "serve", "--run", run_id],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, proc.stderr
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def test_mcp_serve_protocol(run_id: str, nexo_home: Path, project: Path) -> None:
    responses = mcp_session(
        nexo_home,
        run_id,
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "get_task_context", "arguments": {"section": "acceptance"}},
            },
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "search_brain", "arguments": {"query": "tipagem mypy python"}},
            },
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "report_progress", "arguments": {"done": ["x"], "percent": 100}},
            },
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {"name": "get_task_context", "arguments": {"section": "bogus"}},
            },
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {"name": "get_task_context", "arguments": {}},
            },
            {
                "jsonrpc": "2.0",
                "id": 8,
                "method": "tools/call",
                "params": {"name": "request_clarification", "arguments": {"question": "Formato?", "blocking": False}},
            },
        ],
    )
    by_id = {r["id"]: r for r in responses}
    assert by_id[1]["result"]["serverInfo"]["name"] == "nexo"
    assert {t["name"] for t in by_id[2]["result"]["tools"]} == {
        "get_task_context",
        "search_brain",
        "report_progress",
        "request_clarification",
        "run_checks",
        "record_decision",
        "check_dependency",
    }
    assert "version() retorna 0.1.0" in by_id[3]["result"]["structuredContent"]["content"]
    assert by_id[4]["result"]["structuredContent"]["results"][0]["title"] == "python-standards"
    assert by_id[5]["result"]["structuredContent"]["ok"]
    assert by_id[6]["result"]["isError"]
    assert by_id[8]["result"]["structuredContent"]["status"] == "queued"
    assert set(by_id[7]["result"]["structuredContent"]["refs"]) == {
        "task",
        "acceptance",
        "files",
        "handoff",
        "standards",
    }


def test_mcp_serve_unknown_run(nexo: Runner) -> None:
    code, _, err = nexo("mcp", "serve", "--run", "run-nope")
    assert code == 1 and "não encontrada" in err


def claude_hook(nexo_home: Path, run_id: str, tool: str, tool_input: dict) -> str:  # type: ignore[type-arg]
    payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input}
    env = {**os.environ, "NX_HOME": str(nexo_home)}
    proc = subprocess.run(
        [sys.executable, "-m", "nexo", "hook", "run", "pre-tool-policy", "--run", run_id],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_hook_denies_forbidden_action(run_id: str, nexo_home: Path) -> None:
    out = json.loads(claude_hook(nexo_home, run_id, "Read", {"file_path": "/Users/x/.ssh/id_rsa"}))
    decision = out["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny" and "SEC-22" in decision["permissionDecisionReason"]


def test_hook_allows_worktree_action(run_id: str, nexo_home: Path) -> None:
    assert claude_hook(nexo_home, run_id, "Bash", {"command": "pytest -q"}) == ""


def test_hook_unknown_run_and_bad_input(nexo_home: Path, project: Path) -> None:
    ctx = AppContext(cwd=project, home=nexo_home, options=Options())
    out = io.StringIO()
    run_pre_tool_policy(ctx, "run-x", io.StringIO("not json"), out)
    assert "deny" in out.getvalue()
    ctx.close()


def test_hook_cli_unknown_name(nexo: Runner) -> None:
    code, _, err = nexo("hook", "run", "other", "--run", "x")
    assert code == 1 and "desconhecido" in err


def test_agents_helpers(nexo_home: Path, project: Path) -> None:
    ctx = AppContext(cwd=project, home=nexo_home, options=Options())
    cfg = agents_app.mcp_config(ctx, "run-1")
    server = cfg["mcpServers"]["nexo"]
    assert server["args"][-3:] == ["serve", "--run", "run-1"] and server["env"]["NX_HOME"] == str(nexo_home)
    hook = agents_app.hook_settings(ctx, "run-1")["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert "pre-tool-policy" in hook and "run-1" in hook
    assert agents_app.shell_join(["a b", "c"]) in ("'a b' c", '"a b" c')
    ctx.close()


def test_doctor_reports_agent_auth(nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    _code, out, _ = nexo("--json", "doctor")
    names = {c["name"] for c in json.loads(out)["checks"]}
    assert "python" in names


def test_f3_tools_in_process(
    run_id: str, nexo_home: Path, project: Path, fast_recipe: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nexo.kernel.dependencies import registries
    from nexo.modules.execution.app.mcp_server import build_server

    ctx = AppContext(cwd=project, home=nexo_home, options=Options())
    server = build_server(ctx, run_id)

    def call(name: str, args: dict) -> dict:  # type: ignore[type-arg]
        res = server.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
        )
        return res["result"]  # type: ignore[index]

    checks = call("run_checks", {"stages": ["lint", "unit"]})["structuredContent"]
    # as etapas de preparo do ambiente (setup) rodam sempre junto
    assert {s["stage"] for s in checks["summary"]} - {"venv", "install", "install-project"} == {"lint", "unit"}
    dec = call(
        "record_decision", {"title": "Cache", "decision": "usar lru_cache", "context": "x", "alternatives": ["redis"]}
    )
    assert dec["structuredContent"]["id"].startswith(run_id)
    assert "usar lru_cache" in (project / ".nexo" / "runs" / run_id / "decisions.md").read_text(encoding="utf-8")

    monkeypatch.setattr(
        registries,
        "package_info",
        lambda eco, name: {"exists": True, "latest": "2.0", "license": "MIT License", "age_days": 900},
    )
    monkeypatch.setattr(registries, "vulnerabilities", lambda eco, name, v: [{"id": "GHSA-1", "severity": "high"}])
    dep = call("check_dependency", {"ecosystem": "pypi", "name": "requests"})["structuredContent"]
    assert dep["exists"] and dep["license_allowed"] and dep["requires_human_gate"] and dep["vulnerabilities"]
    monkeypatch.setattr(registries, "package_info", lambda eco, name: {"exists": False})
    ghost = call("check_dependency", {"ecosystem": "npm", "name": "pacote-alucinado"})["structuredContent"]
    assert ghost["exists"] is False and ghost["vulnerabilities"] == []
    ctx.close()


def test_registries_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    from nexo.kernel.dependencies import registries

    pypi = {
        "info": {"version": "2.0", "license_expression": "Apache-2.0"},
        "releases": {
            "1.0": [{"upload_time_iso_8601": "2015-01-01T00:00:00Z"}],
            "2.0": [{"upload_time_iso_8601": "2024-01-01T00:00:00Z"}],
        },
    }
    responses = {
        "https://pypi.org/pypi/requests/json": pypi,
        "https://registry.npmjs.org/left-pad": {
            "dist-tags": {"latest": "1.3.0"},
            "license": "WTFPL",
            "time": {"created": "2014-03-14T00:00:00.000Z"},
        },
        "https://api.nuget.org/v3-flatcontainer/newtonsoft.json/index.json": {"versions": ["12.0.1", "13.0.3"]},
    }
    monkeypatch.setattr(registries, "_get", lambda url: responses.get(url))
    info = registries.package_info("pypi", "requests")
    assert info["latest"] == "2.0" and info["license"] == "Apache-2.0" and info["age_days"] > 3000
    assert registries.package_info("npm", "left-pad")["license"] == "WTFPL"
    assert registries.package_info("nuget", "Newtonsoft.Json")["latest"] == "13.0.3"
    assert registries.package_info("pypi", "nao-existe") == {"exists": False}
    with pytest.raises(ValueError):
        registries.package_info("maven", "x")
    monkeypatch.setattr(
        registries,
        "_post",
        lambda url, body: {"vulns": [{"id": "PYSEC-1", "database_specific": {"severity": "HIGH"}}, {"id": "X"}]},
    )
    assert registries.vulnerabilities("pypi", "x", "1.0") == [
        {"id": "PYSEC-1", "severity": "high"},
        {"id": "X", "severity": "unknown"},
    ]
