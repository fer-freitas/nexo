"""o trabalho de cada papel: o tech lead analisa o requisito e escreve o requisito técnico com premissas;
o dev vê as tarefas, entende o que cada uma pede e confere se a história está pronta de verdade."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from tests.conftest import Runner, dump_yaml
from tests.integration.test_story import OK_REVIEWS, QA, SPEC, backlog, impl

ANALYSIS = {
    "tool": "claude",
    "role": "analyst",
    "events": [
        {
            "done": {
                "structured": {
                    "summary": "Endpoint simples de versão.",
                    "gaps": [
                        {
                            "text": "origem técnica da versão",
                            "severity": "important",
                            "suggestion": "ler do pacote instalado",
                        }
                    ],
                    "functional_gaps": [
                        {"text": "A versão deve aparecer também para usuários externos?", "severity": "blocking"}
                    ],
                    "risks": [{"title": "versão divergente entre ambientes", "mitigation": "ler do pacote instalado"}],
                    "affected": ["src/svc/version.py"],
                    "effort": "P",
                    "ready_for_tech": False,
                }
            }
        }
    ],
}
ARCHITECT = {
    "tool": "claude",
    "role": "architect",
    "events": [
        {
            "done": {
                "structured": {
                    "premises": [
                        {
                            "text": "A versão do pacote instalado é a fonte da verdade",
                            "why": "é o que vai para produção",
                            "if_false": "ler de variável de ambiente no deploy",
                        }
                    ],
                    "spec_patch": {
                        "design": {
                            "context": "serviço sem endpoint de versão",
                            "solution": "função version() lê o pacote",
                        },
                        "decisions": [{"title": "Ler a versão do pacote", "decision": "importlib.metadata"}],
                    },
                }
            }
        }
    ],
}
BRIEFING = {
    "tool": "claude",
    "role": "briefing",
    "events": [
        {
            "done": {
                "structured": {
                    "what": "Criar a função version() que devolve a versão do serviço.",
                    "why": "AC-1: quem opera precisa saber a versão em produção.",
                    "where": ["src/svc/version.py"],
                    "watch_out": ["não fixar a versão no código"],
                    "how_to_check": ["pytest tests/test_version.py"],
                    "unclear": [],
                }
            }
        }
    ],
}


def test_tech_lead_analyzes_then_writes_the_technical_requirement(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    scenario(
        [
            {"tool": "claude", "events": [SPEC]},
            ANALYSIS,
            {"tool": "claude", "events": [SPEC]},  # refinamento depois da resposta do PM
            ARCHITECT,
        ]
    )
    assert nexo("plan", "consulta de versão do serviço")[0] == 0
    code, out, err = nexo("tech", "analyze", "F-1")
    assert code == 0, out + err
    assert "Lacunas técnicas" in out and "sugestão: ler do pacote instalado" in out
    assert "escaparam do refinamento" in out and "tech return F-1" in out
    assert json.loads(nexo("--json", "plan", "status", "F-1")[1])["questions"] == []  # nada vai sozinho ao PM
    folder = next(project.glob(".nexo/specs/F-1-*"))
    assert (folder / "analise-tecnica.md").is_file()
    labels = [i["label"] for i in json.loads(nexo("--json", "inbox")[1])["items"]]
    assert "F-1: assumir a lacuna funcional como premissa" in labels  # a decisão vira opções numeradas
    assert "F-1: devolver a lacuna funcional ao PM" in labels
    code, out, _ = nexo("tech", "return", "F-1", "--reason", "regra de exposição é do negócio")
    assert code == 0 and "usuários externos" in out and "plan answer F-1" in out
    code, out, err = nexo("tech", "spec", "F-1")
    assert code != 0 and "dúvida(s) bloqueante(s)" in out + err  # sem a resposta do PM, não escreve
    qid = json.loads(nexo("--json", "plan", "status", "F-1")[1])["questions"][0]["id"]
    assert nexo("plan", "answer", "F-1", f"{qid}=não, só interno")[0] == 0
    code, out, err = nexo("tech", "spec", "F-1", "--notes", "siga o padrão do serviço")
    assert code == 0, out + err
    assert "A versão do pacote instalado é a fonte da verdade" in out and "plan approve F-1" in out
    spec = (folder / "spec.yaml").read_text(encoding="utf-8")
    assert "importlib.metadata" in spec and "A versão do pacote instalado é a fonte da verdade" in spec
    doc = (folder / "requisito-tecnico.md").read_text(encoding="utf-8")
    assert "## Premissas" in doc and "se não se confirmar: ler de variável de ambiente" in doc


def test_dev_sees_tasks_understands_them_and_checks_the_story(
    project: Path, nexo: Runner, scenario, fast_recipe: Path, nexo_home: Path
) -> None:  # type: ignore[no-untyped-def]
    nexo("config", "set", "gates.thresholds.code", "0.999", "--project")
    dump_yaml(nexo_home / "brain" / "org" / "config.yaml", {"gates": {"allow_self_approval": True}})
    scenario(
        [
            {"tool": "claude", "events": [SPEC]},
            {"tool": "claude", "events": [backlog(["src/svc/version.py"], ["README.md"])]},
            BRIEFING,
            QA,
            impl("T-1-1"),
            impl("T-1-2", path="README.md"),
            *OK_REVIEWS,
        ]
    )
    nexo("plan", "consulta de versão do serviço")
    assert nexo("plan", "approve", "F-1")[0] == 0
    code, out, _ = nexo("tasks")
    assert code == 0 and "US-1-1" in out and "T-1-1" in out and "T-1-2" in out
    code, out, err = nexo("explain", "T-1-1")
    assert code == 0, out + err
    assert "O quê" in out and "src/svc/version.py" in out and "não fixar a versão" in out
    before = json.loads(nexo("--json", "check", "US-1-1")[1])
    assert before["ok"] is False and any(
        i["label"] == "Tarefas" and i["fix"] == "run story US-1-1" for i in before["items"]
    )
    nexo("run", "story", "US-1-1")
    waiting = json.loads(nexo("--json", "check", "US-1-1")[1])
    gate = next(i for i in waiting["items"] if i["label"] == "Portão")
    assert gate["ok"] is False and gate["fix"] == "approve US-1-1"
    nexo("approve", "US-1-1")
    after = json.loads(nexo("--json", "check", "US-1-1")[1])
    assert after["state"] == "Concluido" and next(i for i in after["items"] if i["label"] == "Pipeline")["ok"] is True
    code, out, _ = nexo("check", "US-1-1")
    assert "Testes de aceite travados" in out


def test_console_speaks_each_role(project: Path, nexo: Runner, scenario, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    from nexo.modules.console.domain.intents import Focus, parse

    commands = {"plan", "tech", "tasks", "explain", "check", "status"}
    assert parse("analisa o requisito da F-1", commands).argv == ("tech", "analyze", "F-1")
    assert parse("cria o requisito técnico da F-2 com as premissas", commands).argv == ("tech", "spec", "F-2")
    assert parse("devolve a F-2 para o PM", commands).argv == ("tech", "return", "F-2")
    assert parse("quais são minhas tarefas?", commands).argv == ("tasks",)
    assert parse("me explica a T-1-2", commands).argv == ("explain", "T-1-2")
    assert parse("confere se está tudo certo", commands, Focus(story="US-1-1")).argv == ("check", "US-1-1")
    found = parse("quero fazer um discovery sobre login com biometria no app", commands)
    assert found.argv == ("plan", "discovery", "login com biometria no app")
    scenario([{"tool": "claude", "events": [SPEC]}])
    nexo("plan", "consulta de versão do serviço")
    monkeypatch.setattr("sys.stdin", io.StringIO("ajuda\nsair\n"))
    _, out, _ = nexo("console")
    assert "F-1: analisar o requisito (tech lead)" in out
    assert "Como PM" in out and "Como tech lead" in out and "Como dev" in out
    monkeypatch.setattr("sys.stdin", io.StringIO("status\nsair\n"))
    _, out, _ = nexo("console")
    after = out.split("→ status", 1)[1]
    assert "Para agora" in after and "1" in after  # depois de uma consulta, as opções numeradas voltam


def test_explain_uses_what_was_decided_and_real_doubts_go_to_the_tech_lead(
    project: Path, nexo: Runner, scenario
) -> None:  # type: ignore[no-untyped-def]
    """Achado no vídeo: a explicação dizia "não está claro" sobre o que a spec e outra história já definiam, e
    reclamava do mapa do repositório."""
    from ruamel.yaml import YAML

    unclear = {
        **BRIEFING,
        "events": [
            {
                "done": {
                    "structured": {
                        **BRIEFING["events"][0]["done"]["structured"],
                        "unclear": ["O endpoint é público ou interno?"],
                    }
                }
            }
        ],
    }
    unclear["expect_prompt"] = "RN-1: versão vem do pacote instalado"
    scenario(
        [
            {"tool": "claude", "events": [SPEC]},
            {"tool": "claude", "events": [backlog(["src/svc/version.py"], ["README.md"])]},
            unclear,
        ]
    )
    nexo("plan", "consulta de versão do serviço")
    assert nexo("plan", "approve", "F-1")[0] == 0
    folder = next(project.glob(".nexo/specs/F-1-*"))
    y = YAML()
    spec = y.load((folder / "spec.yaml").read_text(encoding="utf-8"))
    spec["rules"] = [{"id": "RN-1", "text": "versão vem do pacote instalado", "example": "0.1.0"}]
    with (folder / "spec.yaml").open("w", encoding="utf-8") as fh:
        y.dump(spec, fh)
    code, out, err = nexo("explain", "T-1-1")
    assert code == 0, out + err  # o prompt trouxe a regra da feature (expect_prompt)
    assert (project / ".nexo" / "map").is_dir()  # mapa do código gerado sozinho, sem IA
    labels = [i["label"] for i in json.loads(nexo("--json", "inbox")[1])["items"]]
    assert "T-1-1: levar 1 dúvida ao tech lead" in labels
    code, out, _ = nexo("explain", "T-1-1", "--ask")
    assert code == 0 and "público ou interno" in out and "plan answer F-1" in out
    status = json.loads(nexo("--json", "plan", "status", "F-1")[1])
    question = next(q for q in status["questions"] if q["source"] == "dev")
    assert nexo("plan", "answer", "F-1", f"{question['id']}=interno")[0] == 0  # sem replanejar (sem passo no cenário)
    labels = [i["label"] for i in json.loads(nexo("--json", "inbox")[1])["items"]]
    assert not any("levar" in x for x in labels)


def test_breakdown_review_completes_tasks_and_sends_real_gaps_to_the_tech_lead(
    project: Path, nexo: Runner, scenario, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    """A ambiguidade de uma tarefa é pega na quebra, não quando o dev vai começar."""
    monkeypatch.setenv("NX_REVIEW_BREAKDOWN", "1")
    review = {
        "tool": "claude",
        "role": "breakdown-reviewer",
        "events": [
            {
                "done": {
                    "structured": {
                        "issues": [
                            {
                                "task": "T-1-1",
                                "problem": "não diz a versão",
                                "fix": "A versão retornada é 0.1.0 (AC-1).",
                            },
                            {"task": "T-1-2", "problem": "README sem formato", "question": "Qual o formato no README?"},
                        ]
                    }
                }
            }
        ],
    }
    scenario(
        [
            {"tool": "claude", "events": [SPEC]},
            {"tool": "claude", "events": [backlog(["src/svc/version.py"], ["README.md"])]},
            review,
        ]
    )
    nexo("plan", "consulta de versão do serviço")
    code, out, err = nexo("plan", "approve", "F-1")
    assert code == 0, out + err
    task = (project / ".nexo" / "tasks" / "T-1-1.yaml").read_text(encoding="utf-8")
    assert "A versão retornada é 0.1.0 (AC-1)." in task  # completada com o que a spec define
    question = next(
        q for q in json.loads(nexo("--json", "plan", "status", "F-1")[1])["questions"] if q["source"] == "review"
    )
    assert question["text"].startswith("[T-1-2]")
    assert nexo("plan", "answer", "F-1", f"{question['id']}=lista com a versão no topo")[0] == 0  # sem replanejar
    task2 = (project / ".nexo" / "tasks" / "T-1-2.yaml").read_text(encoding="utf-8")
    assert "Decidido pelo tech lead" in task2 and "lista com a versão no topo" in task2
