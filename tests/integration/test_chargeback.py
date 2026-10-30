"""custo por centro de custo: uso pelo Nexo, uso fora dele e licenças rateadas pelos squads da pessoa."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nexo.central.chargeback import NO_CC
from tests.conftest import Runner
from tests.integration.test_telemetry import TOKENS, enable, login

MONTH = "2026-08"
PEOPLE = {
    **TOKENS,
    "tok-gil": {"role": "manager", "dev": "gil"},
    "tok-dev": {"role": "dev", "dev": "bia"},
    "tok-sso-fin": {"role": "dev", "dev": "fin@empresa.com"},  # papel vem do diretório (finops)
}
DIRECTORY = {
    "directorates": [
        {
            "id": "dir-digital",
            "cost_center": "CC-100",
            "squads": [
                {"id": "sq-a", "cost_center": "CC-110", "managers": ["gil"], "members": ["bia", "caio"]},
                {"id": "sq-b", "members": ["caio"]},  # sem centro próprio: herda o da diretoria
            ],
        },
        {"id": "dir-novos", "squads": [{"id": "sq-c", "cost_center": "=HYPERLINK(1)", "members": ["duda"]}]},
    ],
    "roles": {"gil": "manager", "fin@empresa.com": "finops"},
}
LICENSES = [
    {"dev": "bia", "tool": "devin", "monthly_brl": 1000.0},
    {"dev": "caio", "tool": "copilot", "monthly_brl": 500.0},  # membro de dois squads: metade em cada
    {"dev": "zeca", "tool": "claude", "monthly_brl": 100.0},  # fora do diretório: sem centro de custo
]


def h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def org(central: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("NX_COLLECTOR_TOKENS", json.dumps(PEOPLE))
    assert central.put("/v2/admin/directory", json=DIRECTORY, headers=h("tok-admin")).status_code == 200
    assert central.put("/v2/admin/licenses", json=LICENSES, headers=h("tok-admin")).status_code == 200
    from nexo.central.common import db

    d = db()
    for i, (squad, brl) in enumerate((("sq-a", 10.0), ("sq-b", 20.0), (None, 5.0), ("sq-c", 1.0))):
        d.execute(
            "INSERT INTO fact_step (event_id, month, squad, tool, usage_brl) VALUES (?,?,?,?,?)",
            (f"e{i}", MONTH, squad, "claude", brl),
        )
    d.execute(
        "INSERT INTO fact_step (event_id, month, squad, tool, usage_brl) VALUES ('old', '2026-07', 'sq-a', 'claude', 99)"
    )
    d.execute(
        "INSERT INTO fact_external_usage (device_id, squad, month, day, tool, model, usage_brl, at)"
        " VALUES ('d1', 'sq-a', ?, ?, 'claude', 'sonnet', 3.0, 'x')",
        (MONTH, MONTH + "-10"),
    )
    return central


def test_chargeback_by_cost_center(org: Any) -> None:
    body = org.get(f"/v2/finops/chargeback?month={MONTH}", headers=h("tok-finops")).json()
    rows = {r["cost_center"]: r for r in body["rows"]}
    assert rows["CC-110"] == {
        "cost_center": "CC-110",
        "squads": ["sq-a"],
        "nexo_brl": 10.0,
        "outside_brl": 3.0,
        "licenses_brl": 1250.0,
        "total_brl": 1263.0,
    }
    assert rows["CC-100"]["squads"] == ["sq-b"] and rows["CC-100"]["total_brl"] == 270.0  # herdado da diretoria
    assert rows[NO_CC]["nexo_brl"] == 5.0 and rows[NO_CC]["licenses_brl"] == 100.0
    assert body["rows"][-1]["cost_center"] == NO_CC  # sempre por último, para o FinOps corrigir o diretório
    assert body["total_brl"] == round(sum(r["total_brl"] for r in body["rows"]), 2) == 1639.0


def test_visibility_csv_and_dashboard(org: Any) -> None:
    mine = org.get(f"/v2/finops/chargeback?month={MONTH}", headers=h("tok-gil")).json()
    assert [r["cost_center"] for r in mine["rows"]] == ["CC-110"]  # gestor vê só o squad que gere
    assert org.get(f"/v2/finops/chargeback?month={MONTH}", headers=h("tok-dev")).status_code == 403
    assert org.get("/v2/finops/chargeback?month=agosto", headers=h("tok-finops")).status_code == 400
    csv_text = org.get(f"/v2/finops/chargeback?month={MONTH}&format=csv", headers=h("tok-finops")).text
    lines = csv_text.strip().splitlines()
    assert lines[0].startswith("mes,centro_de_custo,squads") and len(lines) == 5
    assert "'=HYPERLINK(1)" in csv_text  # fórmula neutralizada na planilha
    page = org.get(f"/v2/pages/finops?month_from={MONTH}&month_to={MONTH}", headers=h("tok-finops")).json()
    table = next(t for t in page["tables"] if t["id"] == "chargeback")
    assert table["rows"][0][:2] == ["CC-110", "sq-a"] and table["individual"] is None
    from nexo.central import distribution
    from nexo.central.common import db

    report = distribution.monthly_finops(db(), MONTH, deliver=False)
    assert any(t["id"] == "chargeback" for t in report["tables"])
    assert distribution._top_centers(report["tables"])[0] == ("Centro de custo CC-110", "R$ 1.263,00")


def test_cli_chargeback_with_csv(org: Any, nexo: Runner, nexo_home: Path, tmp_path: Path) -> None:
    enable(nexo_home)
    login(nexo_home, org, "tok-sso-fin")
    out = tmp_path / "cc.csv"
    code, stdout, err = nexo("--json", "cost", "chargeback", "--month", MONTH, "--csv", str(out))
    assert code == 0, err
    data = json.loads(stdout)
    assert data["total_brl"] == 1639.0 and data["csv"] == str(out)
    assert out.read_text(encoding="utf-8").count("\n") == 5
    code, stdout, _ = nexo("cost", "chargeback", "--month", MONTH)
    assert code == 0 and "CC-110" in stdout and "1263.00" in stdout
    assert nexo("cost", "chargeback", "--month", "2026/08")[0] != 0
