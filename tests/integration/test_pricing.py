"""preços por cliente: tabela pública de fábrica, contrato informado, vigências e custo estimado."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import Runner, edit

DONE = {"progress": {"percent": 100}}


def run_copilot(nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    scenario([{"tool": "copilot", "events": [edit(), DONE, {"usage": {"premium_request": 10}}]}])
    nexo("--tool", "copilot", "run", "task", "T-1")


def test_factory_prices_are_flagged_as_estimated(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    run_copilot(nexo, scenario)
    data = json.loads(nexo("--json", "pricing", "show")[1])
    copilot = next(s for s in data["subscriptions"] if s["tool"] == "copilot")
    assert copilot["factory"] and copilot["in_use"] and copilot["current"]["usd_month"] == 19
    assert data["list_prices_as_of"] and data["fx"]["factory"]
    code, out, _ = nexo("--json", "pricing", "check")
    assert code != 0 and {i["item"] for i in json.loads(out)["issues"]} == {"fx", "copilot"}
    cost = json.loads(nexo("--json", "cost", "--by", "tool")[1])
    assert cost["estimated_prices"] == ["copilot"]
    doctor = json.loads(nexo("--json", "doctor")[1])
    pricing = next(c for c in doctor["checks"] if c["name"] == "pricing")
    assert pricing["status"] == "warn" and "copilot" in pricing["detail"]


def test_contract_prices_on_the_project(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    run_copilot(nexo, scenario)
    assert nexo("pricing", "set", "fx", "5,10")[0] == 0
    assert nexo("pricing", "set", "copilot.usd_month", "39")[0] == 0
    assert nexo("pricing", "set", "copilot.unit_price_usd", "0.05")[0] == 0
    code, out, _ = nexo("--json", "pricing", "check")
    assert code == 0 and json.loads(out)["ok"]
    row = json.loads(nexo("--json", "cost", "--story", "T-1")[1])["rows"][0]
    assert row["by_tool"] == {"copilot": 2.55}  # 10 × US$ 0,05 × 5,10
    assert json.loads(nexo("--json", "cost", "--by", "tool")[1])["estimated_prices"] == []
    logs = json.loads(nexo("--json", "logs", "-n", "50")[1])
    assert any(e["type"] == "pricing.set" and e["payload"]["new"] == 39 for e in logs)


def test_new_validity_keeps_history(project: Path, nexo: Runner) -> None:
    nexo("pricing", "set", "devin.usd_month", "400")
    nexo("pricing", "set", "devin.usd_month", "450", "--from", "2026-11-01")
    data = json.loads(nexo("--json", "pricing", "show")[1])
    devin = next(s for s in data["subscriptions"] if s["tool"] == "devin")
    assert devin["versions"] == 2 and devin["current"]["usd_month"] == 400  # a nova vigência ainda não começou
    import yaml

    stored = yaml.safe_load((project / ".nexo/nexo.yaml").read_text(encoding="utf-8"))["pricing"]["subscriptions"][
        "devin"
    ]
    assert [v["usd_month"] for v in stored] == [400, 450]
    assert stored[1]["included_units"] == 250 and stored[1]["overage_usd"] == 2.0  # copiou o restante do contrato


def test_org_price_goes_to_the_brain_clone(project: Path, nexo: Runner, tmp_path: Path) -> None:
    brain = tmp_path / "brain-clone"
    (brain / "org").mkdir(parents=True)
    code, out, err = nexo("--json", "pricing", "set", "cursor.usd_month", "32", "--brain", str(brain))
    data = json.loads(out)
    assert code == 0 and data["level"] == "org" and data["next"], err
    import yaml

    org = yaml.safe_load((brain / "org/config.yaml").read_text(encoding="utf-8"))
    assert org["pricing"]["subscriptions"]["cursor"]["usd_month"] == 32
    squad = nexo("--json", "pricing", "set", "fx", "5.2", "--brain", str(brain), "--squad", "pagamentos")
    assert squad[0] == 0 and (brain / "squads/pagamentos/config.yaml").is_file()


@pytest.mark.parametrize(
    "args,message",
    [
        (("copilot.preco", "1"), "campo desconhecido"),
        (("--", "copilot.usd_month", "-5"), "negativo"),
        (("copilot.usd_month", "abc"), "numérico inválido"),
        (("fx", "0"), "maior que zero"),
        (("fx", "5", "--from", "01/11/2026"), "data inválida"),
    ],
)
def test_invalid_input_is_refused(project: Path, nexo: Runner, args: tuple[str, ...], message: str) -> None:
    code, _out, err = nexo("pricing", "set", *args)
    assert code != 0 and message in err
