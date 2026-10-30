"""perguntas sobre o código e o cérebro: busca sem IA, resposta com fontes e fontes inventadas descartadas."""

from __future__ import annotations

import json
from pathlib import Path

from nexo.modules.knowledge.app.ask import retrieve, terms
from tests.conftest import Runner, dump_yaml


def seed(project: Path) -> None:
    (project / "src" / "svc" / "pagamento.py").write_text(
        "def estornar(valor):\n    # estorno só até 90 dias\n    return -valor\n", encoding="utf-8"
    )
    adr = project / "spec" / "adr"
    adr.mkdir(parents=True)
    (adr / "0003-estorno-90-dias.md").write_text


def test_retrieval_finds_code_and_decisions(project: Path) -> None:
    seed(project)
    assert terms("Como funciona o estorno de pagamento?") == ["estorno", "pagamento"]
    found = retrieve(project, "Como funciona o estorno de pagamento?")
    assert found["code"][0] == "src/svc/pagamento.py" and "estornar" in found["code_text"]
    assert found["docs"] == ["spec/adr/0003-estorno-90-dias.md"] and "90 dias" in found["doc_text"]


def test_ask_answers_with_verified_sources(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    seed(project)
    answer = {
        "answer": "O estorno devolve o valor negativo e só vale até 90 dias, pela ADR-0003.",
        "sources": [
            {"path": "src/svc/pagamento.py", "lines": "1-3", "why": "implementação"},
            {"path": "spec/adr/0003-estorno-90-dias.md", "why": "decisão"},
            {"path": "src/svc/nao_existe.py", "why": "inventado"},
            {"path": "../../etc/passwd"},
        ],
        "confidence": "high",
        "unknown": False,
    }
    scenario([{"tool": "claude", "role": "explainer", "events": [{"done": {"structured": answer}}]}])
    code, out, err = nexo("--json", "ask", "Como funciona o estorno de pagamento?")
    assert code == 0, err
    res = json.loads(out)
    assert [s["path"] for s in res["sources"]] == ["src/svc/pagamento.py", "spec/adr/0003-estorno-90-dias.md"]
    assert res["dropped_sources"] == ["src/svc/nao_existe.py", "../../etc/passwd"] and res["confidence"] == "high"
    code, out, _ = nexo("ask", "Como funciona o estorno de pagamento?")  # sem cenário novo: falha controlada
    assert code != 0
    scenario([{"tool": "claude", "events": [{"done": {"structured": {**answer, "sources": [{"path": "x.py"}]}}}]}])
    res = json.loads(nexo("--json", "ask", "Como funciona o estorno de pagamento?")[1])
    assert res["sources"] == [] and res["confidence"] == "low"  # sem fonte válida, confiança baixa


def test_ask_respects_flag(project: Path, nexo: Runner, nexo_home: Path) -> None:
    dump_yaml(nexo_home / "brain" / "org" / "flags.yaml", {"flags": {"ask": False}})
    code, _, err = nexo("ask", "Como funciona o estorno de pagamento?")
    assert code != 0 and "feature flag" in err
