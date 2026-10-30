"""padrão de código garantido: comando, configuração da org × projeto, etapa no pipeline e painel."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tests.conftest import Runner, dump_yaml
from tests.integration.test_dashboard import h, people, post, rec, seed  # noqa: F401 — fixture people

INFRA = "import requests\n\n\ndef total(valores):\n    return sum(valores)\n"
LONG = "def grande():\n" + "\n".join(f"    x{i} = {i}" for i in range(40)) + "\n"


def write(project: Path, rel: str, text: str) -> None:
    path = project / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_error_rule_fails_and_warning_only_reports(project: Path, nexo: Runner) -> None:
    write(project, "src/svc/domain/estorno.py", INFRA)
    code, out, _ = nexo("--json", "standards", "check")
    data = json.loads(out)
    assert code != 0 and data["status"] == "failed"
    rules = {v["rule"] for v in data["violations"]}
    assert "DDD-DOMAIN-INFRA" in rules and data["standards"]["errors"] >= 1
    assert Path(data["sarif"]).is_file()
    write(project, "src/svc/domain/estorno.py", "def total(valores):\n    return sum(valores)\n")
    write(project, "src/svc/app.py", LONG)
    code, out, _ = nexo("--json", "standards", "check")
    data = json.loads(out)
    assert code == 0 and data["status"] == "passed"
    assert any(v["rule"] == "CLEAN-FUNCAO-LONGA" for v in data["violations"])  # aviso não reprova
    assert 0 < data["standards"]["files_ok"] < data["standards"]["files"]


def test_rules_listed_with_skill(project: Path, nexo: Runner) -> None:
    rules = {r["id"]: r for r in json.loads(nexo("--json", "standards", "rules")[1])}
    assert rules["DDD-DOMAIN-INFRA"]["skill"] == "ddd-tatico" and rules["DDD-DOMAIN-INFRA"]["severity"] == "error"
    assert rules["CLEAN-FUNCAO-LONGA"]["skill"] == "solid-e-clean-code"


def test_project_and_org_layers(project: Path, nexo: Runner, nexo_home: Path) -> None:
    write(project, "src/svc/domain/estorno.py", INFRA)
    dump_yaml(project / ".nexo/standards.yaml", {"rules": [{"id": "DDD-DOMAIN-INFRA", "severity": "warning"}]})
    assert nexo("standards", "check")[0] == 0  # o projeto pode ajustar a própria regra
    (project / ".nexo/standards.yaml").unlink()
    nexo("config", "set", "standards.disabled", "[DDD-DOMAIN-INFRA]", "--project")
    assert nexo("standards", "check")[0] != 0  # desligar regra pelo projeto não vale
    logs = json.loads(nexo("--json", "logs", "-n", "50")[1])
    assert any(e["payload"].get("rule") == "standards-disabled-local" for e in logs if e["type"] == "policy.violation")
    nexo("config", "unset", "standards.disabled", "--project")
    dump_yaml(nexo_home / "brain/org/config.yaml", {"standards": {"disabled": ["DDD-DOMAIN-INFRA"]}})
    assert nexo("standards", "check")[0] == 0  # a org pode desligar
    dump_yaml(
        nexo_home / "brain/standards/python.yaml",
        {
            "rules": [
                {
                    "id": "ORG-SEM-PRINT",
                    "title": "sem print",
                    "kind": "forbidden_pattern",
                    "severity": "error",
                    "params": {"pattern": "^\\s*print\\("},
                }
            ]
        },
    )
    write(project, "src/svc/x.py", "print('oi')\n")
    code, out, _ = nexo("--json", "standards", "check")
    assert code != 0 and any(v["rule"] == "ORG-SEM-PRINT" for v in json.loads(out)["violations"])


def test_stage_runs_in_quality_pipeline(project: Path, nexo: Runner) -> None:
    write(project, "src/svc/domain/estorno.py", INFRA)
    code, out, _ = nexo("--json", "quality", "run", "--stage", "standards")
    stage = next(s for s in json.loads(out)["stages"] if s["id"] == "standards")
    assert code != 0 and stage["status"] == "failed" and stage["checked"] >= 1


def test_panel_shows_adherence_and_rules(people: Any) -> None:
    seed(people)
    std = {
        "files": 10,
        "files_ok": 7,
        "violations": 5,
        "errors": 1,
        "preexisting": 0,
        "rules": {"DDD-DOMAIN-INFRA": 1, "CLEAN-FUNCAO-LONGA": 4},
    }
    payload = {"story": "US-9", "stack": "python", "failed": 1, "findings": {"high": 1}, "standards": std}
    assert len(post(people, "tok-lia", "sq-a", [rec("event", "quality.run", payload)])["accepted"]) == 1
    bad = {**payload, "standards": {**std, "rules": {"texto livre com /caminho/arquivo.py": 1}}}
    post(people, "tok-lia", "sq-a", [rec("event", "quality.run", bad)])  # regra fora do formato não vira fato
    page = people.get("/v2/pages/quality", headers=h("tok-lia")).json()
    kpis = {k["id"]: k["value"] for k in page["kpis"]}
    assert (
        kpis["standards"] == 70.0 and kpis["standards_errors"] == 2
    )  # as duas execuções; só a regra inválida fica fora
    tables = {t["id"]: t for t in page["tables"]}
    assert tables["standards_rules"]["rows"][0][:2] == ["CLEAN-FUNCAO-LONGA", 4]
    assert all("caminho" not in str(r) for r in tables["standards_rules"]["rows"])
    export = people.get("/v2/export/quality", headers=h("tok-nx"))
    assert export.status_code in (200, 404)
