"""custo da história ao vivo, projeção, teto, portão de custo e teto por feature."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import Runner
from tests.integration.test_story import OK_REVIEWS, QA, impl, run_json

USAGE = {"usage": {"api_equiv_usd": 1.0}}  # R$ 5,40 por passo (câmbio 5,40)


def paid(step: dict[str, Any]) -> dict[str, Any]:
    return {**step, "events": [*step["events"], USAGE]}


STEPS = [paid(QA), paid(impl("T-1-1")), paid(impl("T-1-2", path="README.md")), *OK_REVIEWS]


def logs(nexo: Runner) -> list[dict[str, Any]]:
    return json.loads(nexo("--json", "logs", "-n", "500")[1])


def cost_decisions(nexo: Runner) -> list[dict[str, Any]]:
    return [e["payload"] for e in logs(nexo) if e["type"] == "gate.decision" and e["payload"].get("gate") == "cost"]


def test_live_cost_projection_and_default_cap(planned, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    planned(scenario, STEPS)
    code, out, _ = nexo("run", "story", "US-1-1")
    assert code == 0 and "R$ 5,40 (projeção R$ 20,40)" in out  # custo ao vivo ao fim de cada fase
    assert "custo  R$ 16,20 de R$ 45,00 (36% do teto) · estimativa R$ 15,00" in out
    code, rep = run_json(nexo, "status", "US-1-1")
    rows = {r["id"]: r for r in json.loads(nexo("--json", "status")[1])}
    assert rows["US-1-1"]["cost_brl"] == 16.2 and rows["US-1-1"]["cap_brl"] == 45.0
    assert cost_decisions(nexo) == []


def test_watch_prints_live_cost_until_story_ends(planned, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    planned(scenario, STEPS)
    nexo("run", "story", "US-1-1")
    code, out, _ = nexo("cost", "--story", "US-1-1", "--watch", "--interval", "1")
    assert code == 0 and out.strip() == "US-1-1 · Concluido · custo R$ 16,20 de R$ 45,00 de teto · projeção R$ 16,20"
    data = json.loads(nexo("--json", "cost", "--story", "US-1-1", "--watch")[1])
    assert data["cost_brl"] == 16.2 and data["state"] == "Concluido"
    assert nexo("cost", "--watch")[0] == 2


def test_cap_pauses_story_and_author_raises_within_limit(planned, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    planned(scenario, STEPS)
    nexo("config", "set", "budget.story_cap.fixed_brl", "6.5", "--project")
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert rep["state"] == "AguardandoHumano" and rep["cost_gate"]["kind"] == "story"
    assert rep["cost_gate"]["cost"] == 16.2 and "teto de custo da história" in rep["reason"]
    assert any(e["type"] == "story.cost_alert" for e in logs(nexo))  # 80% do teto após o QA
    assert run_json(nexo, "resume", "US-1-1")[1]["state"] == "AguardandoHumano"
    code, _, err = nexo("approve", "US-1-1")
    assert code == 1 and "--raise" in err
    code, rep = run_json(nexo, "approve", "US-1-1", "--raise", "200")  # R$ 19,50 ≤ 2 × estimativa (R$ 30)
    assert code == 0 and rep["state"] == "Concluido" and rep["cap_brl"] == 19.5
    assert rep["attempts"] == 0 and rep["bugs"] == []  # RN-103: custo não é falha
    approved = [d for d in cost_decisions(nexo) if d["decision"] == "approved"]
    assert approved[0]["old_cap_brl"] == 6.5 and approved[0]["new_cap_brl"] == 19.5


def test_raise_above_twice_estimate_needs_another_person(
    planned,
    nexo: Runner,
    scenario,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    planned(scenario, STEPS)
    nexo("config", "set", "budget.story_cap.fixed_brl", "8", "--project")
    run_json(nexo, "run", "story", "US-1-1")
    code, _, err = nexo("approve", "US-1-1", "--raise", "400")  # R$ 40 > R$ 30
    assert code == 5 and "segregação" in err
    monkeypatch.setenv("NX_ACTOR", "tech-lead")
    code, rep = run_json(nexo, "approve", "US-1-1", "--raise", "400")
    assert code == 0 and rep["state"] == "Concluido"


def test_economic_profile_after_cost_gate(planned, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    planned(scenario, STEPS)
    nexo("config", "set", "budget.story_cap.fixed_brl", "14", "--project")
    run_json(nexo, "run", "story", "US-1-1")
    code, rep = run_json(nexo, "approve", "US-1-1", "--economic")
    assert code == 0 and rep["state"] == "Concluido" and rep["cap_brl"] == 17.5
    state = json.loads(next((Path.cwd() / ".nexo" / "runs").glob("*/story.json")).read_text(encoding="utf-8"))
    assert state["profile_override"] == "economico"


def test_reject_cost_escalates_without_bug(planned, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    planned(scenario, STEPS)
    nexo("config", "set", "budget.story_cap.fixed_brl", "8", "--project")
    run_json(nexo, "run", "story", "US-1-1")
    code, rep = run_json(nexo, "reject", "US-1-1", "--reason", "custo alto demais para o valor")
    assert rep["state"] == "Escalado" and rep["bugs"] == [] and "custo" in rep["reason"]
    assert [d["decision"] for d in cost_decisions(nexo)] == ["human", "rejected"]


def test_raise_flag_is_refused_on_quality_gate(planned, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    nexo("config", "set", "gates.thresholds.code", "0.99", "--project")
    planned(scenario, STEPS)
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert rep["state"] == "AguardandoHumano" and rep["cost_gate"] is None
    code, _, err = nexo("approve", "US-1-1", "--raise", "10")
    assert code == 1 and "portão de custo" in err


def test_feature_cap_needs_another_person(
    planned,
    nexo: Runner,
    scenario,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    planned(scenario, STEPS)
    nexo("config", "set", "budget.feature_cap_brl", "10", "--project")
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert rep["cost_gate"]["kind"] == "feature" and "teto de custo da feature" in rep["reason"]
    assert nexo("approve", "US-1-1", "--raise", "10")[0] == 5
    monkeypatch.setenv("NX_ACTOR", "gestor")
    code, rep = run_json(nexo, "approve", "US-1-1", "--raise", "100")
    assert code == 0 and rep["state"] == "Concluido"
