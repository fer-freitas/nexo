"""`nexo run story` ponta a ponta com agente fake e pipeline real (receita rápida offline)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nexo.kernel.spec.model import TEST_TYPES
from tests.conftest import Runner, dump_yaml, git_cmd

SPEC = {
    "done": {
        "structured": {
            "spec_patch": {
                "title": "Versão",
                "objective": "Consultar versão",
                "problem": "Sem visibilidade",
                "personas": ["SRE"],
                "scope_in": ["version"],
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
                "data_class": "internal",
                "contracts_not_applicable": "interno",
                "nfr": {"performance": "<1ms", "security": "nada", "availability": "n/a"},
                "dependencies_none": True,
                "test_strategy": {t: "sim" for t in TEST_TYPES},
                "risks_reviewed": True,
            },
            "questions": [],
        }
    }
}


def backlog(files_1: list[str], files_2: list[str]) -> dict[str, Any]:
    return {
        "done": {
            "structured": {
                "stories": [{"id": "US-1-1", "title": "Consultar versão", "ac_refs": ["AC-1"], "points": 1}],
                "tasks": [
                    {"id": "T-1-1", "story": "US-1-1", "kind": "backend", "title": "version", "files_hint": files_1},
                    {"id": "T-1-2", "story": "US-1-1", "kind": "docs", "title": "docs", "files_hint": files_2},
                ],
            }
        }
    }


QA = {
    "tool": "claude",
    "role": "qa-author",
    "events": [
        {
            "edit": {
                "path": "tests/test_version.py",
                "content": "# @AC-1\nfrom svc.version import version\n\n\ndef test_ac_1_retorna_versao() -> None:\n"
                '    assert version() == "0.1.0"\n',
            }
        },
        {"progress": {"percent": 100}},
    ],
}


def impl(task: str, value: str = "0.1.0", path: str = "src/svc/version.py") -> dict[str, Any]:
    content = f'def version() -> str:\n    return "{value}"\n' if path.endswith(".py") else f"# {value}\n"
    return {
        "tool": "claude",
        "task": task,
        "events": [{"edit": {"path": path, "content": content}}, {"progress": {"percent": 100}}],
    }


def review(verdict: str = "approve", role: str = "reviewer", **extra: Any) -> dict[str, Any]:
    return {
        "tool": "copilot",
        "role": role,
        "events": [{"done": {"structured": {"verdict": verdict, "findings": extra.pop("findings", []), **extra}}}],
    }


OK_REVIEWS = [review(), review(role="security-reviewer")]


def run_json(nexo: Runner, *args: str) -> tuple[int, dict]:  # type: ignore[type-arg]
    code, out, err = nexo("--json", *args)
    return code, (json.loads(out) if out.strip() else {"err": err})


@pytest.fixture
def planned(project: Path, nexo: Runner, fast_recipe: Path):  # type: ignore[no-untyped-def]
    """Projeto com F-1 aprovada; recebe os passos de execução da história."""

    def make(scenario, steps: list[dict[str, Any]], b: dict[str, Any] | None = None) -> Path:  # type: ignore[no-untyped-def]
        scenario(
            [
                {"tool": "claude", "events": [SPEC]},
                {"tool": "claude", "events": [b or backlog(["src/svc/version.py"], ["README.md"])]},
                *steps,
            ]
        )
        assert nexo("plan", "versão")[0] == 0
        assert nexo("plan", "approve", "F-1")[0] == 0
        return project

    nexo("config", "set", "gates.thresholds.code", "0.9", "--project")
    return make


def test_story_happy_path_auto_gate(planned, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    root = planned(scenario, [QA, impl("T-1-1"), impl("T-1-2", path="README.md"), *OK_REVIEWS])
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert code == 0 and rep["state"] == "Concluido" and rep["gate"]["decision"] == "auto"
    assert rep["tasks"] == {"T-1-1": "Concluido", "T-1-2": "Concluido"}
    assert {q["id"]: q["status"] for q in rep["quality"]}["unit"] == "passed"
    trace = next(root.glob(".nexo/specs/F-1-*/traceability.yaml")).read_text(encoding="utf-8")
    assert "test_ac_1_retorna_versao" in trace and "status: passed" in trace
    logs = json.loads(nexo("--json", "logs", "-n", "500")[1])
    types = {e["type"] for e in logs}
    assert {
        "story.start",
        "story.qa",
        "story.reviews",
        "gate.decision",
        "pre_commit" if False else "hook.pre_commit",
    } <= types
    qa = next(e for e in logs if e["type"] == "story.qa")["payload"]
    assert qa["red_before_impl"] is True and qa["locked"] == ["tests/test_version.py"]
    wt = Path(rep["worktree"])
    log = git_cmd(wt, "log", "--format=%s")
    assert "merge(US-1-1): T-1-1" in log and "merge(US-1-1): US-1-1-QA" in log


def test_bug_loop_fixes_and_revalidates(planned, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    planned(scenario, [QA, impl("T-1-1", "0.0.9"), impl("T-1-2", path="README.md"), impl("US-1-1-FIX1"), *OK_REVIEWS])
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert rep["state"] == "Concluido" and rep["bugs"] == ["B-1"] and rep["attempts"] == 1
    bug = (Path(rep["worktree"]).parents[2] / ".nexo/bugs/B-1.yaml").read_text(encoding="utf-8")
    assert "test_ac_1_retorna_versao" in bug and "state: Resolvido" in bug


def test_bug_loop_escalates_after_max_attempts(planned, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    nexo("config", "set", "bug_loop.max_attempts", "2", "--project")
    planned(
        scenario,
        [
            QA,
            impl("T-1-1", "0.0.9"),
            impl("T-1-2", path="README.md"),
            impl("US-1-1-FIX1", "0.0.8"),
            impl("US-1-1-FIX2", "0.0.7"),
        ],
    )
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert code == 1 and rep["state"] == "Escalado" and "2 tentativas" in rep["reason"]
    logs = json.loads(nexo("--json", "logs", "-n", "600")[1])
    fixes = [e["payload"] for e in logs if e["type"] == "story.bugfix"]
    assert [f["profile"] for f in fixes] == [None, "maxima-qualidade"]  # escada de escalonamento
    assert any(e["type"] == "notify" for e in logs)


def test_locked_tests_cannot_be_changed_by_fix(planned, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    cheat = {
        "tool": "claude",
        "task": "US-1-1-FIX1",
        "events": [
            {"edit": {"path": "tests/test_version.py", "content": "def test_ac_1_x() -> None:\n    pass\n"}},
            {"progress": {"percent": 100}},
        ],
    }
    planned(scenario, [QA, impl("T-1-1", "0.0.9"), impl("T-1-2", path="README.md"), cheat])
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert rep["state"] == "Escalado" and "SEC-40" in rep["reason"]


def test_human_gate_for_risk_area_and_segregation(
    planned,
    nexo: Runner,
    scenario,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    b = backlog(["src/svc/payments/refund.py"], ["README.md"])
    pay = {
        "tool": "claude",
        "task": "T-1-1",
        "events": [
            {"edit": {"path": "src/svc/version.py", "content": 'def version() -> str:\n    return "0.1.0"\n'}},
            {"edit": {"path": "src/svc/payments/refund.py", "content": "LIMIT = 10\n"}},
            {"progress": {"percent": 100}},
        ],
    }
    planned(scenario, [QA, pay, impl("T-1-2", path="README.md"), *OK_REVIEWS], b)
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert rep["state"] == "AguardandoHumano" and "payments" in rep["reason"]
    code, out, _ = nexo("tl", "gate", "US-1-1")
    assert code == 0 and "aguarda decisão humana" in out and "payments" in out and "nexo approve US-1-1" in out, out + _
    code, _, err = nexo("approve", "US-1-1")
    assert code == 5 and "SEC-52" in err
    monkeypatch.setenv("NX_ACTOR", "tech-lead")
    code, rep = run_json(nexo, "approve", "US-1-1")
    assert code == 0 and rep["state"] == "Concluido"
    logs = json.loads(nexo("--json", "logs", "-n", "400")[1])
    assert any(e["type"] == "gate.decision" and e["payload"].get("approver") == "os:tech-lead" for e in logs)


def test_reject_creates_bug_and_resume_fixes(
    planned,
    nexo: Runner,
    scenario,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nexo("config", "set", "gates.thresholds.code", "0.99", "--project")
    planned(
        scenario,
        [
            QA,
            impl("T-1-1"),
            impl("T-1-2", path="README.md"),
            *OK_REVIEWS,
            impl("US-1-1-FIX1", "log de auditoria", path="README.md"),
            *OK_REVIEWS,
        ],
    )
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert rep["state"] == "AguardandoHumano" and "confiança" in rep["reason"]
    monkeypatch.setenv("NX_ACTOR", "revisor")
    assert nexo("reject", "US-1-1")[0] == 1
    code, rep = run_json(nexo, "reject", "US-1-1", "--reason", "faltou log de auditoria")
    assert rep["state"] == "EmDev" and rep["bugs"] == ["B-1"]
    nexo("config", "set", "gates.allow_self_approval", "true", "--project")
    code, rep = run_json(nexo, "resume", "US-1-1")
    assert rep["state"] == "AguardandoHumano" and rep["attempts"] == 1
    code, rep = run_json(nexo, "approve", "US-1-1")
    assert rep["state"] == "Concluido"
    assert nexo("approve", "US-1-1")[0] == 1


def test_reviewer_spec_issue_returns_to_refinement(planned, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    planned(
        scenario,
        [
            QA,
            impl("T-1-1"),
            impl("T-1-2", path="README.md"),
            review("changes", spec_issue=True, findings=[{"severity": "medium", "message": "AC-1 ambíguo"}]),
            review(role="security-reviewer"),
        ],
    )
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert rep["state"] == "Refinamento" and "AC-1 ambíguo" in rep["reason"]
    status = json.loads(nexo("--json", "plan", "status", "F-1")[1])
    assert any(q["text"].startswith("[US-1-1]") for q in status["questions"])
    assert nexo("resume", "US-1-1")[0] == 1


def test_contradictory_spec_issue_from_an_approving_reviewer_is_ignored(planned, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    """Achado no autoteste real: o revisor aprovou, sem apontamento, e marcou `spec_issue`; a história voltava ao
    refinamento com motivo vazio."""
    planned(
        scenario,
        [
            QA,
            impl("T-1-1"),
            impl("T-1-2", path="README.md"),
            review("approve", spec_issue=True),
            review(role="security-reviewer"),
        ],
    )
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert rep["state"] in ("AguardandoHumano", "Concluido"), rep
    events = json.loads(nexo("--json", "logs", "-n", "300")[1])
    assert any(e["type"] == "review.spec_issue_ignored" for e in events)


def test_reviewer_reject_goes_to_bug(planned, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    nexo("config", "set", "bug_loop.max_attempts", "1", "--project")
    planned(
        scenario,
        [
            QA,
            impl("T-1-1"),
            impl("T-1-2", path="README.md"),
            review("reject"),
            review(role="security-reviewer", findings=[{"severity": "high", "message": "log com CPF"}]),
            impl("US-1-1-FIX1"),
            review("reject"),
            review(role="security-reviewer"),
        ],
    )
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert rep["state"] == "Escalado"
    bug = (Path(rep["worktree"]).parents[2] / ".nexo/bugs/B-1.yaml").read_text(encoding="utf-8")
    assert "log com CPF" in bug and "reviewer reprovou" in bug


def test_missing_tool_is_reported_before_spending(planned, nexo: Runner, scenario, fast_recipe: Path) -> None:  # type: ignore[no-untyped-def]
    recipe = json.loads(json.dumps(__import__("tests.conftest", fromlist=["x"]).FAST_PYTHON_RECIPE))
    recipe["stages"].append(
        {
            "id": "sast",
            "kind": "security",
            "tool": "ferramenta-inexistente",
            "run": ["x"],
            "parser": "exitcode",
            "required": True,
        }
    )
    dump_yaml(fast_recipe / "recipes" / "python.yaml", recipe)
    planned(scenario, [QA, impl("T-1-1"), impl("T-1-2", path="README.md")])
    code, out, err = nexo("run", "story", "US-1-1")
    assert code != 0 and "ferramenta-inexistente" in err and "Nada foi gasto" in err, err + out
    assert json.loads(nexo("--json", "status", "US-1-1")[1])["state"] == "Pronto", "avisa antes de gastar"


def test_qa_without_ac_tags_retries_then_escalates(planned, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    bad_qa = {
        "tool": "claude",
        "role": "qa-author",
        "events": [
            {"edit": {"path": "tests/test_x.py", "content": "def test_x() -> None:\n    assert False\n"}},
            {"progress": {"percent": 100}},
        ],
    }
    bad_qa2 = {
        "tool": "claude",
        "role": "qa-author",
        "events": [
            {"edit": {"path": "tests/test_y.py", "content": "def test_y() -> None:\n    assert False\n"}},
            {"progress": {"percent": 100}},
        ],
    }
    planned(scenario, [bad_qa, bad_qa2])
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert rep["state"] == "Escalado" and "AC-1" in rep["reason"]


def test_parallel_conflict_is_reintegrated(planned, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    b = backlog(["src/svc/version.py"], ["README.md"])
    t2 = {
        "tool": "claude",
        "task": "T-1-2",
        "events": [
            {"edit": {"path": "src/svc/version.py", "content": 'def version() -> str:\n    return "outra"\n'}},
            {"progress": {"percent": 100}},
        ],
    }
    integ = {
        "tool": "claude",
        "task": "T-1-2-INT",
        "events": [{"edit": {"path": "README.md", "content": "# doc\n"}}, {"progress": {"percent": 100}}],
    }
    planned(scenario, [QA, impl("T-1-1"), t2, integ, *OK_REVIEWS], b)
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert rep["state"] == "Concluido"
    logs = json.loads(nexo("--json", "logs", "-n", "600")[1])
    assert any(e["type"] == "story.conflict" for e in logs)


def test_story_errors(project: Path, nexo: Runner) -> None:
    assert nexo("run", "story", "US-9")[0] == 1
    assert nexo("approve", "US-9")[0] == 1
    assert nexo("resume", "US-9")[0] == 1


def test_no_independent_reviewer_forces_human_gate(planned, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    root = planned(scenario, [QA, impl("T-1-1"), impl("T-1-2", path="README.md")])
    # só a ferramenta do mesmo fornecedor do autor está disponível
    import os

    from ruamel.yaml import YAML

    scen = Path(os.environ["NX_FAKE_SCENARIO"])
    data = YAML(typ="safe").load(scen.read_text(encoding="utf-8"))
    data["tools"] = {"claude": {}}
    dump_yaml(scen, data)
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert rep["state"] == "AguardandoHumano" and "indisponível" in rep["reason"]
    assert root.exists()


def test_flaky_test_is_retried_without_bug(planned, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    flaky = {
        "tool": "claude",
        "task": "T-1-2",
        "events": [
            {
                "edit": {
                    "path": "tests/test_flaky.py",
                    "content": "from pathlib import Path\n\n\ndef test_flaky() -> None:\n"
                    "    marker = Path(__file__).with_suffix('.ran')\n"
                    "    first = not marker.exists()\n    marker.write_text('x')\n    assert not first\n",
                }
            },
            {"progress": {"percent": 100}},
        ],
    }
    b = backlog(["src/svc/version.py"], ["tests/test_flaky.py"])
    planned(scenario, [QA, impl("T-1-1"), flaky, *OK_REVIEWS], b)
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert rep["state"] == "Concluido" and rep["bugs"] == []
    logs = json.loads(nexo("--json", "logs", "-n", "600")[1])
    assert any(e["type"] == "quality.flaky" for e in logs)


def test_tracked_build_artifacts_do_not_break_merges(planned, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    t1 = impl("T-1-1")
    t1["events"].insert(1, {"edit": {"path": "src/svc/cache.bin", "content": "v3"}})
    root = planned(scenario, [QA, t1, impl("T-1-2", path="README.md"), *OK_REVIEWS])
    # repositório real com artefato versionado por engano, alterado pelo pipeline
    (root / "src/svc/cache.bin").write_bytes(b"v1")
    git_cmd(root, "add", "-A")
    git_cmd(root, "commit", "-qm", "artefato versionado")
    from tests.conftest import FAST_PYTHON_RECIPE

    recipe = json.loads(json.dumps(FAST_PYTHON_RECIPE))
    recipe["stages"].insert(
        0,
        {
            "id": "touch",
            "kind": "unit",
            "parser": "exitcode",
            "required": False,
            "run": ["python3", "-c", "open('src/svc/cache.bin','wb').write(b'v2')"],
        },
    )
    dump_yaml(root.parents[1] / "nexo-home" / "brain" / "recipes" / "python.yaml", recipe)
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert rep["state"] == "Concluido", rep


def test_qa_clarification_during_story_returns_to_refinement(planned, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    question = {"clarification": {"question": "AC-1 não diz a interface da função", "blocking": True}}
    planned(scenario, [{"tool": "claude", "role": "qa-author", "events": [question]}])
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert rep["state"] == "Refinamento" and "aguardando esclarecimento" in rep["reason"]


def test_answering_the_agent_question_resumes_the_story_without_replanning(planned, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    """Achado na gravação real: o agente perguntou a interface da função; a resposta rodava o planejador de novo,
    mudava os critérios e o `resume` exigia aprovar o plano inteiro de novo, sem passo claro no console."""
    question = {"clarification": {"question": "Qual a assinatura de montar_perfil?", "blocking": True}}
    planned(
        scenario,
        [
            {"tool": "claude", "role": "qa-author", "events": [question]},
            QA,
            impl("T-1-1"),
            impl("T-1-2", path="README.md"),
            *OK_REVIEWS,
        ],
    )
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert rep["state"] == "Refinamento"
    labels = [i["label"] for i in json.loads(nexo("--json", "inbox")[1])["items"]]
    assert "US-1-1: responder a pergunta do agente" in labels
    code, out, err = nexo("resume", "US-1-1")
    assert code != 0 and "responda a pergunta do agente" in out + err
    qid = next(q["id"] for q in json.loads(nexo("--json", "plan", "status", "F-1")[1])["questions"])
    assert nexo("plan", "answer", "F-1", f"{qid}=montar_perfil(nome, email) -> dict")[0] == 0  # sem replanejar
    labels = [i["label"] for i in json.loads(nexo("--json", "inbox")[1])["items"]]
    assert "US-1-1: retomar (pergunta respondida)" in labels
    code, rep = run_json(nexo, "resume", "US-1-1")
    assert rep["state"] in ("AguardandoHumano", "Concluido"), rep


def test_plan_test_task_goes_to_qa_not_to_dev(planned, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    """Achado no teste real com o Claude Code: tarefa de teste do plano escalava, porque o dev não pode alterar os
    testes travados do QA. No test-first ela é do QA: entra no pedido do QA e sai da fila do desenvolvedor."""
    b = {
        "done": {
            "structured": {
                "stories": [{"id": "US-1-1", "title": "Consultar versão", "ac_refs": ["AC-1"], "points": 1}],
                "tasks": [
                    {
                        "id": "T-1-1",
                        "story": "US-1-1",
                        "kind": "backend",
                        "title": "version",
                        "files_hint": ["src/svc/version.py"],
                    },
                    {
                        "id": "T-1-2",
                        "story": "US-1-1",
                        "kind": "test",
                        "title": "limites da versão",
                        "description": "cobrir versão vazia e formato semver",
                        "files_hint": ["tests/test_version.py"],
                        "depends_on": ["T-1-1"],
                    },
                ],
            }
        }
    }
    qa = {**QA, "expect_prompt": "T-1-2 — limites da versão"}
    planned(scenario, [qa, impl("T-1-1"), *OK_REVIEWS], b)  # nenhum passo de dev para T-1-2
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert code == 0 and rep["state"] == "Concluido", rep
    assert rep["tasks"] == {"T-1-1": "Concluido", "T-1-2": "Concluido"}
