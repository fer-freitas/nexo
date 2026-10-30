"""aprovação parametrizável (Nexo ou externa) e limite do dev que muda fora do Nexo."""

from __future__ import annotations

import getpass
import json
from pathlib import Path
from typing import Any

import pytest

from nexo.kernel.runtime import http
from tests.conftest import DEFAULT_TASK, Runner, dump_yaml, edit
from tests.integration.test_story import OK_REVIEWS, QA, impl, run_json

USAGE = {"usage": {"api_equiv_usd": 1.0}}  # R$ 5,40


def paid(step: dict[str, Any]) -> dict[str, Any]:
    return {**step, "events": [*step["events"], USAGE]}


STEPS = [paid(QA), paid(impl("T-1-1")), paid(impl("T-1-2", path="README.md")), *OK_REVIEWS]


def org(nexo_home: Path, text: str) -> None:
    folder = nexo_home / "brain" / "org"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "config.yaml").write_text(text, encoding="utf-8")


def events(nexo: Runner) -> list[dict[str, Any]]:
    return json.loads(nexo("--json", "logs", "-n", "500")[1])


@pytest.fixture
def approvals_dir(nexo_home: Path, tmp_path: Path) -> Path:
    folder = tmp_path / "aprovacoes"
    org(
        nexo_home,
        f"approvals:\n  gates:\n    quality: external\n    cost: external\n    budget: external\n"
        f"  channel: file\n  file:\n    dir: {folder.as_posix()}\n",
    )
    return folder


def test_quality_gate_waits_for_external_continue(
    planned,
    nexo: Runner,
    scenario,
    approvals_dir: Path,  # type: ignore[no-untyped-def]
) -> None:
    nexo("config", "set", "gates.thresholds.code", "0.99", "--project")
    planned(scenario, STEPS)
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert rep["state"] == "AguardandoHumano" and "aprovação externa AP-1" in rep["reason"]
    request = json.loads((approvals_dir / "AP-1.request.json").read_text(encoding="utf-8"))
    assert request["kind"] == "quality" and request["target"] == "US-1-1" and request["answer"] == "continue ou stop"
    code, _, err = nexo("approve", "US-1-1")
    assert code == 1 and "externa (AP-1)" in err
    assert json.loads(nexo("--json", "approvals", "sync")[1]) == []  # sem sinal ainda
    (approvals_dir / "AP-1.decision").write_text("continue\n", encoding="utf-8")
    synced = json.loads(nexo("--json", "approvals", "sync")[1])
    assert synced == [
        {"request": "AP-1", "kind": "quality", "target": "US-1-1", "decision": "continue", "state": "Concluido"}
    ]
    decision = [e for e in events(nexo) if e["type"] == "gate.decision" and e["payload"].get("source") == "AP-1"]
    assert decision[0]["payload"]["approver"] == "external:file"


def test_external_stop_escalates_without_bug(planned, nexo: Runner, scenario, approvals_dir: Path) -> None:  # type: ignore[no-untyped-def]
    nexo("config", "set", "gates.thresholds.code", "0.99", "--project")
    planned(scenario, STEPS)
    run_json(nexo, "run", "story", "US-1-1")
    (approvals_dir / "AP-1.decision").write_text("stop", encoding="utf-8")
    nexo("approvals", "sync")
    code, rep = run_json(nexo, "status", "US-1-1")
    assert rep["state"] == "Escalado"
    assert not list((Path.cwd() / ".nexo" / "bugs").glob("B-*.yaml"))


def test_cost_gate_external_via_http(
    planned,
    nexo: Runner,
    scenario,
    nexo_home: Path,  # type: ignore[no-untyped-def]
) -> None:
    org(
        nexo_home,
        "approvals:\n  gates:\n    cost: external\n  channel: http\n  http:\n    url: https://aprov.example/api\n",
    )
    state: dict[str, str] = {}

    def transport(method: str, url: str, headers: dict[str, str], data: bytes | None) -> http.Response:
        if method == "POST":
            body = json.loads(data or b"{}")
            state[body["id"]] = "pending"
            return http.Response(201, {"ref": f"CHG-{body['id']}"})
        return http.Response(200, {"status": state[url.rsplit("/", 1)[1]]})

    http.set_transport(transport)
    try:
        planned(scenario, STEPS)
        nexo("config", "set", "budget.story_cap.fixed_brl", "14", "--project")
        code, rep = run_json(nexo, "run", "story", "US-1-1")
        assert rep["cost_gate"]["request"] == "AP-1" and "aguardando aprovação externa AP-1" in rep["reason"]
        assert nexo("approve", "US-1-1", "--raise", "50")[0] == 1
        state["AP-1"] = "continue"
        synced = json.loads(nexo("--json", "approvals", "sync")[1])
        assert synced[0]["state"] == "Concluido"  # teto +50% (R$ 21) cobre os R$ 16,20
        listed = json.loads(nexo("--json", "approvals", "list", "--all")[1])
        assert listed[0]["external_ref"] == "CHG-AP-1" and listed[0]["status"] == "continued"
    finally:
        http.set_transport(None)


