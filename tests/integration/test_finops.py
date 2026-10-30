"""custo, rateio, orçamento com bloqueio/retomada, métricas, estimativa e coletor central."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tests.conftest import DEFAULT_TASK, Runner, dump_yaml, edit
from tests.integration.test_story import OK_REVIEWS, QA, SPEC, backlog, impl

DONE = {"progress": {"percent": 100}}


def run_json(nexo: Runner, *args: str) -> tuple[int, dict]:  # type: ignore[type-arg]
    code, out, err = nexo("--json", *args)
    return code, (json.loads(out) if out.strip() else {"err": err})


def test_cost_by_story_dev_tool(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    dump_yaml(project / ".nexo/tasks/T-2.yaml", DEFAULT_TASK)
    scenario(
        [
            {
                "tool": "claude",
                "task": "T-1",
                "events": [edit(), DONE, {"usage": {"api_equiv_usd": 2.0, "tokens_out": 1000}}],
            },
            {"tool": "copilot", "task": "T-2", "events": [edit(), DONE, {"usage": {"premium_request": 5}}]},
        ]
    )
    nexo("run", "task", "T-1")
    nexo("--tool", "copilot", "run", "task", "T-2")
    code, data = run_json(nexo, "cost", "--story", "T-1")
    row = data["rows"][0]
    assert code == 0 and row["key"] == "T-1" and row["by_tool"] == {"claude": 10.8}  # 2 US$ × 5,40
    assert row["units"]["claude:tokens_out"] == 1000 and row["estimated"]
    assert row["allocated_brl"] == 540.0 and data["provisional"]  # único uso do Claude no mês
    t2 = run_json(nexo, "cost", "--story", "T-2")[1]["rows"][0]
    assert t2["by_tool"] == {"copilot": 1.08}  # 5 × 0,04 US$ × 5,40
    by_tool = run_json(nexo, "cost", "--by", "tool")[1]
    assert {r["key"] for r in by_tool["rows"]} == {"claude", "copilot"}
    by_dev = run_json(nexo, "cost", "--dev", __import__("getpass").getuser())[1]
    assert by_dev["total_usage_brl"] == round(10.8 + 1.08, 2)
    assert by_dev["total_allocated_brl"] == round(540.0 + 102.6, 2)  # 100 US$ + 19 US$ (tabela pública) rateados
    code, out, _ = nexo("cost")
    assert "Total: uso R$" in out


def test_budget_show_devin_acu_balance(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    scenario([{"tool": "devin", "events": [edit(), DONE, {"usage": {"acu": 12.5}}]}], tools={"devin": {}})
    nexo("--tool", "devin", "run", "task", "T-1")
    data = run_json(nexo, "budget", "show")[1]
    assert data["units"]["devin"] == {"used": 12.5, "included": 250.0}  # Devin Team (tabela pública)
    _code, out, _ = nexo("budget", "show")
    assert "12.5 de 250 ACU inclusos (saldo 237.5)" in out


def test_banner_shows_budget_and_running_items(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    scenario([{"tool": "claude", "events": [edit(), DONE, {"usage": {"api_equiv_usd": 2.0}}]}])
    nexo("budget", "set", "--limit", "100")
    nexo("run", "task", "T-1")
    dump_yaml(project / ".nexo/tasks/T-2.yaml", DEFAULT_TASK)
    nexo("--tool", "devin", "run", "task", "T-2")  # devin ausente no cenário → Bloqueado
    _code, out, _ = nexo()
    assert "R$ 10,80 de R$ 100,00 (11%)" in out
    assert "Devin 0 de 250 ACU" in out
    assert "1 bloqueado(s)" in out
    data = run_json(nexo)[1]
    assert data["budget"]["spent_brl"] == 10.8 and data["running"] == {"Bloqueado": 1}


def test_close_month_freezes_allocation(project: Path, nexo: Runner, scenario, nexo_home: Path) -> None:  # type: ignore[no-untyped-def]
    scenario([{"tool": "claude", "events": [edit(), DONE, {"usage": {"api_equiv_usd": 1.0}}]}])
    nexo("run", "task", "T-1")
    db = sqlite3.connect(nexo_home / "nexo.db")
    db.execute("UPDATE steps SET started_at = '2026-08-10T10:00:00.000+00:00'")
    db.commit()
    db.close()
    code, out, _ = nexo("cost", "close", "--month", "2026-08")
    assert code == 0 and "R$ 540,00" in out  # assinatura inteira (100 US$) rateada no único uso do mês
    data = run_json(nexo, "cost", "--month", "2026-08")[1]
    assert data["provisional"] == [] and data["total_allocated_brl"] == 540.0
    assert nexo("cost", "close", "--month", "2026-08")[0] == 1
    assert nexo("cost", "close", "--month", "2999-01")[0] == 1


def test_budget_alerts_block_and_resume_when_raised(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    dump_yaml(project / ".nexo/tasks/T-2.yaml", DEFAULT_TASK)
    nexo("budget", "set", "--limit", "10")
    scenario(
        [
            {"tool": "claude", "task": "T-1", "events": [edit(), DONE, {"usage": {"api_equiv_usd": 2.0}}]},
            {"tool": "claude", "task": "T-2", "events": [edit(), DONE]},
        ]
    )
    assert run_json(nexo, "run", "task", "T-1")[1]["state"] == "Concluido"
    status = run_json(nexo, "budget", "show")[1]
    assert status["spent_brl"] == 10.8 and status["ratio"] > 1
    logs = json.loads(nexo("--json", "logs", "-n", "300")[1])
    alerts = [e["payload"]["title"] for e in logs if e["type"] == "notify" and e["payload"]["event"] == "budget"]
    assert any("70%" in a for a in alerts) and any("90%" in a for a in alerts) and any("esgotado" in a for a in alerts)
    _, rep = run_json(nexo, "run", "task", "T-2")
    assert rep["state"] == "Bloqueado" and "orçamento" in rep["reason"] and rep["blocked_until"]
    _, again = run_json(nexo, "resume", "T-2")
    assert again["state"] == "Bloqueado"  # ainda sem limite
    nexo("budget", "set", "--limit", "100")  # gestor aumentou
    code, out, _ = nexo("resume", "--ready")
    assert code == 0 and "T-2" in out
    assert json.loads(nexo("--json", "status", "T-2")[1])["state"] == "Concluido"


def test_tool_specific_budget_excludes_route(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    nexo("budget", "set", "--limit", "5", "--for", "claude")
    dump_yaml(project / ".nexo/tasks/T-2.yaml", DEFAULT_TASK)
    scenario(
        [
            {"tool": "claude", "task": "T-1", "events": [edit(), DONE, {"usage": {"api_equiv_usd": 2.0}}]},
            {"tool": "copilot", "task": "T-2", "events": [edit(), DONE]},
        ]
    )
    nexo("run", "task", "T-1")
    _, rep = run_json(nexo, "run", "task", "T-2")
    assert rep["state"] == "Concluido" and rep["routes"][0].startswith("copilot")
    code, out, _ = nexo("budget", "show")
    assert "esgotado" in out


def test_metrics_estimate_and_calibrate(
    project: Path,
    nexo: Runner,
    scenario,
    fast_recipe: Path,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nexo("config", "set", "gates.thresholds.code", "0.9", "--project")
    usage = {"usage": {"api_equiv_usd": 1.0, "tokens_in": 500, "tokens_out": 500}}
    t1 = impl("T-1-1")
    t1["events"].append(usage)
    scenario(
        [
            {"tool": "claude", "events": [SPEC]},
            {"tool": "claude", "events": [backlog(["src/svc/version.py"], ["README.md"])]},
            QA,
            t1,
            impl("T-1-2", path="README.md"),
            *OK_REVIEWS,
        ]
    )
    nexo("plan", "versão")
    nexo("plan", "approve", "F-1")
    assert run_json(nexo, "run", "story", "US-1-1")[1]["state"] == "Concluido"
    _, m = run_json(nexo, "metrics")
    assert m["done"] == 1 and m["e2e_without_human"] == 1.0 and m["first_pass_yield"] == 1.0
    assert m["brl_per_point"] == 5.4 and m["tokens_per_point"] == 1000 and m["cycle_time_h"] is not None
    code, out, _ = nexo("metrics")
    assert "R$/ponto" in out and "100%" in out
    _, est = run_json(nexo, "estimate", "F-1")
    assert est["points"] == 1 and est["confidence"] == "baixa" and est["brl"] == 15.0
    _, est2 = run_json(nexo, "estimate", "--points", "8")
    assert est2["points"] == 8
    assert nexo("estimate")[0] == 1
    _, cal = run_json(nexo, "gates", "calibrate")
    assert cal["samples"] == 0 and cal["proposal"] == 0.9 and "insuficiente" in cal["note"]


def test_watcher_install_files(project: Path, nexo: Runner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    code, out, _ = nexo("budget", "watch", "--install")
    assert code == 0 and ("launchctl" in out or "schtasks" in out or "crontab" in out)
    code, out, _ = nexo("budget", "watch")
    assert code == 0 and "0 item(ns)" in out
