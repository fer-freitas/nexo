"""Definition of Ready: modelo único de refinamento, limites de performance e resultado do k6."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from nexo.kernel import ready


def good() -> dict[str, Any]:
    r = ready.skeleton("US-1-1", ["AC-1", "AC-2"], ["backend", "contract"])
    r["criterios_tecnicos"] = [{"id": "CT-1", "descricao": "Consumidor idempotente pela chave", "verificacao": "teste"}]
    r["testes"]["integracao"] = [{"id": "CI-1", "cenario": "Mensagem gera registro e evento"}]
    r["testes"]["contrato"] = {"aplica": True, "artefato": "contracts/estorno.asyncapi.yaml"}
    r["testes"]["performance"].update(
        cenario="POST /estornos com 200 req/s",
        carga={"tipo": "rps", "valor": 200, "duracao": "10m"},
        limites={"p95_ms": 300, "p99_ms": 800, "erro_max_pct": 0.5},
    )
    r["observabilidade"] = {"logs": ["estorno.solicitado"], "metricas": ["estornos_total"], "alarmes": ["DLQ > 0"]}
    r["deploy"]["rollback"] = "Voltar o alias da Lambda"
    r["mudanca"] = {"risco": "baixo", "candidata_mudanca_padrao": True}
    r["estimativa"]["pontos"] = 3
    return r


def test_complete_refinement_is_ready() -> None:
    rep = ready.check(good(), "US-1-1", ["AC-1", "AC-2"], ["backend", "contract"], {"coverage": 80, "mutation": 60})
    assert rep.ready, rep.problems


def test_skeleton_is_not_ready_and_says_why() -> None:
    rep = ready.check(ready.skeleton("US-1-1", ["AC-1"], ["backend"]), "US-1-1", ["AC-1"], ["backend"])
    text = "\n".join(rep.problems)
    for expected in (
        "critério técnico CT-1",
        "testes.integracao #1",
        "testes.performance.cenario",
        "p95_ms",
        "observabilidade.logs",
        "deploy.rollback",
        "estimativa.pontos",
    ):
        assert expected in text
    assert not ready.check(None, "US-1-1", ["AC-1"], []).ready


@pytest.mark.parametrize(
    "change,problem",
    [
        (lambda r: r.update(criterios_de_aceite=["AC-1"]), "fora do refinamento: AC-2"),
        (lambda r: r["testes"]["performance"].update(aplica=False), "exige justificativa"),
        (lambda r: r["testes"]["performance"]["limites"].update(p99_ms=100), "p99_ms não pode ser menor"),
        (lambda r: r["testes"]["performance"]["carga"].update(duracao="10 minutos"), "duracao no formato"),
        (lambda r: r["testes"]["contrato"].update(aplica=False), "tarefa de contrato"),
        (lambda r: r["testes"]["unitario"].update(cobertura_minima_linhas_alteradas=50), "abaixo do piso"),
        (lambda r: r["mudanca"].update(risco="alto"), "candidata_mudanca_padrao só com risco baixo"),
        (lambda r: r["testes"]["integracao"].clear(), "pelo menos um cenário"),
    ],
)
def test_each_rule(change: Any, problem: str) -> None:
    r = copy.deepcopy(good())
    change(r)
    rep = ready.check(r, "US-1-1", ["AC-1", "AC-2"], ["backend", "contract"], {"coverage": 80})
    assert any(problem in p for p in rep.problems), rep.problems


def test_old_name_of_standard_change_still_accepted() -> None:
    r = good()
    r["mudanca"] = {"risco": "alto", "candidata_low_risk": True}
    rep = ready.check(r, "US-1-1", ["AC-1", "AC-2"], ["backend", "contract"])
    assert any("candidata_mudanca_padrao só com risco baixo" in p for p in rep.problems)


def test_justified_no_performance_is_ready() -> None:
    r = good()
    r["testes"]["performance"] = {"aplica": False, "justificativa_nao_aplica": "Só muda texto de documentação interna"}
    assert ready.check(r, "US-1-1", ["AC-1", "AC-2"], ["backend", "contract"]).ready


def test_k6_script_thresholds_match_and_result() -> None:
    perf = good()["testes"]["performance"]
    script = ready.k6_script("US-1-1", perf)
    assert "p(95)<300" in script and "p(99)<800" in script and "rate<0.005" in script
    assert ready.script_matches(script, perf["limites"]) == []
    loose = script.replace("p(95)<300", "p(95)<900")
    assert ready.script_matches(loose, perf["limites"]) == [
        "threshold p95_ms do script (900) diferente do refinamento (300)"
    ]
    ok = {
        "metrics": {
            "http_req_duration": {"p(95)": 210.0, "p(99)": 640.0},
            "http_req_failed": {"value": 0.001},
            "http_reqs": {"count": 120000},
        }
    }
    res = ready.evaluate_summary(ok, perf["limites"])
    assert res["passed"] and res["measured"]["erro_max_pct"] == 0.1
    slow = {
        "metrics": {
            "http_req_duration": {"values": {"p(95)": 350.0, "p(99)": 700.0}},
            "http_req_failed": {"values": {"rate": 0.02}},
        }
    }
    res = ready.evaluate_summary(slow, perf["limites"])
    assert not res["passed"] and res["problems"] == [
        "p95_ms 350 acima do limite 300",
        "erro_max_pct 2 acima do limite 0.5",
    ]


def test_library_without_observability_and_versioned_package() -> None:
    """Achado no teste real: biblioteca sem processo próprio não tem log/métrica/alarme a declarar."""
    r = good()
    r["observabilidade"] = {"aplica": False, "justificativa_nao_aplica": ""}
    r["deploy"]["estrategia"] = "pacote_versionado"
    problems = ready.check(r, "US-1-1", ["AC-1", "AC-2"], ["backend", "contract"]).problems
    assert any("justificativa_nao_aplica" in p for p in problems)
    assert not any("deploy.estrategia" in p for p in problems)
    r["observabilidade"]["justificativa_nao_aplica"] = "biblioteca pura; o serviço que a usa registra e mede"
    assert ready.check(r, "US-1-1", ["AC-1", "AC-2"], ["backend", "contract"]).ready