def test_budget_external_grant_resumes_blocked_task(
    project: Path,
    nexo: Runner,
    scenario,
    approvals_dir: Path,  # type: ignore[no-untyped-def]
) -> None:
    dump_yaml(project / ".nexo/tasks/T-2.yaml", DEFAULT_TASK)
    scenario(
        [
            {"tool": "claude", "task": t, "events": [edit(), {"progress": {"percent": 100}}, USAGE]}
            for t in ("T-1", "T-2")
        ]
    )
    nexo("budget", "set", "--limit", "5")
    nexo("run", "task", "T-1")  # R$ 5,40 > R$ 5,00
    code, rep = run_json(nexo, "run", "task", "T-2")
    assert rep["state"] == "Bloqueado" and "aprovação externa AP-1" in rep["reason"]
    assert nexo("run", "task", "T-2")[0] in (0, 1)  # nova tentativa não duplica a solicitação
    assert len(json.loads(nexo("--json", "approvals", "list")[1])) == 1
    (approvals_dir / "AP-1.decision").write_text("continue", encoding="utf-8")
    nexo("resume", "--ready")
    assert run_json(nexo, "status", "T-2")[1]["state"] == "Concluido"
    granted = [e for e in events(nexo) if e["type"] == "budget.granted"]
    assert granted[0]["payload"]["limit_brl"] == 1.0  # 20% de R$ 5,00
    assert json.loads(nexo("--json", "budget", "show")[1])["limit_brl"] == 6.0


def test_external_limit_changes_outside_nexo(
    project: Path,
    nexo: Runner,
    scenario,
    nexo_home: Path,
    tmp_path: Path,  # type: ignore[no-untyped-def]
) -> None:
    from nexo.modules.finops.app.costs import current_month

    limits = tmp_path / "limites.yaml"
    dev, month = getpass.getuser(), current_month()
    limits.write_text(f"{dev}:\n  '{month}': 5\n", encoding="utf-8")
    org(nexo_home, f"budget:\n  source: external\n  external:\n    file: {limits.as_posix()}\n    ttl_seconds: 0\n")
    dump_yaml(project / ".nexo/tasks/T-2.yaml", DEFAULT_TASK)
    scenario(
        [
            {"tool": "claude", "task": t, "events": [edit(), {"progress": {"percent": 100}}, USAGE]}
            for t in ("T-1", "T-2")
        ]
    )
    nexo("run", "task", "T-1")
    assert run_json(nexo, "run", "task", "T-2")[1]["state"] == "Bloqueado"
    limits.write_text(f"{dev}:\n  '{month}': 50\n", encoding="utf-8")  # aprovado fora do Nexo
    nexo("resume", "--ready")
    assert run_json(nexo, "status", "T-2")[1]["state"] == "Concluido"
    assert json.loads(nexo("--json", "budget", "show")[1])["limit_brl"] == 50.0


def test_dev_cannot_move_approvals_or_limits_out_of_nexo(
    planned,
    nexo: Runner,
    scenario,
    tmp_path: Path,  # type: ignore[no-untyped-def]
) -> None:
    nexo("config", "set", "gates.thresholds.code", "0.99", "--project")
    nexo("config", "set", "approvals.mode", "external", "--project")
    nexo("config", "set", "budget.source", "external")
    nexo("config", "set", "budget.external.file", str(tmp_path / "meu-limite.yaml"))
    planned(scenario, STEPS)
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert rep["state"] == "AguardandoHumano" and "externa" not in rep["reason"]  # continua no Nexo
    reasons = [str(e["payload"].get("reason")) for e in events(nexo) if e["type"] == "policy.violation"]
    assert any("approvals.mode" in r for r in reasons) and any("budget.source" in r for r in reasons)


def test_manual_signal_relay_respects_segregation(
    planned,
    nexo: Runner,
    scenario,
    approvals_dir: Path,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    nexo("config", "set", "gates.thresholds.code", "0.99", "--project")
    planned(scenario, STEPS)
    run_json(nexo, "run", "story", "US-1-1")
    code, _, err = nexo("approvals", "signal", "AP-1", "continue")
    assert code == 5 and "segregação" in err
    monkeypatch.setenv("NX_ACTOR", "operacoes")
    code, out = run_json(nexo, "approvals", "signal", "AP-1", "continue")
    assert code == 0 and out["state"] == "Concluido"
    assert nexo("approvals", "signal", "AP-1", "stop")[0] == 1  # já resolvida
