"""feature flags: toda funcionalidade ligada/desligada remotamente, para a empresa ou por squad."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from nexo.kernel.config.flags import CATALOG, CORE_COMMANDS, FlagSource, flag_for_command, resolve, validate
from tests.conftest import DEFAULT_TASK, Runner, dump_yaml, edit
from tests.integration.test_story import OK_REVIEWS, QA, impl, run_json
from tests.integration.test_telemetry import TOKENS, enable, login

DONE = {"progress": {"percent": 100}}


def brain_flags(nexo_home: Path, flags: dict[str, bool], squad: str | None = None) -> None:
    path = nexo_home / "brain" / ("squads/" + squad if squad else "org") / "flags.yaml"
    dump_yaml(path, {"flags": flags})


def test_catalog_and_precedence() -> None:
    keys = [f.key for f in CATALOG]
    assert len(keys) == len(set(keys)) >= 30
    central = FlagSource("central", {"run_story": False}, {"sq-a": {"run_story": True}})
    brain = FlagSource("cérebro", {"planning": False}, {"sq-b": {"planning": True}})
    assert resolve("run_story", "sq-a", [central, brain]).source == "central:squad"
    assert not resolve("run_story", "sq-b", [central, brain]).enabled
    assert resolve("planning", "sq-b", [central, brain]).enabled
    assert resolve("planning", "sq-c", [central, brain]).source == "cérebro"
    assert resolve("insights", "", [central, brain]).source == "padrão"
    assert flag_for_command(["run", "story", "US-1"]).key == "run_story"  # type: ignore[union-attr]
    assert flag_for_command(["budget", "watch"]).key == "watch"  # type: ignore[union-attr]
    assert flag_for_command(["budget", "show"]) is None and flag_for_command(["hook", "run", "x"]) is None
    assert flag_for_command(["approve", "US-1"]) is None and "killswitch" in CORE_COMMANDS
    assert validate({"run_story": False, "nada": True, "planning": "sim"}, "org/flags.yaml") == [
        "org/flags.yaml: flag desconhecida nada",
        "org/flags.yaml: planning deve ser true/false",
    ]


def test_brain_flags_block_commands_globally_and_per_squad(  # type: ignore[no-untyped-def]
    project: Path, nexo: Runner, scenario, nexo_home: Path
) -> None:
    dump_yaml(project / ".nexo" / "tasks" / "T-1.yaml", DEFAULT_TASK)
    brain_flags(nexo_home, {"run_task": False})
    code, _, err = nexo("run", "task", "T-1")
    assert code != 0 and "desligada para a empresa por feature flag" in err and "run_task" in err
    nexo("config", "set", "squad", "sq-piloto", "--project")
    brain_flags(nexo_home, {"run_task": True}, squad="sq-piloto")  # piloto liberado
    scenario([{"tool": "claude", "events": [edit(), DONE]}])
    assert run_json(nexo, "run", "task", "T-1")[1]["state"] == "Concluido"
    listing = json.loads(nexo("--json", "flags", "list")[1])
    row = next(f for f in listing["flags"] if f["key"] == "run_task")
    assert listing["squad"] == "sq-piloto" and row["enabled"] and row["source"] == "cérebro:squad"
    assert nexo("status")[0] == 0 and nexo("flags", "list")[0] == 0  # comandos centrais nunca bloqueiam
    logs = json.loads(nexo("--json", "logs", "-n", "80")[1])
    assert any(e["type"] == "flag.blocked" for e in logs)


def test_feature_flags_inside_flows(planned, nexo: Runner, scenario, nexo_home: Path) -> None:  # type: ignore[no-untyped-def]
    brain_flags(nexo_home, {"handoff": False, "traces": False, "transcripts": False})
    planned(scenario, [QA, impl("T-1-1"), impl("T-1-2", path="README.md"), *OK_REVIEWS])
    brain_flags(nexo_home, {"run_story": False})
    code, _, err = nexo("run", "story", "US-1-1")
    assert code != 0 and "Execução de história" in err
    from nexo.kernel.context import AppContext
    from nexo.kernel.platform import flags as platform_flags

    ctx = AppContext(cwd=Path.cwd(), home=nexo_home)
    with pytest.raises(Exception, match="feature flag"):
        from nexo.modules.execution.app.run import RunOptions
        from nexo.modules.execution.app.story import StoryRunner

        StoryRunner(ctx, RunOptions()).run("US-1-1")  # o `watch`/runner também passam por aqui
    assert not platform_flags.enabled(ctx, "run_story")
    brain_flags(nexo_home, {"transcripts": False, "run_feature": False})
    with pytest.raises(Exception, match="Feature inteira"):
        from nexo.modules.execution.app import feature

        feature.run_feature(AppContext(cwd=Path.cwd(), home=nexo_home), "F-1")
    ctx.close()
    assert not (nexo_home / "transcripts").exists()


def test_brain_rejects_unknown_flags(tmp_path: Path) -> None:
    from nexo.modules.knowledge.app.content import baseline_files, validate_tree

    root = tmp_path / "b"
    (root / "squads" / "sq-a").mkdir(parents=True)
    (root / "org").mkdir()
    (root / "org" / "flags.yaml").write_text("flags: {run_story: false, voar: true}\n", encoding="utf-8")
    (root / "squads" / "sq-a" / "flags.yaml").write_text("flags: {planning: talvez}\n", encoding="utf-8")
    errors = validate_tree(root)
    assert "org/flags.yaml: flag desconhecida voar" in errors
    assert "squads/sq-a/flags.yaml: planning deve ser true/false" in errors
    template = baseline_files()["org/flags.yaml"]
    assert "flags: {}" in template and "#   auto_merge: false" in template


# ---------------------------------------------------------------- central


PEOPLE = {**TOKENS, "tok-nx": {"role": "nexo", "dev": "nx"}}


def test_central_flags_remote_toggle_per_squad(  # type: ignore[no-untyped-def]
    project: Path, nexo: Runner, scenario, nexo_home: Path, central: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NX_COLLECTOR_TOKENS", json.dumps(PEOPLE))
    enable(nexo_home)
    login(nexo_home, central, "tok-sso-bia")
    nexo("config", "set", "squad", "sq-a", "--project")

    def put(
        token: str, key: str, value: bool | None, squad: str | None = None, reason: str = "incidente INC-77 no piloto"
    ) -> Any:
        return central.put(
            f"/v2/admin/flags/{key}",
            json={"value": value, "squad": squad, "reason": reason},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert put("tok-sso-bia", "run_task", False).status_code == 403
    assert put("tok-nx", "run_task", False, reason="curto").status_code == 400
    assert put("tok-nx", "voar", False).status_code == 404
    assert put("tok-nx", "run_task", False, squad="sq-a").status_code == 200
    dump_yaml(project / ".nexo" / "tasks" / "T-1.yaml", DEFAULT_TASK)
    (nexo_home / "flags.json").unlink(missing_ok=True)  # simula o fim do cache de 2 min
    code, _, err = nexo("run", "task", "T-1")
    assert code != 0 and "desligada para o squad sq-a" in err and "central:squad" in err
    # outro squad segue normal; religar volta a valer em até 2 min (cache) — aqui, sem esperar
    assert put("tok-nx", "run_task", None, squad="sq-a").status_code == 200
    (nexo_home / "flags.json").unlink()
    scenario([{"tool": "claude", "events": [edit(), DONE]}])
    assert run_json(nexo, "run", "task", "T-1")[1]["state"] == "Concluido"
    code, out, _ = nexo("--json", "flags", "set", "insights", "off", "--reason", "sem dono no piloto")
    assert code != 0, out  # a CLI do dev não tem papel para mudar flags
    snap = central.get("/v2/flags", headers={"Authorization": "Bearer tok-sso-bia"}).json()
    assert snap == {"global": {}, "squads": {}, "projects": {}}
    from nexo.central.common import db

    hist = db().query("SELECT key, squad, to_value, actor FROM flag_history ORDER BY at")
    assert [(h["key"], h["squad"], h["to_value"]) for h in hist] == [
        ("run_task", "sq-a", 0),
        ("run_task", "sq-a", None),
    ]
    assert db().query("SELECT action FROM audit WHERE action = 'flag.set'")


def test_central_features_obey_flags(central: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from nexo.central import distribution, flags
    from nexo.central.common import db

    monkeypatch.setenv("NX_COLLECTOR_TOKENS", json.dumps(PEOPLE))
    sent: list[Any] = []
    monkeypatch.setattr(distribution, "transport", lambda url, body, headers: sent.append(url) or (200, {}))
    monkeypatch.setenv("NX_COLLECTOR_TEAMS_WEBHOOKS", json.dumps({"default": "https://teams/x"}))
    d = db()
    d.execute("INSERT INTO dim_squad (squad, name, directorate) VALUES ('sq-a', 'A', 'd'), ('sq-b', 'B', 'd')")
    flags.set_flag(d, "weekly_summary", False, "sq-b", "nx", "squad sem canal definido")
    assert [r["target"] for r in distribution.weekly_summary(d)] == ["sq-a"]
    flags.set_flag(d, "dashboard", False, "", "nx", "manutenção do banco")
    h = {"Authorization": "Bearer tok-admin"}
    assert central.get("/v2/pages/executive", headers=h).status_code == 503
    assert central.get("/v2/pages/flags", headers=h).status_code == 200  # a tela de flags segue acessível
    flags.set_flag(d, "bi_export", False, "", "nx", "revisão do DPO")
    assert central.get("/v2/export/fact_step", headers=h).status_code == 503
    flags.set_flag(d, "systemic_issues", False, "", "nx", "issues pausadas")
    assert distribution.systemic_issues(d) == []
    # dashboard: tela de flags com formulário protegido por CSRF
    flags.set_flag(d, "dashboard", None, "", "nx", "manutenção concluída")
    resp = central.post(
        "/login",
        content="token=tok-nx",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    page = central.get("/dash/flags").text
    csrf = re.search(r'name=csrf value="([^"]+)"', page)
    assert csrf and "piso de segurança" in page and "auto_merge" in page
    assert central.post("/dash/flags", content="csrf=x&key=insights&value=off&reason=teste").status_code == 403
    ok = central.post(
        "/dash/flags",
        content=f"csrf={csrf.group(1)}&key=insights&value=off&squad=sq-b&reason=time+em+onboarding",
        follow_redirects=False,
    )
    assert ok.status_code == 303 and not flags.enabled(d, "insights", "sq-b") and flags.enabled(d, "insights", "sq-a")
