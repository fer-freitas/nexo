"""Fluxo de planejamento ponta a ponta com o agente fake."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexo.kernel.spec.model import TEST_TYPES
from tests.conftest import Runner, dump_yaml, edit

ROUND1 = {
    "done": {
        "structured": {
            "spec_patch": {
                "title": "Endpoint de versão",
                "objective": "Consultar versão implantada",
                "problem": "Não sabemos a versão em produção",
                "personas": ["SRE"],
                "scope_in": ["função version"],
                "scope_out": ["UI"],
                "acceptance": [
                    {
                        "id": "AC-1",
                        "title": "Retorna versão",
                        "given": ["serviço"],
                        "when": ["chamo"],
                        "then": ["recebo 0.1.0"],
                    }
                ],
            },
            "questions": [
                {"text": "Qual a classificação de dados?", "severity": "blocking", "section": "data_class"},
                {"text": "Há requisito de desempenho?", "severity": "important", "section": "nfr"},
            ],
            "assumptions": ["versão é constante"],
        }
    }
}
ROUND2 = {
    "done": {
        "structured": {
            "spec_patch": {
                "data_class": "internal",
                "contracts": [{"id": "C-1", "type": "openapi", "path": "contracts/version.yaml"}],
                "nfr": {"performance": "< 1 ms", "security": "nada sensível", "availability": "99,9%"},
                "dependencies_none": True,
                "test_strategy": {t: "sim" for t in TEST_TYPES},
                "risks_reviewed": True,
            },
            "questions": [],
            "contract_files": [
                {
                    "path": "contracts/version.yaml",
                    "content": "openapi: 3.1.0\ninfo: {title: v, version: '1'}\npaths:\n  /version:\n    get:\n"
                    "      responses:\n        '200': {description: ok}\n",
                }
            ],
        }
    }
}
BACKLOG = {
    "done": {
        "structured": {
            "stories": [{"id": "US-1-1", "title": "Consultar versão", "ac_refs": ["AC-1"], "points": 2}],
            "tasks": [
                {"id": "T-1-1", "story": "US-1-1", "kind": "contract", "title": "Contrato", "files_hint": ["api.yaml"]},
                {
                    "id": "T-1-2",
                    "story": "US-1-1",
                    "kind": "backend",
                    "title": "Backend",
                    "depends_on": ["T-1-1"],
                    "files_hint": ["src/svc/version.py"],
                    "acceptance": ["version() retorna 0.1.0"],
                },
                {
                    "id": "T-1-3",
                    "story": "US-1-1",
                    "kind": "docs",
                    "title": "Docs",
                    "depends_on": ["T-1-1"],
                    "files_hint": ["README.md"],
                },
            ],
        }
    }
}


def run_json(nexo: Runner, *args: str) -> tuple[int, dict]:  # type: ignore[type-arg]
    code, out, err = nexo("--json", *args)
    return code, (json.loads(out) if out.strip() else {"err": err})


@pytest.fixture
def prd(tmp_path: Path) -> Path:
    path = tmp_path / "prd.md"
    path.write_text("# PRD\nExpor a versão do serviço. Contato: joao@empresa.com.br\n", encoding="utf-8")
    return path


def test_full_planning_cycle(project: Path, nexo: Runner, scenario, prd: Path) -> None:  # type: ignore[no-untyped-def]
    scenario(
        [{"tool": "claude", "events": [e]} for e in (ROUND1, ROUND2, BACKLOG)]
        + [{"tool": "claude", "events": [edit(), {"progress": {"percent": 100}}]}]
    )
    code, rep = run_json(nexo, "plan", str(prd), "precisamos para auditoria")
    assert code == 0 and rep["feature"] == "F-1" and rep["title"] == "Endpoint de versão"
    assert rep["score"] < 100 and len(rep["questions"]) == 2 and rep["ingested"][0]["redactions"] == 1
    folder = Path(rep["folder"])
    assert folder.name == "F-1-endpoint-de-versao" and (folder / "spec.md").is_file()
    assert "joao@" not in (folder / "inputs" / "prd.md").read_text(encoding="utf-8")
    code, rep = run_json(nexo, "plan", "approve", "F-1")
    assert code == 1
    code, rep = run_json(nexo, "plan", "answer", "F-1", "Q-1=interna")
    assert rep["ready"] and rep["score"] == 100 and (folder / "contracts/version.yaml").is_file()
    code, rep = run_json(nexo, "plan", "approve", "F-1")
    assert code == 0 and rep["waves"] == [["T-1-1"], ["T-1-2", "T-1-3"]]
    assert (project / ".nexo/tasks/T-1-2.yaml").is_file()
    assert "@AC-1" in (folder / "features/US-1-1.feature").read_text(encoding="utf-8")
    trace = (folder / "traceability.yaml").read_text(encoding="utf-8")
    assert "AC-1" in trace and "T-1-2" in trace
    code, rep = run_json(nexo, "run", "task", "T-1-2")
    assert rep["state"] == "Concluido"
    code, out, _ = nexo("plan", "graph", "F-1", "--mermaid")
    assert "T_1_1 --> T_1_2" in out
    code, out, _ = nexo("plan", "status", "F-1")
    assert "100%" in out and "nexo run story" in out
    logs = json.loads(nexo("--json", "logs", "-n", "300")[1])
    types = {e["type"] for e in logs}
    assert {
        "plan.created",
        "plan.ingested",
        "plan.refined",
        "plan.answered",
        "gate.decision",
        "plan.approved",
        "llm.result",
    } <= types


def test_invalid_output_is_repaired(project: Path, nexo: Runner, scenario, prd: Path) -> None:  # type: ignore[no-untyped-def]
    bad = {"done": {"structured": {"spec_patch": {"data_class": "ultra"}, "questions": []}}}
    scenario([{"tool": "claude", "events": [bad]}, {"tool": "claude", "events": [ROUND1]}])
    code, rep = run_json(nexo, "plan", str(prd))
    assert code == 0 and rep["title"] == "Endpoint de versão"
    logs = json.loads(nexo("--json", "logs", "-n", "200")[1])
    assert any(e["type"] == "llm.invalid" for e in logs)


def test_planner_falls_back_on_rate_limit(project: Path, nexo: Runner, scenario, prd: Path) -> None:  # type: ignore[no-untyped-def]
    scenario(
        [{"tool": "claude", "events": [{"rate_limited": {"reset_in": 600}}]}, {"tool": "copilot", "events": [ROUND1]}]
    )
    code, rep = run_json(nexo, "plan", str(prd))
    assert code == 0 and rep["score"] > 0


def test_no_route_for_planner(project: Path, nexo: Runner, scenario, prd: Path) -> None:  # type: ignore[no-untyped-def]
    scenario([], tools={"devin": {}})
    code, _, err = nexo("plan", str(prd))
    assert code == 1 and "nenhuma rota" in err


def test_force_approve_requires_reason(project: Path, nexo: Runner, scenario, prd: Path) -> None:  # type: ignore[no-untyped-def]
    scenario([{"tool": "claude", "events": [ROUND1]}, {"tool": "claude", "events": [BACKLOG]}])
    nexo("plan", str(prd))
    code, _, err = nexo("plan", "approve", "F-1", "--force")
    assert code == 1 and "--reason" in err
    code, rep = run_json(nexo, "plan", "approve", "F-1", "--force", "--reason", "urgência regulatória aprovada")
    assert code == 0 and rep["backlog"]
    logs = json.loads(nexo("--json", "logs", "-n", "200")[1])
    assert any(e["payload"].get("decision") == "approved_with_exception" for e in logs)


def test_breakdown_repair_on_cycle(project: Path, nexo: Runner, scenario, prd: Path) -> None:  # type: ignore[no-untyped-def]
    cyclic = json.loads(json.dumps(BACKLOG))
    cyclic["done"]["structured"]["tasks"][0]["depends_on"] = ["T-1-2"]
    scenario(
        [
            {"tool": "claude", "events": [ROUND1]},
            {"tool": "claude", "events": [ROUND2]},
            {"tool": "claude", "events": [cyclic]},
            {"tool": "claude", "events": [BACKLOG]},
        ]
    )
    nexo("plan", str(prd))
    nexo("plan", "answer", "F-1", "Q-1=interna")
    code, rep = run_json(nexo, "plan", "approve", "F-1")
    assert code == 0 and len(rep["backlog"]["tasks"]) == 3


def test_answers_from_file_and_audio(
    project: Path, nexo: Runner, scenario, prd: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario([{"tool": "claude", "events": [ROUND1]}, {"tool": "claude", "events": [ROUND2]}])
    nexo("plan", str(prd))
    audio = tmp_path / "resposta.m4a"
    audio.write_bytes(b"x")
    monkeypatch.setenv("NX_FAKE_TRANSCRIPT", "é interna")
    answers = dump_yaml(tmp_path / "a.yaml", {"Q-2": "p95 < 10ms"})
    code, rep = run_json(nexo, "plan", "answer", "F-1", f"Q-1=@{audio}", "--file", str(answers))
    assert code == 0 and rep["ready"]
    qs = (Path(rep["folder"]) / "questions.yaml").read_text(encoding="utf-8")
    assert "é interna" in qs and "p95" in qs


def test_answer_without_refine_and_errors(project: Path, nexo: Runner, scenario, prd: Path) -> None:  # type: ignore[no-untyped-def]
    scenario([{"tool": "claude", "events": [ROUND1]}])
    nexo("plan", str(prd))
    code, rep = run_json(nexo, "plan", "answer", "F-1", "Q-2=sem requisito", "--no-refine")
    assert code == 0 and [q["id"] for q in rep["questions"]] == ["Q-1"]
    assert nexo("plan", "answer", "F-1", "Q-9=x")[0] == 1
    assert nexo("plan", "answer", "F-1", "semigual")[0] == 1
    assert nexo("--json", "plan", "answer", "F-1")[0] == 1
    assert nexo("plan", "status", "F-9")[0] == 1
    assert nexo("plan", "graph", "F-1")[0] == 1
    assert nexo("plan")[0] in (0, 2)


def test_interactive_answer(project: Path, nexo: Runner, scenario, prd: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    scenario([{"tool": "claude", "events": [ROUND1]}, {"tool": "claude", "events": [ROUND2]}])
    nexo("plan", str(prd))
    answers = iter(["interna", ""])
    monkeypatch.setattr("typer.prompt", lambda *a, **k: next(answers))
    code, out, _ = nexo("plan", "answer", "F-1")
    assert code == 0 and "100%" in out


def test_plan_feature_adds_inputs(project: Path, nexo: Runner, scenario, prd: Path) -> None:  # type: ignore[no-untyped-def]
    scenario([{"tool": "claude", "events": [ROUND1]}, {"tool": "claude", "events": [ROUND2]}])
    nexo("plan", str(prd))
    code, rep = run_json(nexo, "plan", "feature", "F-1", "detalhe novo", "-a", "Q-1=interna")
    assert code == 0 and rep["ready"] and rep["ingested"]


def test_workspace_and_adopt(
    workdir: Path,
    nexo: Runner,
    scenario,
    prd: Path,
    nexo_home: Path,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario([{"tool": "claude", "events": [ROUND1]}])
    code, rep = run_json(nexo, "plan", str(prd), "--workspace", "Pagamentos")
    assert code == 0 and rep["workspace"] == "Pagamentos"
    assert str(nexo_home / "workspace" / "pagamentos") in rep["folder"]
    code, rep = run_json(nexo, "plan", "status", "F-1", "--workspace", "Pagamentos")
    assert rep["feature"] == "F-1"
    repo = workdir / "novo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    code, out, _ = nexo("init", "--adopt", "Pagamentos")
    assert code == 0 and "migrado" in out
    assert list((repo / ".nexo/specs").glob("F-1-*"))
    assert not (nexo_home / "workspace" / "pagamentos").exists()
    assert nexo("init", "--adopt", "Pagamentos")[0] == 1


def test_clarification_blocks_task_and_resumes(project: Path, nexo: Runner, scenario, prd: Path) -> None:  # type: ignore[no-untyped-def]
    scenario(
        [
            {"tool": "claude", "events": [ROUND1]},
            {"tool": "claude", "events": [ROUND2]},
            {"tool": "claude", "events": [BACKLOG]},
            {
                "tool": "claude",
                "events": [
                    edit(),
                    {"clarification": {"question": "Formato semver?", "section": "acceptance", "blocking": True}},
                ],
            },
            # a resposta à pergunta do agente não replaneja (0.53.5): o próximo passo já é a retomada da tarefa
            {"tool": "claude", "events": [edit(), {"progress": {"percent": 100}}]},
        ]
    )
    nexo("plan", str(prd))
    nexo("plan", "answer", "F-1", "Q-1=interna")
    nexo("plan", "approve", "F-1")
    code, rep = run_json(nexo, "run", "task", "T-1-2")
    assert rep["state"] == "Bloqueado" and "esclarecimento Q-3" in rep["reason"]
    status = run_json(nexo, "plan", "status", "F-1")[1]
    agent_q = next(q for q in status["questions"] if q["id"] == "Q-3")
    assert not status["ready"] and agent_q["text"].startswith("[T-1-2]") and agent_q["source"] == "agent"
    code, rep = run_json(nexo, "plan", "answer", "F-1", "Q-3=sim, semver")
    assert rep["ready"]
    code, rep = run_json(nexo, "resume", "T-1-2")
    assert rep["state"] == "Concluido"


def test_login_figma_stores_in_keyring(nexo: Runner, monkeypatch: pytest.MonkeyPatch, memory_keyring) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("typer.prompt", lambda *a, **k: "figd_token")
    code, out, _ = nexo("login", "figma")
    assert code == 0 and memory_keyring.store[("nexo-cli", "figma")] == "figd_token"
    assert nexo("login", "nope")[0] == 1
    monkeypatch.setattr("typer.prompt", lambda *a, **k: "  ")
    assert nexo("login", "figma")[0] == 1


def test_ids_are_scoped_per_project_and_state_reconciled(
    project: Path,
    workdir: Path,
    nexo: Runner,
    scenario,  # type: ignore[no-untyped-def]
    prd: Path,
    nexo_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario(
        [
            {"tool": "claude", "events": [ROUND1]},
            {"tool": "claude", "events": [ROUND1]},
            {"tool": "claude", "events": [ROUND2]},
            {"tool": "claude", "events": [BACKLOG]},
        ]
    )
    assert run_json(nexo, "plan", str(prd))[1]["feature"] == "F-1"
    outside = workdir / "fora"
    outside.mkdir()
    monkeypatch.chdir(outside)
    code, rep = run_json(nexo, "plan", str(prd), "--workspace", "outro")
    assert code == 0 and rep["feature"] == "F-1"  # mesmo id em outro projeto não colide
    # outro dev clonou o repo: banco local sem o item, estado reconstruído a partir dos arquivos
    monkeypatch.chdir(project)
    import shutil

    shutil.rmtree(nexo_home)
    scenario([{"tool": "claude", "events": [ROUND2]}, {"tool": "claude", "events": [BACKLOG]}])
    code, rep = run_json(nexo, "plan", "answer", "F-1", "Q-1=interna")
    assert code == 0 and rep["ready"] and rep["state"] == "Refinamento"
    code, rep = run_json(nexo, "plan", "approve", "F-1")
    assert code == 0 and rep["state"] == "Pronto"


def test_structured_roles_prefer_tools_with_structured_output(  # type: ignore[no-untyped-def]
    project: Path, nexo: Runner, scenario, prd: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Achado no teste real: o planejador caía no Copilot `auto` (mais barato), que devolve texto livre e errava o
    esquema. Papéis estruturados vão primeiro para ferramenta com saída estruturada; CLI de texto só se não houver."""
    from nexo.kernel.models.agent import Capabilities
    from nexo.modules.execution.adapters.agents.fake import FakeAdapter

    def caps(self: FakeAdapter) -> Capabilities:
        return Capabilities(verified=True, structured_output=self.name != "copilot")

    monkeypatch.setattr(FakeAdapter, "capabilities", caps)
    scenario([{"tool": "copilot", "events": [ROUND1]}, {"tool": "claude", "events": [ROUND1]}])
    assert run_json(nexo, "plan", str(prd))[0] == 0
    logs = json.loads(nexo("--json", "logs", "-n", "200")[1])
    assert next(e for e in logs if e["type"] == "llm.result")["payload"]["route"].startswith("claude/")


