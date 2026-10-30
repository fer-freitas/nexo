"""segundo cérebro assinado e extensões transpiladas para as ferramentas."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from nexo.kernel.runtime import http
from tests.conftest import DEFAULT_TASK, Runner, dump_yaml, edit, git_cmd
from tests.fakes.http_fake import FakeCloud

SKILL = """---
name: payments-domain
description: Regras do domínio de pagamentos — estorno parcial, limites e idempotência.
---
# Pagamentos
- Estorno parcial nunca excede o valor capturado.
- Toda operação financeira é idempotente por chave de requisição.
- Valores monetários sempre em centavos (inteiros).
"""


@pytest.fixture
def cloud() -> Iterator[FakeCloud]:
    fake = FakeCloud()
    http.set_transport(fake)
    yield fake
    http.set_transport(None)


@pytest.fixture
def tools_home(tmp_path: Path, nexo: Runner) -> Path:
    home = tmp_path / "user-home"
    nexo("config", "set", "agents.claude.home", str(home / ".claude"))
    nexo("config", "set", "agents.copilot.home", str(home / ".copilot"))
    return home


def make_brain(tmp_path: Path, nexo: Runner, key: Path) -> tuple[Path, str]:
    repo = tmp_path / "nexo-brain"
    assert nexo("brain", "init", str(repo))[0] == 0
    assert (repo / "CODEOWNERS").is_file() and (repo / "org/config.yaml").is_file()
    code, out, _ = nexo("brain", "keygen", str(key))
    pub = out.strip().splitlines()[-1].split(": ")[-1]
    dump_yaml(repo / "org/config.yaml", {"max_parallel": 2, "locked": ["max_parallel"]})
    skill = repo / "extensions/skills/secure-payments"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(SKILL, encoding="utf-8")
    dump_yaml(
        skill / "extension.yaml",
        {
            "name": "secure-payments",
            "type": "skill",
            "scope": "org",
            "locked": True,
            "description": "Regras de pagamentos da org",
        },
    )
    assert nexo("brain", "sign", str(repo), "--key", str(key), "--version", "2026.09.24-1")[0] == 0
    git_cmd(repo, "add", "-A")
    git_cmd(repo, "commit", "-qm", "brain inicial")
    return repo, pub


def configure(nexo: Runner, repo: Path, pub: str) -> None:
    nexo("config", "set", "brain.auto_pull", "false")  # pulls explícitos: sem corrida com o pull em segundo plano
    nexo("config", "set", "brain.repo", str(repo))
    nexo("config", "set", "brain.trusted_keys", f"[{pub}]")


def test_brain_signed_pull_and_tamper_refused(workdir: Path, nexo: Runner, tmp_path: Path, nexo_home: Path) -> None:
    repo, pub = make_brain(tmp_path, nexo, tmp_path / "brain.key")
    configure(nexo, repo, pub)
    code, out, _ = nexo("--json", "brain", "pull")
    data = json.loads(out)
    assert code == 0 and data["verified"] and data["version"] == "2026.09.24-1"
    status = json.loads(nexo("--json", "brain", "status")[1])
    assert status["verified"] and status["require_signature"] and status["trusted_keys"] == 1
    assert json.loads(nexo("--json", "config", "get", "max_parallel")[1])["locked_by"] == "org"
    # alguém altera o cérebro sem reassinar → recusado; cache anterior continua valendo
    dump_yaml(repo / "org/config.yaml", {"max_parallel": 16})
    git_cmd(repo, "commit", "-qam", "mudança sem assinatura")
    code, _, err = nexo("brain", "pull")
    assert code == 7 and "difere do manifesto" in err
    assert json.loads(nexo("--json", "config", "get", "max_parallel")[1])["value"] == 2
    # chave errada
    nexo("config", "set", "brain.trusted_keys", "[AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=]")
    git_cmd(repo, "revert", "--no-edit", "HEAD")
    code, _, err = nexo("brain", "pull")
    assert code == 7 and "não confiável" in err
    logs = json.loads(nexo("--json", "logs", "-n", "100")[1])
    assert any(e["type"] == "brain.pull_failed" for e in logs)


def test_brain_unsigned_dev_mode_offline_and_search(workdir: Path, nexo: Runner, tmp_path: Path) -> None:
    repo = tmp_path / "brain-dev"
    nexo("brain", "init", str(repo))
    nexo("config", "set", "brain.auto_pull", "false")
    (repo / "org/standards/nomes.md").write_text(
        "# Nomenclatura\nServiços terminam com Service; filas com Queue.\n", encoding="utf-8"
    )
    git_cmd(repo, "add", "-A")
    git_cmd(repo, "commit", "-qm", "x")
    nexo("config", "set", "brain.repo", str(repo))
    data = json.loads(nexo("--json", "brain", "pull")[1])
    assert data["verified"] is False and "sem manifesto" in data["reason"]
    results = json.loads(nexo("--json", "brain", "search", "nomenclatura filas Queue")[1])
    assert results[0]["path"] == "org/standards/nomes.md"
    nexo("config", "set", "brain.repo", str(tmp_path / "nao-existe"))
    code, _, err = nexo("brain", "pull")
    assert code == 1 and "não foi possível baixar" in err
    assert json.loads(nexo("--json", "brain", "status")[1])["present"]  # cache offline preservado


def test_auto_pull_is_background_and_throttled(
    workdir: Path, nexo: Runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, nexo_home: Path
) -> None:
    import subprocess

    spawned: list[list[str]] = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: spawned.append(args))
    nexo("config", "set", "brain.repo", str(tmp_path / "x"))
    nexo("config", "list")
    nexo("config", "list")
    assert len(spawned) == 1 and spawned[0][-2:] == ["brain", "pull"]
    assert (nexo_home / "brain.last_pull").is_file()


def test_skill_add_transpiles_and_opens_issue(
    project: Path, nexo: Runner, tools_home: Path, cloud: FakeCloud, memory_keyring, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    memory_keyring.store[("nexo-cli", "github")] = cloud.github_token
    nexo("config", "set", "brain.github_repo", "empresa/nexo-brain")
    src = tmp_path / "payments-domain" / "SKILL.md"
    src.parent.mkdir()
    src.write_text(SKILL, encoding="utf-8")
    code, out, _ = nexo("--json", "skill", "add", str(src))
    rep = json.loads(out)
    assert code == 0 and rep["key"] == "skill:payments-domain" and rep["scope"] == "project"
    assert rep["installed"] == {"claude": ["payments-domain"], "copilot": ["payments-domain"]}
    assert rep["emulated"] == {"devin": ["payments-domain"]}
    assert (project / ".nexo/extensions/skills/payments-domain/extension.yaml").is_file()
    assert (project / ".claude/skills/payments-domain/SKILL.md").is_file()
    assert (project / ".github/skills/payments-domain/SKILL.md").is_file()
    issue = cloud.issues[1]
    assert issue["title"] == "Nova skill: payments-domain (project)" and "Promover" in issue["body"]
    assert {"extensao", "skill", "escopo:project"} <= {x["name"] for x in issue["labels"]}
    # uma segunda skill parecida é sinalizada
    similar = tmp_path / "refunds" / "SKILL.md"
    similar.parent.mkdir()
    similar.write_text(
        SKILL.replace("payments-domain", "refunds").replace("idempotente", "idempotente e auditada"), encoding="utf-8"
    )
    rep2 = json.loads(nexo("--json", "skill", "add", str(similar), "--name", "refunds")[1])
    assert rep2["similar"][0]["key"] == "skill:payments-domain" and rep2["similar"][0]["similarity"] >= 0.5
    assert "Similar a `skill:payments-domain`" in cloud.issues[2]["body"]
    listing = json.loads(nexo("--json", "skill", "list")[1])
    assert {s["name"] for s in listing} >= {"payments-domain", "refunds"}
    shown = json.loads(nexo("--json", "skill", "show", "refunds")[1])
    assert "auditada" in shown["content"]
    code, out, _ = nexo("skill", "remove", "refunds")
    assert code == 0 and not (project / ".claude/skills/refunds").exists()
    assert nexo("skill", "remove", "refunds")[0] == 1
    found = json.loads(nexo("--json", "brain", "search", "estorno parcial centavos")[1])
    assert found and "payments-domain" in found[0]["path"]


def test_skill_with_secret_is_blocked(project: Path, nexo: Runner, tmp_path: Path, tools_home: Path) -> None:
    src = tmp_path / "leaky" / "SKILL.md"
    src.parent.mkdir()
    src.write_text(
        "---\nname: leaky\ndescription: x\n---\nUse o token ghp_abcdefghijklmnopqrstuvwxyz0123456789ABCD\n",
        encoding="utf-8",
    )
    code, _, err = nexo("skill", "add", str(src))
    assert code == 5 and "segredo" in err
    assert not (project / ".nexo/extensions/skills/leaky").exists()


def test_org_scope_is_proposal_and_issue_queue(
    project: Path, nexo: Runner, tmp_path: Path, tools_home: Path, cloud: FakeCloud, memory_keyring
) -> None:  # type: ignore[no-untyped-def]
    nexo("config", "set", "brain.github_repo", "empresa/nexo-brain")
    src = tmp_path / "org-skill" / "SKILL.md"
    src.parent.mkdir()
    src.write_text(SKILL, encoding="utf-8")
    rep = json.loads(nexo("--json", "skill", "add", str(src), "--scope", "org")[1])
    assert rep["proposal_only"] and rep["issue"] is None  # sem credencial → fila
    assert not (project / ".nexo/extensions/skills/org-skill").exists()
    memory_keyring.store[("nexo-cli", "github")] = cloud.github_token
    from nexo.kernel.context import AppContext, Options
    from nexo.modules.knowledge.app import brain

    ctx = AppContext(cwd=project, options=Options())
    assert brain.retry_issues(ctx) == (1, 0)
    ctx.close()
    assert cloud.issues[1]["title"].endswith("(org)")


def test_hook_and_tool_transpile(project: Path, nexo: Runner, tmp_path: Path, tools_home: Path) -> None:
    hook = tmp_path / "no-print.py"
    hook.write_text(
        "import json, sys\npayload = json.load(sys.stdin)\n"
        "cmd = str((payload.get('input') or {}).get('command', ''))\n"
        "print(json.dumps({'decision': 'deny' if 'print(' in cmd else 'allow', 'reason': 'sem print'}))\n",
        encoding="utf-8",
    )
    code, out, _ = nexo("--json", "hook", "add", str(hook), "--event", "pre_tool", "--matcher", "Bash")
    rep = json.loads(out)
    assert code == 0 and rep["installed"] == {"claude": ["no-print"]} and set(rep["emulated"]) == {"copilot", "devin"}
    settings = json.loads((project / ".claude/settings.json").read_text(encoding="utf-8"))
    entry = settings["hooks"]["PreToolUse"][0]
    assert entry["matcher"] == "Bash" and "hook exec hook:no-print --claude" in entry["hooks"][0]["command"]
    mcp = tmp_path / "jira-mcp.json"
    mcp.write_text(json.dumps({"command": "npx", "args": ["-y", "mcp-jira"]}), encoding="utf-8")
    rep = json.loads(nexo("--json", "tool", "add", str(mcp), "--name", "jira")[1])
    assert rep["installed"] == {"claude": ["jira"], "copilot": ["jira"]} and rep["unavailable"] == {"devin": ["jira"]}
    assert "jira" in json.loads((project / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert "jira" in json.loads((tools_home / ".copilot/mcp-config.json").read_text(encoding="utf-8"))["mcpServers"]
    sync = json.loads(nexo("--json", "hook", "sync")[1])
    assert "no-print" in sync["installed"]["claude"]
    assert nexo("hook", "add", str(hook))[0] == 1  # hook avulso sem --event
    assert nexo("tool", "remove", "jira")[0] == 0
    assert "jira" not in json.loads((project / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]


def test_hook_exec_claude_protocol(
    project: Path,
    nexo: Runner,
    tmp_path: Path,
    tools_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import io

    hook = tmp_path / "deny-all.py"
    hook.write_text('print(\'{"decision": "deny", "reason": "bloqueado pelo time"}\')\n', encoding="utf-8")
    nexo("hook", "add", str(hook), "--event", "pre_tool")
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": str(project)})),
    )
    code, out, _ = nexo("hook", "exec", "hook:deny-all", "--claude")
    data = json.loads(out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert nexo("hook", "exec", "hook:nope")[0] == 1


def test_locked_org_extension_cannot_be_overridden(
    project: Path, nexo: Runner, tmp_path: Path, tools_home: Path
) -> None:
    repo, pub = make_brain(tmp_path, nexo, tmp_path / "k.key")
    configure(nexo, repo, pub)
    nexo("brain", "pull")
    src = tmp_path / "secure-payments" / "SKILL.md"
    src.parent.mkdir()
    src.write_text(SKILL, encoding="utf-8")
    code, _, err = nexo("skill", "add", str(src), "--name", "secure-payments")
    assert code == 5 and "travada" in err
    listing = json.loads(nexo("--json", "skill", "list")[1])
    assert next(s for s in listing if s["name"] == "secure-payments")["scope"] == "org"


def test_emulated_hooks_in_orchestrator(
    project: Path, nexo: Runner, tmp_path: Path, tools_home: Path, scenario
) -> None:  # type: ignore[no-untyped-def]
    deny = tmp_path / "no-rm.py"
    deny.write_text(
        "import json, sys\np = json.load(sys.stdin)\n"
        "c = str((p.get('input') or {}).get('command', ''))\n"
        "print(json.dumps({'decision': 'deny' if c.startswith('pytest') else 'allow', 'reason': 'sem pytest'}))\n",
        encoding="utf-8",
    )
    nexo("hook", "add", str(deny), "--event", "pre_tool")
    scenario([{"tool": "claude", "events": [edit(), {"tool": {"name": "Bash", "input": {"command": "pytest -q"}}}]}])
    code, out, _ = nexo("--json", "run", "task", "T-1")
    rep = json.loads(out)
    assert rep["state"] == "Escalado" and "no-rm" in rep["reason"]


def test_task_done_hook_can_reject(project: Path, nexo: Runner, tmp_path: Path, tools_home: Path, scenario) -> None:  # type: ignore[no-untyped-def]
    gate = tmp_path / "exige-changelog.py"
    gate.write_text(
        "import json, sys, pathlib\np = json.load(sys.stdin)\n"
        "ok = (pathlib.Path(p['worktree']) / 'CHANGELOG.md').exists()\n"
        "print(json.dumps({'decision': 'allow' if ok else 'deny', 'reason': 'CHANGELOG obrigatório'}))\n",
        encoding="utf-8",
    )
    nexo("hook", "add", str(gate), "--event", "task_done")
    scenario([{"tool": "claude", "events": [edit(), {"progress": {"percent": 100}}]}])
    rep = json.loads(nexo("--json", "run", "task", "T-1")[1])
    assert rep["state"] == "Escalado" and "CHANGELOG" in rep["reason"]
    dump_yaml(project / ".nexo/tasks/T-2.yaml", DEFAULT_TASK)
    scenario([{"tool": "claude", "events": [edit(), edit("CHANGELOG.md", "# x\n"), {"progress": {"percent": 100}}]}])
    assert json.loads(nexo("--json", "run", "task", "T-2")[1])["state"] == "Concluido"


def test_team_skills_reach_agents(
    project: Path, nexo: Runner, tmp_path: Path, tools_home: Path, nexo_home: Path
) -> None:
    src = tmp_path / "payments-domain" / "SKILL.md"
    src.parent.mkdir()
    src.write_text(SKILL, encoding="utf-8")
    nexo("skill", "add", str(src))
    from nexo.kernel.context import AppContext, Options
    from nexo.modules.execution.app.run import Orchestrator, RunOptions
    from nexo.modules.execution.app.run import context as run_context

    ctx = AppContext(cwd=project, home=nexo_home, options=Options())
    orch = Orchestrator(ctx, RunOptions())
    plugin = Path(run_context.skills_plugin(orch))
    assert (plugin / "skills/payments-domain/SKILL.md").is_file() and (plugin / "skills/python-standards").is_dir()
    from nexo.kernel.models.work import Task

    assert "payments-domain: Regras do domínio de pagamentos" in run_context.team_skills(
        orch, "devin", Task(id="T-1", title="x")
    )
    # skill de outra stack fica fora da lista
    assert "python-standards" not in run_context.team_skills(orch, "devin", Task(id="T-1", title="x", stack="dotnet"))
    ctx.close()


def test_manual_pull_waits_for_background_pull(workdir: Path, nexo: Runner, tmp_path: Path, nexo_home: Path) -> None:
    import threading
    import time

    repo, pub = make_brain(tmp_path, nexo, tmp_path / "w.key")
    configure(nexo, repo, pub)
    lock = nexo_home / "brain.lock"
    lock.write_text("", encoding="utf-8")
    threading.Timer(1.0, lambda: lock.unlink(missing_ok=True)).start()  # pull em segundo plano termina
    start = time.monotonic()
    code, out, _ = nexo("--json", "brain", "pull")
    assert code == 0 and json.loads(out)["verified"] and time.monotonic() - start >= 0.9
    lock.write_text("", encoding="utf-8")
    nexo("config", "set", "brain.lock_wait_seconds", "1")
    code, _, err = nexo("brain", "pull")
    assert code == 1 and "em andamento" in err
    lock.unlink()


def test_git_clone_uses_token_header_without_prompt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import subprocess

    from nexo.kernel import brain as brain_adapter

    seen: dict[str, object] = {}

    def fake_run(cmd, **kw):  # type: ignore[no-untyped-def]
        seen["cmd"], seen["env"] = cmd, kw.get("env")
        return subprocess.CompletedProcess(cmd, 128, "", "falha simulada")

    monkeypatch.setattr(brain_adapter.subprocess, "run", fake_run)
    with pytest.raises(Exception, match="falha simulada"):
        brain_adapter.pull("https://github.com/empresa/nexo-brain.git", tmp_path / "b", [], False, token="tok")
    cmd = seen["cmd"]
    assert any(str(c).startswith("http.extraHeader=Authorization: Basic ") for c in cmd)  # type: ignore[union-attr]
    assert "tok" not in " ".join(map(str, cmd))  # token só em base64 no cabeçalho
    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0"  # type: ignore[index]
