"""Fluxo por papel de ponta a ponta: da ideia à história pronta, com a demanda chegando à pessoa certa."""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import Runner, dump_yaml
from tests.integration.test_roleflow import BACKLOG, READY

TEAM = {
    "mode": "team",
    "tl": "os:ana",
    "members": [
        {"id": "os:bia", "roles": ["pm"]},
        {"id": "os:ana", "roles": ["tl"]},
        {"id": "os:caio", "roles": ["qa"]},
        {"id": "os:dani", "roles": ["dev"]},
        {"id": "os:edu", "roles": ["dev"]},
    ],
}
SAFE = copy.deepcopy(READY)
SAFE["done"]["structured"]["spec_patch"].pop("risks")
SAFE["done"]["structured"]["spec_patch"]["risks_reviewed"] = True


def as_(monkeypatch: pytest.MonkeyPatch, who: str) -> None:
    monkeypatch.setenv("NX_ACTOR", who)


def j(nexo: Runner, *args: str) -> Any:
    code, out, err = nexo("--json", *args)
    assert code == 0, err + out
    return json.loads(out) if out.strip() else {}


@pytest.fixture
def team_project(project: Path, nexo_home: Path, scenario: Any, monkeypatch: pytest.MonkeyPatch) -> Path:
    dump_yaml(project / ".nexo" / "team" / "team.yaml", TEAM)
    dump_yaml(nexo_home / "brain" / "org" / "config.yaml", {"planning": {"role_flow": {"joint_session": "never"}}})
    scenario([{"tool": "claude", "events": [e]} for e in (SAFE, BACKLOG, BACKLOG)], tools={"claude": {}})
    return project


def to_assigned(nexo: Runner, monkeypatch: pytest.MonkeyPatch) -> str:
    as_(monkeypatch, "bia")
    assert j(nexo, "pm", "idea", "Parcelamento")["id"] == "IDEA-1"
    j(nexo, "pm", "decide", "IDEA-1", "build", "--reason", "Abandono de carrinho")
    assert j(nexo, "pm", "split", "IDEA-1", "--feature", "Simulador")["features"] == ["F-1"]
    code, out, err = nexo("pm", "refine", "F-1")
    assert code == 0, err + out
    code, out, err = nexo("pm", "approve", "F-1")
    assert code == 0, err + out
    as_(monkeypatch, "ana")
    code, out, err = nexo("tl", "approve", "F-1")
    assert code == 0, err + out
    board = {r["id"]: r for r in j(nexo, "board")}
    story = next(i for i in board if i.startswith("US-"))
    assert board["F-1"]["stage"] == "technical" and board[story]["stage"] == "technical"
    j(nexo, "tl", "assign", "F-1", "--dev", "os:dani", "--qa", "os:caio")
    return story


