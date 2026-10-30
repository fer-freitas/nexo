"""UX e marca: a org troca nome e cores num brand.yaml; o console mostra a história em fases."""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import Runner, dump_yaml
from tests.integration.test_dashboard import _session, people, seed  # noqa: F401 — fixture reaproveitada
from tests.integration.test_story import OK_REVIEWS, QA, SPEC, backlog, impl
from tests.integration.test_telemetry import central, pg_server  # noqa: F401

ACME = {"name": "Acme Dev", "light": {"accent": "#C2185B"}, "dark": {"accent": "#F06292"}}


def test_dashboard_uses_the_org_brand(people: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nexo.central import brand as collector_brand

    seed(people)
    _session(people, "tok-lia")
    page = people.get("/dash/executive").text
    assert "--accent:#2F5FC4" in page and "<title>Visão geral · Nexo</title>" in page  # padrão
    assert 'id="q-spend"' in page and "Precisa de atenção" in page  # perguntas antes do detalhe
    path = tmp_path / "brand.yaml"
    dump_yaml(path, ACME)
    monkeypatch.setenv("NX_BRAND", str(path))
    page = people.get("/dash/executive").text
    assert "--accent:#C2185B" in page and "--accent:#F06292" in page and "Acme Dev" in page
    dump_yaml(path, {"light": {"accent": "rosa"}})  # marca inválida: o dashboard segue com o padrão
    collector_brand._load.cache_clear()
    assert "--accent:#2F5FC4" in people.get("/dash/executive").text
    collector_brand._load.cache_clear()


def test_console_uses_the_brand_from_the_org_brain(
    project: Path, nexo: Runner, nexo_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nexo.cli.app import console

    dump_yaml(nexo_home / "brain" / "brand.yaml", {**ACME, "terminal": {"accent": "#D81B60"}})
    assert nexo("status")[0] == 0
    assert str(console.get_style("nexo.accent").color.name).lower() == "#d81b60"
    dump_yaml(nexo_home / "brain" / "brand.yaml", {"terminal": {"accent": "rosa"}})
    monkeypatch.setenv("NX_BRAND", str(nexo_home / "brain" / "brand.yaml"))
    code, _, err = nexo("status")
    assert code == 0 and "marca ignorada" in err and "cor inválida" in err  # avisa e segue com o padrão


def test_story_runs_in_phases_with_one_line_each(
    project: Path,
    nexo: Runner,
    scenario,
    monkeypatch: pytest.MonkeyPatch,
    fast_recipe: Path,
    nexo_home: Path,
) -> None:  # type: ignore[no-untyped-def]
    nexo("config", "set", "gates.thresholds.code", "0.999", "--project")
    dump_yaml(nexo_home / "brain" / "org" / "config.yaml", {"gates": {"allow_self_approval": True}})
    scenario(
        [
            {"tool": "claude", "events": [SPEC]},
            {"tool": "claude", "events": [backlog(["src/svc/version.py"], ["README.md"])]},
            QA,
            impl("T-1-1"),
            impl("T-1-2", path="README.md"),
            *OK_REVIEWS,
        ]
    )
    assert nexo("plan", "consulta de versão do serviço")[0] == 0
    assert nexo("plan", "approve", "F-1")[0] == 0
    code, out, _ = nexo("run", "story", "US-1-1")
    assert code == 0, out
    lines = [line.strip() for line in out.splitlines()]
    assert any(line.startswith("✔ QA") and "testes de aceite" in line for line in lines), out
    assert any(line.startswith("✔ Dev") and "T-1-1 e T-1-2 em paralelo" in line for line in lines), out
    assert any(line.startswith("✔ Qualidade") for line in lines), out
    assert any(line.startswith("● Portão") and "precisa de você" in line for line in lines), out
    assert "passo 1 em execução" not in out and "✎" not in out  # o passo a passo fica no --verbose e no logs
    assert "R$ 0,00 de R$ 45,00" in out and "nexo approve US-1-1" in out


def test_console_header_and_steps_show_severity_and_role(
    project: Path, nexo: Runner, scenario, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    scenario([])
    monkeypatch.setattr("sys.stdin", io.StringIO("ajuda\nsair\n"))
    code, out, _ = nexo("console")
    first = next(line for line in out.splitlines() if line.startswith("nexo │ "))  # depois da apresentação
    assert code == 0 and "R$ 0,00 no mês" in first
    assert "Para agora" in out and "▌" in out and "No dia a dia" in out
    data = json.loads(nexo("--json", "inbox")[1])
    assert all("level" in item for item in data["items"])


def test_console_presents_the_product_and_directs_the_options(
    tmp_path: Path, nexo: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "novo"
    folder.mkdir()
    monkeypatch.chdir(folder)
    monkeypatch.setattr("sys.stdin", io.StringIO("1\n2\n\nadmin\nsair\n"))  # preparar, planejar (sem ideia), grupo
    code, out, _ = nexo("console")
    assert code == 0, out
    assert "███" in out and "Do pedido ao PR com agentes de IA" in out and "como funciona" in out  # apresentação
    assert "contar ao Nexo sobre este projeto" in out and "planejar uma feature nova" in out  # menu após o init
    assert "Descreva a ideia" in out and "Usage:" not in out and "reject" not in out  # pergunta, não despeja ajuda
    assert "O que fazer em admin" in out and "admin autopilot" in out


def test_every_command_has_a_description() -> None:
    from typer.main import get_command

    from nexo.cli.main import app

    missing: list[str] = []

    def walk(command: Any, path: str) -> None:
        for name, sub in getattr(command, "commands", {}).items():
            here = f"{path} {name}".strip()
            if not (sub.help or sub.short_help) and not sub.hidden:
                missing.append(here)
            walk(sub, here)

    walk(get_command(app), "")
    assert missing == []


def test_ai_work_shows_progress(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    scenario([{"tool": "claude", "events": [SPEC]}])
    code, out, _ = nexo("plan", "consulta de versão do serviço")
    assert code == 0, out
    assert "… planejando a feature com claude" in out and "✔ planejando a feature ·" in out


def test_project_name_reaches_the_dashboard_only_for_who_can_see_it(people: Any) -> None:
    from tests.integration.test_dashboard import h, step, story_snap

    seed(people)
    body = {
        "contract": __import__("nexo.kernel.monitoring.telemetry", fromlist=["CONTRACT"]).CONTRACT,
        "device_id": "",
        "project": "p1",
        "context": {"squad": "sq-a", "stack": "python", "project_name": "loja-checkout"},
        "events": [step("US-9"), story_snap("US-9")],
    }
    assert people.post("/v2/events", json=body, headers=h("tok-bia")).status_code == 200
    _session(people, "tok-lia")  # tech lead do sq-a
    page = people.get("/dash/squad").text
    assert "loja-checkout" in page and '<option value="p1"' in page
    people.get("/logout")
    _session(people, "tok-caio")  # dev do sq-b: não vê o nome de projeto de outro squad
    assert "loja-checkout" not in people.get("/dash/squad").text


def test_cli_sends_the_project_name_unless_the_org_turns_it_off(project: Path, nexo_home: Path) -> None:
    from nexo.kernel.context import AppContext, Options
    from nexo.kernel.platform import telemetry

    ctx = AppContext(options=Options())
    try:
        assert telemetry.batch_context(ctx)["project_name"] == project.name
    finally:
        ctx.close()
    dump_yaml(nexo_home / "brain" / "org" / "config.yaml", {"telemetry": {"project_name": False}})
    ctx = AppContext(options=Options())
    try:
        assert "project_name" not in telemetry.batch_context(ctx)
    finally:
        ctx.close()


def test_feature_flags_filter_by_area_and_sort(people: Any) -> None:
    from nexo.kernel.config.flags import CATALOG

    _session(people, "tok-admin")
    page = people.get("/dash/flags?area=FinOps").text
    finops = [f.key for f in CATALOG if f.area == "FinOps"]
    others = [f.key for f in CATALOG if f.area != "FinOps"]
    assert all(k in page for k in finops) and not any(f"<code class=id>{k}</code>" in page for k in others)
    page = people.get("/dash/flags?sort=key&dir=desc").text
    shown = re.findall(r"<td><code class=id>([a-z_]+)</code>", page)
    assert shown == sorted((f.key for f in CATALOG), reverse=True) and "▼" in page


def test_console_shows_brain_version_and_offers_the_update(
    project: Path, nexo: Runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.integration.test_brain_content import brain_repo, resign, use

    repo, pub, key = brain_repo(tmp_path, nexo, {})
    assert use(nexo, repo, pub)[0] == 0  # cérebro 2026.10.01-1 baixado
    (repo / "org").mkdir(exist_ok=True)
    (repo / "org" / "config.yaml").write_text("max_parallel: 2\n", encoding="utf-8")
    resign(nexo, repo, key, "2026.10.02-1")  # a org publicou uma versão nova
    monkeypatch.setattr("sys.stdin", io.StringIO("\nsair\n"))  # Enter: atualiza
    code, out, err = nexo("console")
    assert code == 0, out + err
    assert "cérebro 2026.10.01-1" in out and "há uma versão nova do cérebro da org" in out
    status = json.loads(nexo("--json", "brain", "status")[1])
    assert status["version"] == "2026.10.02-1"
    monkeypatch.setattr("sys.stdin", io.StringIO("sair\n"))
    _, out, _ = nexo("console")
    assert "versão nova do cérebro" not in out  # atualizado: não pergunta de novo


def test_console_brain_update_can_wait(
    project: Path, nexo: Runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.integration.test_brain_content import brain_repo, resign, use

    repo, pub, key = brain_repo(tmp_path, nexo, {})
    use(nexo, repo, pub)
    (repo / "org").mkdir(exist_ok=True)
    (repo / "org" / "config.yaml").write_text("max_parallel: 2\n", encoding="utf-8")
    resign(nexo, repo, key, "2026.10.02-1")
    monkeypatch.setattr("sys.stdin", io.StringIO("n\nsair\n"))
    _, out, _ = nexo("console")
    assert "digite `brain pull` quando quiser" in out
    assert json.loads(nexo("--json", "brain", "status")[1])["version"] == "2026.10.01-1"


@pytest.mark.parametrize(
    ("path", "token", "questions"),
    [
        ("/dash/finops", "tok-fin", ("spend", "licenses", "through_nexo", "cache")),
        ("/dash/squad", "tok-lia", ("done", "waiting", "rework", "estimate")),
        ("/dash/quality", "tok-sec", ("risk", "standards", "coverage", "auto")),
    ],
)
def test_costs_squads_and_quality_answer_questions_first(
    people: Any,
    path: str,
    token: str,
    questions: tuple[str, ...],
) -> None:
    seed(people)
    _session(people, token)
    page = people.get(path).text
    assert all(f'id="q-{q}"' in page for q in questions), path
    assert "Precisa de atenção" in page
    assert page.index('id="q-') < page.index("<table")  # perguntas antes do detalhe


def test_page_answers_flag_what_is_off_track() -> None:
    from nexo.central.metrics.pages import answers

    q, todo = answers.finops(
        800.0, 1200.0, 1000.0, 5000.0, 200.0, 20.0, 3.0, [{"tool": "devin", "diff_pct": 9.0}], "outubro"
    )
    status = {h["id"]: h["status"] for h in q}
    assert status == {"spend": "bad", "licenses": "warn", "through_nexo": "neutral", "cache": "warn"}
    assert [t["title"] for t in todo] == [
        "Projeção acima do orçamento",
        "Assinaturas pouco usadas",
        "ACU do Devin acima do incluso",
        "Fatura de devin diverge do medido",
    ]
    q, todo = answers.finops(10.0, 20.0, 0.0, 0.0, 0.0, None, 0.0, [], "outubro")
    assert {h["id"]: h["status"] for h in q}["licenses"] == "neutral" and todo[0]["action"] == "nexo cost budget set"
    stories = {"done": 4, "waiting_hours": 80.0, "fpy": 75.0}
    q, todo = answers.squad(stories, 2, [(100.0, 180.0)], ["US-1"], ["US-2"], 1, ("cota", 3))
    status = {h["id"]: h["status"] for h in q}
    assert status == {"done": "neutral", "waiting": "bad", "rework": "warn", "estimate": "bad"}
    assert len(todo) == 4 and todo[0]["level"] == "bad"
    q, todo = answers.squad({"done": 0}, 0, [], [], [], 0, None)
    assert {h["status"] for h in q} == {"neutral"} and todo == []
    q, todo = answers.quality(50.0, 1, 2, 70.0, 55.0, 2, 1, 1)
    status = {h["id"]: h["status"] for h in q}
    assert status == {"risk": "bad", "standards": "bad", "coverage": "bad", "auto": "neutral"}
    assert [t["level"] for t in todo] == ["bad", "warn", "warn", "info"]
    q, todo = answers.quality(None, 0, 0, None, None, 0, 0, 0)
    assert {h["id"]: h["status"] for h in q}["risk"] == "good" and todo == []