def test_plan_again_after_planner_failure(project: Path, nexo: Runner, scenario, prd: Path) -> None:  # type: ignore[no-untyped-def]
    """Achado no teste real: depois de uma falha do planejador o item ficava em Refinamento e um novo `nexo plan`
    dava "transição inválida"; replanejar a partir de Refinamento é permitido."""
    bad = {"done": {"structured": {"spec_patch": {"data_class": "ultra"}, "questions": []}}}
    scenario(
        [
            {"tool": "claude", "events": [bad]},
            {"tool": "claude", "events": [bad]},
            {"tool": "claude", "events": [ROUND1]},
        ]
    )
    code, _, err = nexo("plan", str(prd))
    assert code != 0 and "resposta válida" in err
    code, rep = run_json(nexo, "plan", str(prd))
    assert code == 0 and rep["title"] == "Endpoint de versão", rep


def test_approving_twice_returns_the_existing_backlog(project: Path, nexo: Runner, scenario, prd: Path) -> None:  # type: ignore[no-untyped-def]
    scenario([{"tool": "claude", "events": [e]} for e in (ROUND1, ROUND2, BACKLOG)])
    assert run_json(nexo, "plan", str(prd))[0] == 0
    assert run_json(nexo, "plan", "answer", "F-1", "Q-1=interna")[0] == 0
    code, first = run_json(nexo, "plan", "approve", "F-1")
    assert code == 0 and first["backlog"]["stories"]
    code, again = run_json(nexo, "plan", "approve", "F-1")  # sem nova quebra (o cenário não tem mais passos)
    assert code == 0 and again["backlog"] == first["backlog"]
