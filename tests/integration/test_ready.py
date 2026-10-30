"""Definition of Ready como portão e performance como teste do fluxo."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nexo.kernel import ready
from tests.conftest import Runner, dump_yaml
from tests.integration.test_story import OK_REVIEWS, QA, impl, run_json


def refinement() -> dict[str, Any]:
    r = ready.skeleton("US-1-1", ["AC-1"], ["backend", "docs"])
    r["criterios_tecnicos"] = [{"id": "CT-1", "descricao": "version() sem acesso a rede", "verificacao": "teste"}]
    r["testes"]["integracao"] = [{"id": "CI-1", "cenario": "Endpoint /version responde 0.1.0"}]
    r["testes"]["performance"].update(
        cenario="GET /version com 50 req/s por 1 minuto",
        carga={"tipo": "rps", "valor": 50, "duracao": "1m"},
        limites={"p95_ms": 200, "p99_ms": 500, "erro_max_pct": 1},
    )
    r["observabilidade"] = {"logs": ["version.lida"], "metricas": ["version_total"], "alarmes": ["5xx > 1%"]}
    r["deploy"]["rollback"] = "Voltar para a imagem anterior"
    r["estimativa"]["pontos"] = 1
    return r


def test_gate_blocks_until_refinement_is_ready(planned, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    root = planned(scenario, [QA, impl("T-1-1"), impl("T-1-2", path="README.md"), *OK_REVIEWS])
    code, out, _ = nexo("ready", "US-1-1")
    assert code == 1  # a aprovação do plano já criou o refinamento; falta o tech lead completar
    nexo("config", "set", "gates.definition_of_ready", "required", "--project")
    code, _, err = nexo("run", "story", "US-1-1")
    assert code != 0 and "Definition of Ready não atendida" in err and "nexo ready US-1-1" in err
    assert nexo("ready", "US-1-1", "--init")[0] == 0
    path = next(root.glob(".nexo/specs/F-1-*/refinamento/US-1-1.yaml"))
    code, out, _ = nexo("--json", "ready", "F-1")
    problems = json.loads(out)[0]["problems"]
    assert code == 1 and any("performance.cenario" in p for p in problems)
    dump_yaml(path, refinement())
    code, out, _ = nexo("--json", "ready", "F-1")
    assert code == 0 and json.loads(out)[0]["ready"], out
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert code == 0 and rep["state"] == "Concluido"
    logs = json.loads(nexo("--json", "logs", "-n", "400")[1])
    assert any(e["type"] == "ready.checked" and e["payload"]["ready"] for e in logs)


def test_performance_is_a_test_of_the_flow(  # type: ignore[no-untyped-def]
    planned, nexo: Runner, scenario, tmp_path: Path
) -> None:
    root = planned(scenario, [QA, impl("T-1-1"), impl("T-1-2", path="README.md"), *OK_REVIEWS])
    nexo("ready", "US-1-1", "--init")
    dump_yaml(next(root.glob(".nexo/specs/F-1-*/refinamento/US-1-1.yaml")), refinement())
    assert nexo("perf", "script", "US-1-1")[0] == 0
    script = root / "tests/load/us-1-1.js"
    assert "p(95)<200" in script.read_text(encoding="utf-8") and "rate<0.01" in script.read_text(encoding="utf-8")
    good = tmp_path / "ok.json"
    good.write_text(
        json.dumps(
            {
                "metrics": {
                    "http_req_duration": {"p(95)": 120, "p(99)": 300},
                    "http_req_failed": {"value": 0.002},
                    "http_reqs": {"count": 3000},
                }
            }
        ),
        encoding="utf-8",
    )
    code, out, _ = nexo("--json", "perf", "check", "US-1-1", "--summary", str(good))
    assert code == 0 and json.loads(out)["passed"]
    script.write_text(
        script.read_text(encoding="utf-8").replace("p(95)<200", "p(95)<900"), encoding="utf-8"
    )  # afrouxar o limite no script reprova
    code, out, _ = nexo("perf", "check", "US-1-1", "--summary", str(good))
    assert code == 1 and "diferente do refinamento" in out
    script.write_text(script.read_text(encoding="utf-8").replace("p(95)<900", "p(95)<200"), encoding="utf-8")
    slow = tmp_path / "slow.json"
    slow.write_text(
        json.dumps({"metrics": {"http_req_duration": {"p(95)": 450, "p(99)": 900}, "http_req_failed": {"value": 0.0}}}),
        encoding="utf-8",
    )
    code, out, _ = nexo("perf", "check", "US-1-1", "--summary", str(slow))
    assert code == 1 and "p95_ms 450 acima do limite 200" in out
    assert nexo("--json", "perf", "check", "US-1-1", "--summary", str(good))[0] == 0
    code, out, err = nexo("--json", "evidence", "build", "F-1", "--out", str(tmp_path / "ev"))
    assert code == 0, err
    data = json.loads(out)
    folder = Path(data["folder"])
    saved = json.loads((folder / "dados.json").read_text(encoding="utf-8"))["performance"]
    assert saved == [
        {
            "story": "US-1-1",
            "required": True,
            "status": "aprovado",
            "measured": {"p95_ms": 120.0, "p99_ms": 300.0, "erro_max_pct": 0.2, "requisicoes": 3000.0},
            "limits": {"p95_ms": 200, "p99_ms": 500, "erro_max_pct": 1},
        }
    ]
    assert (folder / "documentos/refinamento/US-1-1.yaml").is_file()
    assert "## Performance (teste de carga)" in (folder / "index.md").read_text(encoding="utf-8")
