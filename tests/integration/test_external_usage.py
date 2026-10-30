"""IA usada fora do Nexo: leitura local das três ferramentas, resumo do dev e envio agregado ao central."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from nexo.kernel.finops import external
from nexo.modules.usage.adapters import local_sessions
from tests.conftest import Runner, dump_yaml
from tests.integration.test_telemetry import TOKENS, enable, login
from tests.unit.test_copilot_usage import make_db as make_copilot_db
from tests.unit.test_devin_usage import make_db as make_devin_db

TODAY = datetime.now(UTC).strftime("%Y-%m-%d")
MONTH = TODAY[:7]


def claude_line(mid: str, cwd: str, tin: int, tout: int, cread: int = 0, model: str = "claude-sonnet-4-5") -> str:
    return json.dumps(
        {
            "type": "assistant",
            "sessionId": "c-" + cwd.rsplit("/", 1)[-1],
            "cwd": cwd,
            "timestamp": f"{TODAY}T12:00:00.000Z",
            "message": {
                "id": mid,
                "model": model,
                "content": [{"type": "text", "text": "resposta sigilosa"}],
                "usage": {
                    "input_tokens": tin,
                    "output_tokens": tout,
                    "cache_read_input_tokens": cread,
                    "cache_creation_input_tokens": 0,
                },
            },
        }
    )


def make_claude(root: Path, nexo_cwd: str, own_cwd: str) -> Path:
    projects = root / "projects"
    (projects / "p1").mkdir(parents=True)
    lines = [
        claude_line("m-1", own_cwd, 1000, 200, 5000),
        claude_line("m-1", own_cwd, 1000, 200, 5000),  # mesma resposta em duas linhas (dois blocos): conta uma vez
        claude_line("m-2", own_cwd, 500, 100),
        claude_line("m-3", own_cwd, 0, 0),  # erro de API: sem consumo
        claude_line("m-9", nexo_cwd, 300, 30),  # sessão aberta pelo Nexo (worktree)
        json.dumps({"type": "user", "message": {"content": "pergunta sigilosa"}}),
        "linha quebrada",
    ]
    (projects / "p1" / "s.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return projects


def test_domain_rules() -> None:
    assert external.is_nexo_session("/repo/.nexo/worktrees/T-1")
    assert external.is_nexo_session("C:\\repo\\.nexo\\specs\\F-1")
    assert external.is_nexo_session("/home/ana/.nexo/workspace/x", "/home/ana/.nexo")
    assert not external.is_nexo_session("/repo/src", "/home/ana/.nexo")
    prices = external.claude_prices({"claude-sonnet-4-5": {"input": 2, "output": 10}})
    units = {"tokens_in": 1_000_000, "tokens_out": 100_000, "cache_read": 1_000_000}
    assert external.claude_api_usd("claude-sonnet-4-5-20250929", units, prices) == 3.2  # override mais específico
    assert external.claude_api_usd("claude-opus-5", units, prices) == 8.0  # 5 + 2,5 + 0,5
    assert external.claude_api_usd("gpt-5", units, prices) == 0.0


def test_scans_three_tools_and_separates_nexo(tmp_path: Path) -> None:
    own = tmp_path / "meu-repo"
    wt = tmp_path / "repo" / ".nexo" / "worktrees" / "T-1"
    for d in (own, wt):
        d.mkdir(parents=True)
    projects = make_claude(tmp_path / ".claude", str(wt), str(own))
    rows = local_sessions.scan_claude(projects)
    by_cwd = {r.cwd: r for r in rows}
    assert by_cwd[str(own)].responses == 2
    assert by_cwd[str(own)].units == {"tokens_in": 1500.0, "tokens_out": 300.0, "cache_read": 5000.0}
    now = time.time()
    copilot = local_sessions.scan_copilot(make_copilot_db(tmp_path / "session-store.db", own, now))
    assert sum(r.units.get("premium_request", 0) for r in copilot) == pytest.approx(6.33)  # 1,33 + 5 (sessão antiga)
    devin = local_sessions.scan_devin(make_devin_db(tmp_path / "sessions.db", own, int(now), {"total_acu_cost": 2.0}))
    assert {r.session: r.units["acu"] for r in devin} == {"s-1": 2.0, "s-0": 9.0}
    totals = external.aggregate(rows + copilot + devin, str(tmp_path / "home"), external.claude_prices())
    nexo = [t for t in totals if t.nexo]
    assert [(t.tool, t.units) for t in nexo] == [("claude", {"tokens_in": 300.0, "tokens_out": 30.0})]
    claude_out = next(t for t in totals if t.tool == "claude" and not t.nexo)
    assert claude_out.api_equiv_usd == pytest.approx((1500 * 3 + 300 * 15 + 5000 * 0.3) / 1e6)


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, own: Path, wt: Path) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    make_claude(tmp_path / ".claude", str(wt), str(own))
    now = time.time()
    monkeypatch.setenv("NX_COPILOT_DB", str(make_copilot_db(tmp_path / "session-store.db", own, now)))
    monkeypatch.setenv(
        "NX_DEVIN_DB", str(make_devin_db(tmp_path / "sessions.db", own, int(now), {"total_acu_cost": 2.0}))
    )


def test_cli_scan_and_show(nexo: Runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, nexo_home: Path) -> None:
    own, wt = tmp_path / "meu-repo", tmp_path / "repo" / ".nexo" / "worktrees" / "T-1"
    _env(monkeypatch, tmp_path, own, wt)
    code, out, err = nexo("--json", "usage", "scan", "--full")
    assert code == 0, err
    data = json.loads(nexo("--json", "usage", "show", "--month", MONTH)[1])
    assert set(data["tools"]) == {"claude", "copilot", "devin"} and data["shared"] is False
    assert data["tools"]["claude"]["nexo"]["units"] == {"tokens_in": 300.0, "tokens_out": 30.0}
    assert data["tools"]["copilot"]["fora"]["units"]["ai_credits"] == pytest.approx(9.54376)  # inclui a sessão antiga
    assert 0 < data["nexo_share_pct"] < 100
    assert "sigilosa" not in (nexo_home / "nexo.db").read_bytes().decode("latin-1")  # conteúdo nunca é guardado
    code, out, _ = nexo("usage", "show", "--month", MONTH)
    assert code == 0 and "fica só nesta máquina" in out
    dump_yaml(nexo_home / "brain" / "org" / "flags.yaml", {"flags": {"external_usage": False}})
    code, _, err = nexo("usage", "scan")
    assert code != 0 and "feature flag" in err


def test_outside_usage_reaches_finops_page(  # type: ignore[no-untyped-def]
    project: Path, nexo: Runner, central: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, nexo_home: Path
) -> None:
    monkeypatch.setenv("NX_COLLECTOR_TOKENS", json.dumps(TOKENS))
    own, wt = tmp_path / "meu-repo", project / ".nexo" / "worktrees" / "T-1"
    _env(monkeypatch, tmp_path, own, wt)
    enable(nexo_home)  # telemetria ligada, mas sem external_usage: nada de fora do Nexo sai
    nexo("config", "set", "usage.only_known_projects", "false", "--project")
    login(nexo_home, central, "tok-sso-bia")
    assert nexo("telemetry", "flush")[0] == 0
    from nexo.central.common import db

    assert db().query("SELECT * FROM fact_external_usage") == []
    from nexo.kernel.runtime import yamlfile

    org = nexo_home / "brain" / "org" / "config.yaml"
    cfg = yamlfile.load(org)
    cfg["telemetry"]["external_usage"] = True  # a org liga o envio
    dump_yaml(org, cfg)
    assert nexo("telemetry", "flush")[0] == 0
    rows = db().query("SELECT tool, model, sessions, acu, ai_credits, usage_brl FROM fact_external_usage ORDER BY tool")
    assert {r["tool"] for r in rows} == {"claude", "copilot", "devin"}
    raw = json.dumps(db().query("SELECT payload FROM raw_events WHERE kind = 'usage'"))
    assert str(own) not in raw and "sigilosa" not in raw and "meu-repo" not in raw
    page = central.get("/v2/pages/finops", headers={"Authorization": "Bearer tok-finops"}).json()
    outside = next(t for t in page["tables"] if t["id"] == "outside_nexo")
    assert {r[0] for r in outside["rows"]} == {"claude", "copilot", "devin"}
    measured = central.get(f"/v2/finops/measured?month={MONTH}", headers={"Authorization": "Bearer tok-finops"})
    assert measured.json()["usage_brl"]["claude"] > 0
    # nova varredura (hora seguinte) atualiza o retrato do dia em vez de duplicar
    (nexo_home / "local_sessions.json").unlink()
    assert nexo("telemetry", "flush")[0] == 0
    assert len(db().query("SELECT * FROM fact_external_usage")) == len(rows)


def test_with_known_projects_only_they_count(tmp_path: Path, project: Path) -> None:
    from types import SimpleNamespace

    from nexo.kernel.context import AppContext, Options
    from nexo.modules.lineage.app import lineage
    from nexo.modules.usage.app.external_usage import _company_only

    lineage.register_project(project)
    rows = [SimpleNamespace(cwd=str(project / "src")), SimpleNamespace(cwd=str(tmp_path / "pessoal"))]
    assert [r.cwd for r in _company_only(AppContext(options=Options()), rows)] == [str(project / "src")]
