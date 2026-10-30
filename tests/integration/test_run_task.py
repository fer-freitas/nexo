"""Orquestração de tarefas com o agente fake (sem tokens)."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from nexo.kernel.context import AppContext, Options
from nexo.modules.execution.app.run import Orchestrator, RunOptions
from tests.conftest import DEFAULT_TASK, Runner, dump_yaml, edit, git_cmd

DONE = {"progress": {"done": ["feito"], "percent": 100}}


def status(nexo: Runner, target: str = "T-1") -> dict:  # type: ignore[type-arg]
    return json.loads(nexo("--json", "status", target)[1])


def run_json(nexo: Runner, *args: str) -> tuple[int, dict]:  # type: ignore[type-arg]
    code, out, _ = nexo("--json", *args)
    return code, json.loads(out)


def test_success_single_commit(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    scenario([{"tool": "claude", "events": [edit(), DONE, {"usage": {"tokens_out": 10}}]}])
    code, rep = run_json(nexo, "run", "task", "T-1")
    assert code == 0 and rep["state"] == "Concluido" and rep["commit"]
    wt = Path(rep["worktree"])
    subjects = git_cmd(wt, "log", "--format=%s", "HEAD~1..HEAD").splitlines()
    assert subjects == ["feat(T-1): Adicionar função version()"]
    body = git_cmd(wt, "log", "-1", "--format=%B")
    assert "Nexo-Run: run-" in body and "Nexo-Routes: claude/sonnet" in body
    assert (wt / "src/svc/version.py").read_text(encoding="utf-8").endswith("'0.1.0'\n")
    # o checkout principal não é tocado
    assert not (project / "src/svc/version.py").exists()
    st = status(nexo)
    assert st["state"] == "Concluido" and st["runs"][0]["usage"][0]["unit"] == "tokens_out"
    code, _, err = nexo("run", "task", "T-1")
    assert code == 1 and "concluída" in err


def test_handoff_on_rate_limit(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    scenario(
        [
            {
                "tool": "claude",
                "events": [
                    edit(content="x = 1\n"),
                    {"progress": {"done": ["parcial"], "next_steps": ["terminar"], "percent": 40}},
                    {"rate_limited": {"reset_in": 3600}},
                ],
            },
            {"tool": "copilot", "events": [edit(), DONE]},
        ]
    )
    _code, rep = run_json(nexo, "run", "task", "T-1")
    assert rep["state"] == "Concluido" and rep["handoffs"] == 1
    assert rep["routes"] == ["claude/sonnet", "copilot/claude-sonnet-4.5"]
    run = status(nexo)["runs"][0]
    packet = Path(run["handoffs"][0]["packet"]).read_text(encoding="utf-8")
    assert "parcial" in packet and "terminar" in packet and "src/svc/version.py" in packet
    events = json.loads(nexo("--json", "logs", "-n", "200")[1])
    types = [e["type"] for e in events]
    assert {"hook.session_start", "hook.pre_send", "hook.post_step", "hook.on_limit", "run.handoff"} <= set(types)


def test_simulated_rate_limit_option(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    scenario([{"tool": "copilot", "events": [edit(), DONE]}])
    _code, rep = run_json(nexo, "run", "task", "T-1", "--simulate-rate-limit", "claude")
    assert rep["state"] == "Concluido" and rep["handoffs"] == 1


def test_all_routes_limited_blocks_then_resumes(project: Path, nexo: Runner, scenario, nexo_home: Path) -> None:  # type: ignore[no-untyped-def]
    scenario(
        [
            {"tool": "claude", "events": [edit(content="x=1\n"), {"rate_limited": {"reset_in": 600}}]},
            {"tool": "claude", "events": [edit(), DONE]},
        ],
        tools={"claude": {}},
    )
    code, rep = run_json(nexo, "run", "task", "T-1")
    assert rep["state"] == "Bloqueado" and rep["blocked_until"]
    code, rep = run_json(nexo, "resume", "T-1")
    assert rep["state"] == "Bloqueado" and "aguardando cota" in rep["reason"]
    # a cota renova
    db = sqlite3.connect(nexo_home / "nexo.db")
    db.execute("UPDATE route_state SET limited_until = ?", (time.time() - 1,))
    db.execute("UPDATE runs SET blocked_until = ?", (time.time() - 1,))
    db.commit()
    db.close()
    _code, rep = run_json(nexo, "resume", "T-1")
    assert rep["state"] == "Concluido"
    assert "x=1" not in (Path(rep["worktree"]) / "src/svc/version.py").read_text(encoding="utf-8")


def test_policy_violation_escalates(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    scenario(
        [{"tool": "claude", "events": [edit(), {"tool": {"name": "Bash", "input": {"command": "rm -rf /"}}}, DONE]}]
    )
    code, rep = run_json(nexo, "run", "task", "T-1")
    assert code == 1 and rep["state"] == "Escalado" and "SEC-21" in rep["reason"]
    events = json.loads(nexo("--json", "logs", "-n", "100")[1])
    assert any(e["type"] == "policy.violation" for e in events)


def test_resume_starts_a_fresh_denial_count(project: Path, nexo_home: Path) -> None:
    """F49 (achado no e2e Flutter): quem retoma já tratou a causa; as negações antigas não escalam de novo."""
    from nexo.modules.execution.app.hooks import denial_count

    ctx = AppContext(cwd=project, home=nexo_home, options=Options())
    for _ in range(3):
        ctx.audit("policy.violation", {"rule": "SEC-21"}, ref="run-x")
    assert denial_count(ctx, "run-x") == 3
    ctx.audit("run.resume", {"task": "T-1", "run": "run-x"}, ref="run-x")
    assert denial_count(ctx, "run-x") == 0
    ctx.audit("policy.violation", {"rule": "SEC-21"}, ref="run-x")
    assert denial_count(ctx, "run-x") == 1
    ctx.close()


def test_forbidden_file_change_escalates(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    scenario([{"tool": "claude", "events": [edit(".env", "SECRET=1\n"), DONE]}])
    _code, rep = run_json(nexo, "run", "task", "T-1")
    assert rep["state"] == "Escalado" and "SEC-13" in rep["reason"]


def test_restricted_data_class_requires_human(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    scenario([{"tool": "claude", "events": [edit(), DONE]}])
    dump_yaml(project / ".nexo/tasks/T-1.yaml", {**DEFAULT_TASK, "data_class": "restricted"})
    _code, rep = run_json(nexo, "run", "task", "T-1")
    assert rep["state"] == "Escalado" and "execução humana" in rep["reason"]


def test_technical_errors_retry_then_switch(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    scenario(
        [
            {"tool": "claude", "events": [{"error": {"kind": "technical", "message": "boom"}}]},
            {"tool": "claude", "events": [{"error": {"kind": "overloaded", "message": "529"}}]},
            {"tool": "copilot", "events": [edit(), DONE]},
        ]
    )
    _code, rep = run_json(nexo, "run", "task", "T-1")
    assert rep["state"] == "Concluido"
    assert rep["routes"] == ["claude/sonnet", "claude/sonnet", "copilot/claude-sonnet-4.5"]
    assert rep["handoffs"] == 1


def test_auth_error_excludes_tool(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    scenario(
        [
            {"tool": "claude", "events": [{"error": {"kind": "auth", "message": "login"}}]},
            {"tool": "copilot", "events": [edit(), DONE]},
        ]
    )
    _code, rep = run_json(nexo, "run", "task", "T-1")
    assert rep["state"] == "Concluido" and rep["routes"][-1].startswith("copilot")


def test_context_limit_switches_route(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    scenario(
        [
            {"tool": "claude", "events": [{"error": {"kind": "context_limit", "message": "too long"}}]},
            {"tool": "copilot", "events": [edit(), DONE]},
        ]
    )
    _code, rep = run_json(nexo, "run", "task", "T-1")
    assert rep["state"] == "Concluido"


def test_no_changes_counts_as_failure(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    scenario(
        [
            {"tool": "claude", "events": [DONE]},
            {"tool": "claude", "events": [DONE]},
            {"tool": "copilot", "events": [edit(), DONE]},
        ]
    )
    _code, rep = run_json(nexo, "run", "task", "T-1")
    assert rep["state"] == "Concluido" and len(rep["routes"]) == 3


def test_blocker_reported_escalates(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    scenario([{"tool": "claude", "events": [edit(), {"progress": {"blockers": ["critério ambíguo"], "percent": 10}}]}])
    code, rep = run_json(nexo, "run", "task", "T-1")
    assert rep["state"] == "Escalado" and "critério ambíguo" in rep["reason"]
    # humano decide continuar: resume a partir de Escalado
    scenario([{"tool": "claude", "events": [edit(), DONE]}])
    _code, rep = run_json(nexo, "resume", "T-1")
    assert rep["state"] == "Concluido"


def test_max_handoffs(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    nexo("config", "set", "handoff.max_per_task", "1")
    scenario(
        [
            {"tool": "claude", "events": [{"rate_limited": {"reset_in": 60}}]},
            {"tool": "copilot", "events": [{"rate_limited": {"reset_in": 60}}]},
        ]
    )
    _code, rep = run_json(nexo, "run", "task", "T-1")
    assert rep["state"] == "Escalado" and "handoffs" in rep["reason"]


def test_max_steps(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    nexo("config", "set", "agents.max_steps", "1")
    scenario([{"tool": "claude", "events": [{"error": {"kind": "technical"}}]}])
    _code, rep = run_json(nexo, "run", "task", "T-1")
    assert rep["state"] == "Escalado" and "SEC-60" in rep["reason"]


def test_interrupt_and_resume(project: Path, scenario, nexo: Runner, nexo_home: Path) -> None:  # type: ignore[no-untyped-def]
    scenario(
        [
            {"tool": "claude", "events": [edit(content="parcial = True\n"), {"sleep": {"seconds": 5}}, DONE]},
            {"tool": "claude", "events": [edit(), DONE]},
        ]
    )
    ctx = AppContext(cwd=project, home=nexo_home, options=Options())

    def ui(kind: str, data: dict) -> None:  # type: ignore[type-arg]
        if kind == "file":
            raise KeyboardInterrupt

    rep = Orchestrator(ctx, RunOptions(), ui).run_task("T-1")
    assert rep.state == "Bloqueado" and "interrompido" in rep.reason
    wt = Path(rep.worktree or "")
    assert "nexo: checkpoint (interrompido)" in git_cmd(wt, "log", "-1", "--format=%s")
    ctx.close()
    _code, out = run_json(nexo, "resume", "T-1")
    assert out["state"] == "Concluido"
    assert status(nexo)["runs"][0]["handoffs"] == []  # retomada não conta como handoff


def test_dry_run_does_not_touch_state(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    scenario([{"tool": "claude", "events": [edit(), DONE]}])
    code, rep = run_json(nexo, "--dry-run", "run", "task", "T-1")
    assert rep["decision"]["route"] == "claude/sonnet" and rep["state"] == "Pronto"
    assert rep["decision"]["candidates"] and "devin/default" in rep["decision"]["rejected"]
    assert not (project / ".nexo/worktrees").exists()
    _code, out, _ = nexo("--verbose", "--dry-run", "run", "task", "T-1")
    assert "rota escolhida" in out and "p(sucesso)" in out


def test_llm_classifier_used_when_rules_fail(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    task = {k: v for k, v in DEFAULT_TASK.items() if k != "kind"}
    dump_yaml(project / ".nexo/tasks/T-1.yaml", {**task, "title": "Algo vago", "description": "", "acceptance": []})
    scenario(
        [
            {"tool": "claude", "events": [{"done": {"structured": {"kind": "backend", "size": "S"}}}]},
            {"tool": "claude", "events": [edit(), DONE]},
        ]
    )
    _code, rep = run_json(nexo, "run", "task", "T-1")
    assert rep["state"] == "Concluido"
    run = status(nexo)["runs"][0]
    assert run["classification"]["source"] == "llm"


def test_requires_project_git_and_commit(workdir: Path, nexo: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    code, _, err = nexo("run", "task", "T-1")
    assert code == 1 and "nexo init" in err
    root = workdir / "p"
    root.mkdir()
    monkeypatch.chdir(root)
    nexo("init")
    code, _, err = nexo("run", "task", "T-1")
    assert "repositório git" in err
    git_cmd(root, "init", "-q")
    dump_yaml(root / ".nexo/tasks/T-1.yaml", DEFAULT_TASK)
    monkeypatch.setenv("NX_FAKE_SCENARIO", str(dump_yaml(workdir / "s.yaml", {"tools": {"claude": {}}, "steps": []})))
    code, _, err = nexo("run", "task", "T-1")
    assert "commits" in err
    code, _, err = nexo("run", "task", "T-9")
    assert "não encontrada" in err


def test_invalid_task_file(project: Path, nexo: Runner) -> None:
    dump_yaml(project / ".nexo/tasks/T-1.yaml", {"title": "x", "kind": "weird"})
    code, _, err = nexo("run", "task", "T-1")
    assert code == 1 and "inválida" in err


def test_status_views(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    scenario([{"tool": "claude", "events": [edit(), DONE]}])
    nexo("run", "task", "T-1")
    code, out, _ = nexo("status")
    assert "T-1" in out and "Concluido" in out
    code, out, _ = nexo("--verbose", "status", "T-1")
    assert "claude/sonnet" in out
    code, _, err = nexo("status", "nope")
    assert code == 1
    code, _, _err = nexo("resume", "nope")
    assert code == 1


def test_run_task_ui_output(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    scenario(
        [
            {
                "tool": "claude",
                "events": [
                    edit(),
                    {"quota": {"utilization": 0.5}},
                    {"message": {"text": "ok"}},
                    {"rate_limited": {"reset_in": 60}},
                ],
            },
            {"tool": "copilot", "events": [edit(), DONE]},
        ]
    )
    _code, out, _ = nexo("--verbose", "run", "task", "T-1")
    assert "Pronto → EmDev" in out and "handoff de claude/sonnet" in out and "Concluido" in out
    assert "✎ src/svc/version.py" in out


def test_simulated_limit_is_not_global(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    dump_yaml(project / ".nexo/tasks/T-2.yaml", DEFAULT_TASK)
    scenario([{"tool": "copilot", "events": [edit(), DONE]}, {"tool": "claude", "events": [edit(), DONE]}])
    _, rep = run_json(nexo, "run", "task", "T-1", "--simulate-rate-limit", "claude")
    assert rep["routes"][-1].startswith("copilot")
    _, rep = run_json(nexo, "run", "task", "T-2")
    assert rep["state"] == "Concluido" and rep["routes"] == ["claude/sonnet"]


def test_pre_commit_blocks_secret_in_code(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    scenario(
        [
            {
                "tool": "claude",
                "events": [edit(content='API_TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz0123456789ABCD"\n'), DONE],
            }
        ]
    )
    _, rep = run_json(nexo, "run", "task", "T-1")
    assert rep["state"] == "Escalado" and "segredo" in rep["reason"]
    wt = project / ".nexo/worktrees/T-1"
    assert "ghp_" not in git_cmd(wt, "log", "-p", "--all")


def test_pre_commit_allows_contact_email(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    scenario([{"tool": "claude", "events": [edit(content='SUPPORT = "suporte@empresa.com.br"\n'), DONE]}])
    _, rep = run_json(nexo, "run", "task", "T-1")
    assert rep["state"] == "Concluido"


def test_relative_nexo_home_is_made_absolute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nexo.kernel.context import nexo_home

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NX_HOME", "../home")
    assert nexo_home() == (tmp_path.parent / "home").resolve() and nexo_home().is_absolute()
