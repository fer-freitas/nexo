"""aprovação pelo Teams: portão humano vira cartão com formulário no central; o watch aplica a decisão."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import Runner
from tests.integration.test_story import OK_REVIEWS, QA, impl, run_json
from tests.integration.test_telemetry import TOKENS, enable, login

PEOPLE = {**TOKENS, "tok-ci": {"role": "ci", "dev": "teams-flow"}}


def gated_story(planned, nexo: Runner, scenario, nexo_home: Path, central: Any, monkeypatch: pytest.MonkeyPatch) -> str:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("NX_COLLECTOR_TOKENS", json.dumps(PEOPLE))
    enable(nexo_home)
    login(nexo_home, central, "tok-sso-bia")  # a Bia dispara a história
    planned(scenario, [QA, impl("T-1-1"), impl("T-1-2", path="README.md"), *OK_REVIEWS])
    nexo("config", "set", "gates.thresholds.code", "0.999", "--project")  # força o portão humano
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert code == 0 and rep["state"] == "AguardandoHumano", rep
    pending = json.loads((nexo_home / "approvals-remote.json").read_text(encoding="utf-8"))
    (aid,) = pending
    return aid


def apply(nexo_home: Path) -> list[dict[str, str]]:
    from nexo.kernel.context import AppContext
    from nexo.modules.delivery.app import remote_approval

    ctx = AppContext(cwd=Path.cwd(), home=nexo_home)
    try:
        return remote_approval.apply(ctx)
    finally:
        ctx.close()


def test_teams_card_decision_and_segregation(  # type: ignore[no-untyped-def]
    planned, nexo: Runner, scenario, nexo_home: Path, central: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    aid = gated_story(planned, nexo, scenario, nexo_home, central, monkeypatch)
    logs = json.loads(nexo("--json", "logs", "-n", "300")[1])
    note = next(e for e in logs if e["type"] == "notify" and "aguardando humano" in e["payload"]["title"])
    from nexo.central.common import db

    row = db().one("SELECT gate, decision FROM remote_approvals WHERE id = ?", (aid,))
    assert row == {"gate": "code", "decision": None} and note["payload"]["channels"]
    decide = lambda token, **body: central.post(  # noqa: E731
        f"/v2/approvals/{aid}/decide", json=body, headers={"Authorization": f"Bearer {token}"}
    )
    assert decide("tok-sso-bia", decision="approve").status_code == 403  # quem disparou não decide
    assert decide("tok-sso-caio", decision="reject").status_code == 400  # reprovação sem motivo
    assert decide("tok-ci", decision="approve", by="caio@empresa.com").status_code == 200  # cartão do Teams
    assert decide("tok-sso-caio", decision="approve").status_code == 409
    applied = apply(nexo_home)
    assert applied == [{"story": "US-1-1", "decision": "approve", "by": "sso:caio@empresa.com"}]
    st = json.loads(nexo("--json", "status", "US-1-1")[1])
    assert st["state"] == "Concluido"
    gates = [e["payload"] for e in json.loads(nexo("--json", "logs", "US-1-1")[1]) if e["type"] == "gate.decision"]
    assert gates[-1]["approver"] == "sso:caio@empresa.com" and gates[-1]["approver_verified"]
    assert db().query("SELECT * FROM remote_approvals") == []  # entregue e apagada do central


def test_form_with_sso_rejects_with_reason(  # type: ignore[no-untyped-def]
    planned, nexo: Runner, scenario, nexo_home: Path, central: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    aid = gated_story(planned, nexo, scenario, nexo_home, central, monkeypatch)
    resp = central.post(
        "/login",
        content="token=tok-sso-caio",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    page = central.get(f"/v2/approvals/{aid}/form").text
    csrf = re.search(r'name=csrf value="([^"]+)"', page)
    assert csrf and "Aprovar" in page and "Reprovar" in page
    ok = central.post(
        f"/v2/approvals/{aid}/form",
        content=f"csrf={csrf.group(1)}&decision=reject&reason=faltou+teste+de+erro",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert ok.status_code == 200 and "reprovada" in ok.text
    applied = apply(nexo_home)
    assert applied[0]["decision"] == "reject"
    bugs = list((Path.cwd() / ".nexo" / "bugs").glob("B-*.yaml"))
    assert bugs and "faltou teste de erro" in bugs[0].read_text(encoding="utf-8")


def test_without_central_the_terminal_flow_stays(planned, nexo: Runner, scenario, nexo_home: Path) -> None:  # type: ignore[no-untyped-def]
    planned(scenario, [QA, impl("T-1-1"), impl("T-1-2", path="README.md"), *OK_REVIEWS])
    nexo("config", "set", "gates.thresholds.code", "0.999", "--project")
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert rep["state"] == "AguardandoHumano" and not (nexo_home / "approvals-remote.json").exists()
