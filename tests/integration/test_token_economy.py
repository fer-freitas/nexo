"""economia de tokens: teto por tarefa, acerto do cache no painel e maiores consumos."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import Runner, edit
from tests.integration.test_run_task import run_json
from tests.integration.test_telemetry import TOKENS

DONE = {"progress": {"percent": 100}}
HEAVY = {"usage": {"tokens_in": 8000, "tokens_out": 500, "cache_read": 40000}}


def test_task_token_cap_escalates(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    nexo("config", "set", "agents.max_task_tokens", "5000")
    scenario(
        [
            {"tool": "claude", "events": [HEAVY, {"error": {"kind": "technical"}}]},
            {"tool": "claude", "events": [edit(), DONE]},
        ]
    )
    _code, rep = run_json(nexo, "run", "task", "T-1")
    assert rep["state"] == "Escalado" and "teto de 5000 tokens" in rep["reason"] and "8500" in rep["reason"]
    logs = json.loads(nexo("--json", "logs", "-n", "100")[1])
    assert any(e["type"] == "run.token_cap" and e["payload"]["used"] == 8500 for e in logs)


def test_cache_read_does_not_count_and_zero_disables(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    nexo("config", "set", "agents.max_task_tokens", "0")
    scenario(
        [
            {"tool": "claude", "events": [HEAVY, {"error": {"kind": "technical"}}]},
            {"tool": "claude", "events": [edit(), DONE]},
        ]
    )
    _code, rep = run_json(nexo, "run", "task", "T-1")
    assert rep["state"] == "Concluido"


def test_cache_hit_kpi_on_finops_page(central: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NX_COLLECTOR_TOKENS", json.dumps(TOKENS))
    from datetime import UTC, datetime

    from nexo.central.common import db

    month = datetime.now(UTC).strftime("%Y-%m")
    for i, (tin, read) in enumerate(((1000, 3000), (1000, 5000))):
        db().execute(
            "INSERT INTO fact_step (event_id, month, tool, tokens_in, cache_read, cache_write, usage_brl)"
            " VALUES (?,?,?,?,?,?,?)",
            (f"e{i}", month, "claude", tin, read, 0, 1.0),
        )
    page = central.get("/v2/pages/finops", headers={"Authorization": "Bearer tok-finops"}).json()
    kpi = next(k for k in page["kpis"] if k["id"] == "cache_hit")
    assert kpi["value"] == 80.0 and kpi["fmt"] == "pct"  # 8000 lidos do cache de 10000 de entrada
