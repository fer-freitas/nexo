"""Guarda instalada sozinha e próximos passos que faltavam no console: entrega (evidências e pedido de mudança), tracker por CSV,
configuração do projeto e recusa de uma parte do plano."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from nexo.kernel.context import AppContext, Options
from nexo.modules.setup.app import autopilot, guard, pending
from tests.conftest import Runner, dump_yaml
from tests.integration.test_story import SPEC, backlog


def _claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: dict | None = None) -> Path:  # type: ignore[type-arg]
    folder = tmp_path / ".claude"
    folder.mkdir(exist_ok=True)
    if settings is not None:
        (folder / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(folder))
    return folder / "settings.json"


def _labels(ctx: AppContext) -> dict[str, tuple[str, ...]]:
    return {s.label: s.argv for s in pending.next_steps(ctx, limit=50)}


def test_autopilot_installs_the_guard_and_says_so(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _claude(tmp_path, monkeypatch)
    ctx = AppContext(options=Options())
    try:
        result = autopilot.run(ctx, now=1_000_000.0)
        assert "guard" in result["done"] and guard.installed(ctx)
        assert any(a["command"] == "guard status" for a in result["alerts"])
        events = [e.type for e in ctx.events.recent(limit=5000)]
        assert "guard.auto_installed" in events
        again = autopilot.run(ctx, now=1_000_000.0 + 2 * 86400)  # já instalada: nada a dizer
        assert not any(a["command"].startswith("guard") for a in again["alerts"])
    finally:
        ctx.close()
    assert "hook run guard" in settings.read_text(encoding="utf-8")


def test_foreign_guard_is_never_replaced(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    other = {"type": "command", "command": "/opt/outro/.venv/bin/python -m outro hook run guard"}
    settings = _claude(tmp_path, monkeypatch, {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [other]}]}})
    ctx = AppContext(options=Options())
    try:
        assert guard.auto_install(ctx) == "foreign" and not guard.installed(ctx)
        result = autopilot.run(ctx, now=1_000_000.0)
        assert any(a["command"] == "guard install" for a in result["alerts"])  # a pessoa decide
    finally:
        ctx.close()
    assert json.loads(settings.read_text(encoding="utf-8"))["hooks"]["PreToolUse"][0]["hooks"] == [other]


def test_org_can_turn_off_the_automatic_guard(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, nexo_home: Path
) -> None:
    _claude(tmp_path, monkeypatch)
    dump_yaml(nexo_home / "brain" / "org" / "config.yaml", {"guard": {"auto": False}})
    ctx = AppContext(options=Options())
    try:
        assert guard.auto_install(ctx) == "disabled" and not guard.installed(ctx)
    finally:
        ctx.close()


def test_delivered_feature_suggests_evidence_then_change(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    scenario(
        [
            {"tool": "claude", "events": [SPEC]},
            {"tool": "claude", "events": [backlog(["src/svc/version.py"], ["README.md"])]},
        ]
    )
    assert nexo("plan", "versão")[0] == 0
    assert nexo("plan", "approve", "F-1")[0] == 0
    ctx = AppContext(options=Options())
    try:
        assert "F-1: montar o pacote de evidências" not in _labels(ctx)  # história ainda não concluída
        from nexo.store.runs import RunStore, project_key

        store = RunStore(ctx.conn, project_key(project))
        for row in ctx.conn.execute("SELECT id FROM work_items WHERE type = 'story'"):
            store.set_item_state(str(row["id"]), "Concluido")
        assert _labels(ctx)["F-1: montar o pacote de evidências"] == ("audit", "evidence", "build", "F-1")
        (project / ".nexo" / "evidencias" / "F-1-20261008-1").mkdir(parents=True)
        assert "F-1: abrir o pedido de mudança" not in _labels(ctx)  # sem ITSM configurado, só as evidências
        nexo("config", "set", "change.channel", "servicenow", "--project")
        assert _labels(ctx)["F-1: abrir o pedido de mudança"] == ("change", "open", "F-1")
    finally:
        ctx.close()


def test_tracker_by_csv_asks_to_bring_the_states_back(project: Path, nexo: Runner) -> None:
    nexo("config", "set", "tracker.kind", "file", "--project")
    folder = project / ".nexo" / "tracker"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "outbox.csv").write_text("ID,Title\nUS-1,x\n", encoding="utf-8")
    ctx = AppContext(options=Options())
    try:
        steps = {s.label: s for s in pending.next_steps(ctx, limit=50)}
        step = steps["tracker por arquivo: importar o outbox e trazer os estados de volta"]
        assert step.argv == ("tracker", "import") and step.needs == "<arquivo.csv>"
        (folder / "inbox.csv").write_text("ID,State\n", encoding="utf-8")
        os.utime(folder / "inbox.csv", None)  # trazido depois do último outbox
        assert "tracker por arquivo: importar o outbox e trazer os estados de volta" not in _labels(ctx)
    finally:
        ctx.close()


def test_fresh_project_suggests_the_map_after_planning(project: Path) -> None:
    ctx = AppContext(options=Options())
    try:
        labels = list(_labels(ctx))
        assert labels.index("planejar uma feature nova") < labels.index("mapear o código existente")
        assert not any("CI" in label for label in labels), "o Nexo não cria workflow"
        (project / ".nexo" / "map").mkdir(parents=True)
        assert "mapear o código existente" not in list(_labels(ctx))
    finally:
        ctx.close()


def test_feature_in_refinement_without_data_class_asks_for_it(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    scenario([{"tool": "claude", "events": [SPEC]}])
    assert nexo("plan", "versão")[0] == 0
    spec = next(project.glob(".nexo/specs/F-1-*/spec.yaml"))
    text = spec.read_text(encoding="utf-8")
    spec.write_text("\n".join(ln for ln in text.splitlines() if not ln.startswith("data_class:")) + "\n", "utf-8")
    ctx = AppContext(options=Options())
    try:
        steps = {s.label: s for s in pending.next_steps(ctx, limit=50)}
        step = steps["F-1: decidir a classe de dados"]
        assert step.argv == ("plan", "classify", "F-1") and step.needs == "<classe>"
    finally:
        ctx.close()
    assert nexo("plan", "classify", "F-1", "internal", "--reason", "sem dado de cliente")[0] == 0
    ctx = AppContext(options=Options())
    try:
        assert "F-1: decidir a classe de dados" not in _labels(ctx)
    finally:
        ctx.close()


def test_local_tracker_with_approved_plan_suggests_the_csv_export(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    scenario(
        [
            {"tool": "claude", "events": [SPEC]},
            {"tool": "claude", "events": [backlog(["src/svc/version.py"], ["README.md"])]},
        ]
    )
    nexo("plan", "versão")
    nexo("plan", "approve", "F-1")
    ctx = AppContext(options=Options())
    try:
        assert _labels(ctx)["exportar as histórias para a ferramenta de gestão"] == ("tracker", "export")
        assert nexo("tracker", "export")[0] == 0
        assert "exportar as histórias para a ferramenta de gestão" not in _labels(ctx)
    finally:
        ctx.close()


def test_incomplete_tracker_mapping_is_a_problem(project: Path, nexo: Runner) -> None:
    nexo("config", "set", "tracker.kind", "file", "--project")
    dump_yaml(
        project / ".nexo" / "mappings" / "file.yaml",
        {"tracker": "file", "types": {"story": "User Story"}, "states": {}},
    )
    ctx = AppContext(options=Options())
    try:
        steps = {s.label: s for s in pending.next_steps(ctx, limit=50)}
        assert steps["de-para com o tracker incompleto"].argv == ("tracker", "map", "--check")
        assert steps["de-para com o tracker incompleto"].level == "problem"
    finally:
        ctx.close()


def test_finops_steps_for_estimated_prices_and_no_budget(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nexo.kernel.finops.costs import BudgetStatus
    from nexo.modules.finops.app import costs, pricing

    monkeypatch.setattr(pricing, "check", lambda ctx: [{"item": "fx", "reason": "câmbio de fábrica"}])
    monkeypatch.setattr(costs, "budget_status", lambda ctx: BudgetStatus(None, 12.5, {}, {}))
    ctx = AppContext(options=Options())
    try:
        steps = {s.label: s for s in pending.next_steps(ctx, limit=50)}
        assert steps["informar os preços do contrato"].argv == ("cost", "pricing", "check")
        budget = steps["definir o orçamento do mês"]
        assert budget.argv == ("cost", "budget", "set", "--limit") and budget.needs == "<R$>"
    finally:
        ctx.close()


def test_autopilot_verifies_the_trail_collects_lessons_and_finds_updates(
    project: Path, nexo: Runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nexo.kernel.security.audit import VerifyResult
    from nexo.modules.knowledge.app import learning
    from nexo.modules.setup.app import selfmanage

    nexo("config", "set", "update.manifest", "https://exemplo.com/nexo/manifest.json", "--project")
    monkeypatch.setattr(selfmanage, "read_manifest", lambda src: selfmanage.Artifact("99.0.0", "x", "y", "z"))
    monkeypatch.setattr(learning, "collect", lambda ctx: {})
    monkeypatch.setattr(
        learning, "status", lambda ctx: {"sources": {"bugs": {"events": 3, "pending": 3}}, "lessons": 0}
    )
    ctx = AppContext(options=Options())
    try:
        monkeypatch.setattr(type(ctx.events), "verify", lambda self: VerifyResult(False, 10, 7, "hash diferente"))
        result = autopilot.run(ctx, now=1_000_000.0)
        assert {"audit_verify", "learn_collect", "update_check"} <= set(result["done"]), result["errors"]
        commands = {a["command"] for a in result["alerts"]}
        assert {"audit verify", "learn review", "update"} <= commands
        steps = {s.argv[0]: s for s in pending.next_steps(ctx, limit=50)}
        assert steps["audit"].level == "problem"
        # no dia seguinte as tarefas semanais não rodam de novo: os avisos delas continuam
        later = autopilot.run(ctx, now=1_000_000.0 + 86400 + 1)
        assert "audit_verify" not in later["done"]
        assert {"audit verify", "learn review"} <= {a["command"] for a in later["alerts"]}
    finally:
        ctx.close()