def test_idea_to_ready_and_who_has_it_at_each_step(
    team_project: Path, nexo: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    as_(monkeypatch, "bia")
    assert j(nexo, "pm", "idea", "Parcelamento")["id"] == "IDEA-1"
    assert j(nexo, "pm")[0]["demand"] == "IDEA-1"
    as_(monkeypatch, "dani")
    assert j(nexo, "dev") == []
    as_(monkeypatch, "ana")
    assert nexo("pm", "idea", "Outra")[0] != 0  # TL não tem o papel de PM
    as_(monkeypatch, "bia")
    assert nexo("pm", "split", "IDEA-1", "--single")[0] != 0  # precisa decidir antes
    j(nexo, "pm", "decide", "IDEA-1", "build", "--reason", "Abandono")
    j(nexo, "pm", "split", "IDEA-1", "--feature", "Simulador")
    assert j(nexo, "demand", "tree", "IDEA-1")["children"][0]["id"] == "F-1"
    assert nexo("pm", "refine", "F-1")[0] == 0
    code, out, err = nexo("pm", "approve", "F-1")
    assert code == 0, err + out
    as_(monkeypatch, "ana")
    assert any(r["demand"] == "F-1" for r in j(nexo, "tl"))
    assert nexo("tl", "approve", "F-1")[0] == 0
    story = next(r["id"] for r in j(nexo, "board") if r["id"].startswith("US-"))
    assert nexo("tl", "assign", story, "--dev", "os:bia", "--qa", "os:caio")[0] != 0  # PM não é dev
    assert nexo("tl", "assign", story, "--dev", "os:dani", "--qa", "os:dani")[0] != 0  # dev é QA? recusado
    j(nexo, "tl", "assign", "F-1", "--dev", "os:dani", "--qa", "os:caio")
    # QA e dev recebem ao mesmo tempo
    as_(monkeypatch, "caio")
    assert any(r["demand"] == story for r in j(nexo, "qa"))
    as_(monkeypatch, "dani")
    assert any(r["demand"] == story for r in j(nexo, "dev"))
    brief = j(nexo, "brief", story)
    assert brief["for_you"] and brief["expected"] == ["Melhorar o refinamento"]
    assert nexo("qa", "approve", story)[0] != 0  # dev não aprova testes
    as_(monkeypatch, "caio")
    assert nexo("qa", "approve", story)[0] != 0  # sem plano ainda
    j(nexo, "qa", "plan", story)
    assert j(nexo, "qa", "approve", story)["part"] == "testes"
    as_(monkeypatch, "edu")
    assert nexo("dev", "approve", story)[0] != 0  # não é o dev indicado
    as_(monkeypatch, "dani")
    out = j(nexo, "dev", "approve", story)
    assert out["part"] == "plano" and "ready" in out
    assert j(nexo, "demand", "verify")["ok"]


def test_proposal_invalidates_only_the_changed_section(
    team_project: Path, nexo: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    story = to_assigned(nexo, monkeypatch)
    as_(monkeypatch, "caio")
    j(nexo, "qa", "plan", story)
    j(nexo, "qa", "approve", story)
    as_(monkeypatch, "dani")
    j(nexo, "dev", "approve", story)
    j(nexo, "dev", "refine", story, "--propose", "implementacao=mudar a ordem")
    pid = j(nexo, "proposals")[0]["id"]
    assert j(nexo, "proposals", "accept", pid)["invalidated"] == [f"{story}:plano"]
    show = j(nexo, "demand", "show", story)
    assert show["stage"] == "refinement"
    assert [h["role"] for h in show["holders"]] == ["dev"]  # QA continua aprovado
    # técnica: é do tech lead; dev não decide
    j(nexo, "dev", "refine", story, "--propose", "tecnica=trocar a fila")
    pid = j(nexo, "proposals")[0]["id"]
    assert j(nexo, "demand", "why", story)["blocked_by"] == [pid]
    assert nexo("dev", "approve", story)[0] != 0
    assert nexo("proposals", "accept", pid)[0] != 0
    as_(monkeypatch, "ana")
    assert j(nexo, "proposals", "--mine")[0]["id"] == pid
    assert "F-1:design" in j(nexo, "proposals", "accept", pid)["invalidated"]
    assert j(nexo, "demand", "why", story)["blocked_by"] == []


def test_problem_during_build_premise_and_resume_plan(
    team_project: Path, nexo: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    story = to_assigned(nexo, monkeypatch)
    as_(monkeypatch, "dani")
    out = j(nexo, "dev", "problem", story, "--kind", "technical", "--text", "API recusa centavos", "--file", "src/svc/version.py")
    pid = out["proposal"]
    assert out["owner"] == "tl" and out["blocks"]
    assert nexo("dev", "run", story)[0] != 0  # travada até decidir
    assert j(nexo, "dev", "resume", story, "--dry-run")["blocked_by"] == [pid]
    # só o tech lead libera premissa; o dev não
    assert nexo("tl", "assume", pid, "--reason", "prazo")[0] != 0
    as_(monkeypatch, "ana")
    assert j(nexo, "tl", "assume", pid, "--reason", "PM de férias")["merge_held"]
    assert j(nexo, "demand", "why", story)["premises"] == [pid]
    plan = j(nexo, "dev", "resume", story, "--dry-run")
    assert plan["blocked_by"] == [] and plan["what_changed"][0]["why"] == pid
    # dono técnico confirma
    assert j(nexo, "pm", "confirm", pid)["status"] == "accepted"
    assert j(nexo, "demand", "why", story)["premises"] == []
    # problema fora de escopo vira demanda ligada e não trava
    as_(monkeypatch, "dani")
    other = j(nexo, "dev", "problem", story, "--kind", "other", "--text", "log verboso")
    assert not other["blocks"] and other["demand"].startswith("BUG-")
    assert any(c["id"] == other["demand"] for c in j(nexo, "demand", "tree", story)["children"])


def test_premise_refused_in_sensitive_area(project: Path, nexo: Runner, nexo_home: Path, scenario: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    dump_yaml(project / ".nexo" / "team" / "team.yaml", TEAM)
    dump_yaml(nexo_home / "brain" / "org" / "config.yaml", {"planning": {"role_flow": {"joint_session": "never"}}})
    scenario([{"tool": "claude", "events": [e]} for e in (READY, BACKLOG, BACKLOG)], tools={"claude": {}})
    story = to_assigned(nexo, monkeypatch)
    as_(monkeypatch, "dani")
    pid = j(nexo, "dev", "problem", story, "--kind", "functional", "--text", "AC-1 incompleto", "--ac", "AC-1")["proposal"]
    as_(monkeypatch, "ana")
    code, _, err = nexo("tl", "assume", pid, "--reason", "prazo")
    assert code != 0 and "sensível" in err


def test_meeting_items_become_proposals_owned_by_section(
    team_project: Path, nexo: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    as_(monkeypatch, "bia")
    j(nexo, "pm", "idea", "Parcelas")
    j(nexo, "pm", "decide", "IDEA-1", "build", "--reason", "x")
    j(nexo, "pm", "split", "IDEA-1", "--single")
    assert nexo("meeting", "start", "F-1", "--type", "funcional")[0] != 0  # precisa de PM e TL
    mid = j(nexo, "meeting", "start", "F-1", "--type", "funcional", "--with", "os:ana")["id"]
    j(nexo, "meeting", "note", "Arredondamento vai para a última parcela")
    j(nexo, "meeting", "note", "técnico: chamada direta")
    items = j(nexo, "meeting", "close")["items"]
    assert [i["section"] for i in items] == ["funcional", "tecnica"]
    assert not any(e.get("type") == "demand.approved" for e in _events(team_project))
    out = j(nexo, "meeting", "review", mid, "--accept", "I-1")
    assert out["decided"] == ["I-1"]
    assert nexo("meeting", "review", mid, "--accept", "I-2")[0] != 0  # técnica é do TL
    as_(monkeypatch, "ana")
    assert j(nexo, "meeting", "review", mid, "--accept", "I-2")["pending"] == []


def _events(project: Path) -> list[dict[str, Any]]:
    return [json.loads(p.read_text("utf-8")) for p in (project / ".nexo" / "team").rglob("events/**/*.json")]


def test_gate_bug_becomes_demand_and_task_states_follow(
    team_project: Path, nexo: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    story = to_assigned(nexo, monkeypatch)
    from nexo.kernel.context import AppContext, Options

    context = AppContext(options=Options())
    context.audit("bug.created", {"bug": "BUG-X", "story": story, "source": "gate"}, ref=story)
    context.audit("state.transition", {"item": story, "from": "Pronto", "to": "EmDev"}, ref=story)
    show = j(nexo, "demand", "show", "BUG-X")
    assert show["origin"]["kind"] == "gate" and show["spawned_by"] == story and show["stage"] == "build"
    assert j(nexo, "demand", "show", story)["state"] == "EmDev"


def test_two_machines_through_team_repo(tmp_path: Path, workdir: Path, nexo: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem ninguém usar Git: a PM grava numa máquina e o tech lead recebe em outra."""
    bare = tmp_path / "team.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(bare), str(seed)], check=True, capture_output=True)
    dump_yaml(seed / "team.yaml", TEAM)
    dump_yaml(seed / "projects" / "loja" / ".nexo" / "nexo.yaml", {"project": "loja", "team": {"name": "squad", "repo": str(bare)}})
    for args in (["config", "user.email", "t@t"], ["config", "user.name", "t"], ["add", "-A"], ["commit", "-qm", "i"], ["branch", "-M", "main"], ["push", "-q", "-u", "origin", "main"]):
        subprocess.run(["git", *args], cwd=seed, check=True, capture_output=True)
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    def machine(name: str, who: str) -> None:
        monkeypatch.setenv("NX_HOME", str(tmp_path / name))
        as_(monkeypatch, who)

    machine("m-bia", "bia")
    out = j(nexo, "join", "squad", "--repo", str(bare), "--project", "loja")
    assert out["current"] == "loja"
    assert j(nexo, "pm", "idea", "Parcelamento")["id"] == "IDEA-1"
    machine("m-ana", "ana")
    j(nexo, "join", "squad", "--repo", str(bare), "--project", "loja")
    board = j(nexo, "board")
    assert [r["id"] for r in board] == ["IDEA-1"] and board[0]["with"] == ["pm:os:bia"]
    # gravando ao mesmo tempo: nada se perde
    machine("m-bia", "bia")
    j(nexo, "pm", "idea", "Segunda")
    j(nexo, "demand", "verify")
    machine("m-ana", "ana")
    as_(monkeypatch, "bia")
    machine("m-bia", "bia")
    assert {r["id"] for r in j(nexo, "board")} == {"IDEA-1", "IDEA-2"}
    assert (tmp_path / "m-ana" / "teams" / "squad" / ".git").exists()
    log = subprocess.run(["git", "--git-dir", str(bare), "log", "--oneline", "main"], capture_output=True, text=True, encoding="utf-8", check=True).stdout
    assert "IDEA-2" in log


def test_try_and_test_create_no_demand(project: Path, nexo: Runner, scenario: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    scenario([{"tool": "claude", "events": [{"edit": {"path": "x.py", "content": "print(1)\n"}}, {"done": {"summary": "ok"}}]}], tools={"claude": {}})
    as_(monkeypatch, "bia")
    data = j(nexo, "try", "cria um arquivo")
    assert data["id"] == "TRY-1"
    assert j(nexo, "board", "--all") == []
    assert j(nexo, "try", "list")[0]["id"] == "TRY-1"
    promoted = j(nexo, "try", "promote", "TRY-1")
    assert promoted["idea"] == "IDEA-1"
    assert j(nexo, "board")[0]["id"] == "IDEA-1"
    assert j(nexo, "try", "discard", "TRY-1")["discarded"]
    assert j(nexo, "try", "list") == []


def test_help_is_by_role(workdir: Path, nexo: Runner) -> None:
    code, out, _ = nexo("help")
    assert code == 0 and "pm refine" in out and "qa approve" in out and "tl assign" in out and "dev run" in out
    code, out, _ = nexo("help", "--role", "qa")
    assert "qa plan" in out and "pm refine" not in out
