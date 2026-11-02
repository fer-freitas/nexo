"""Falhas achadas ao rodar o roteiro de testes comando a comando. Cada teste reproduz uma delas."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import Runner, dump_yaml
from tests.integration.test_demands_flow import TEAM, as_, j, team_project  # noqa: F401 — fixture
from tests.integration.test_incident import _incident_file


@pytest.fixture
def team(project: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    dump_yaml(project / ".nexo" / "team" / "team.yaml", TEAM)
    return project


def test_tech_idea_has_a_spec_and_features_without_one_explain_themselves(
    team: Path, nexo: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    as_(monkeypatch, "ana")
    fid = j(nexo, "tl", "idea", "Trocar o logger")["id"]
    folder = next((team / ".nexo" / "specs").glob(f"{fid}-*"))
    assert "Trocar o logger" in (folder / "spec.yaml").read_text(encoding="utf-8")
    as_(monkeypatch, "bia")
    j(nexo, "pm", "idea", "Parcelamento")
    j(nexo, "pm", "decide", "IDEA-1", "build", "--reason", "Abandono de carrinho")
    other = j(nexo, "pm", "split", "IDEA-1", "--feature", "Simulador")["features"][0]
    code, _, err = nexo("tl", "analyze", other)
    assert code != 0 and "ainda não tem spec" in err and "erro inesperado" not in err


def test_qa_bug_and_added_task_can_be_run(team_project: Path, nexo: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.integration.test_demands_flow import to_assigned

    story = to_assigned(nexo, monkeypatch)
    as_(monkeypatch, "caio")
    bug = j(nexo, "qa", "bug", story, "Versão errada no rodapé")["id"]
    as_(monkeypatch, "dani")
    added = j(nexo, "dev", "refine", story, "--add-task", "Logar a versão")["changes"][0].split()[1].rstrip(":")
    from nexo.kernel.workspace.tasks import tasks_dir

    for tid in (bug, added):
        assert (tasks_dir(team_project) / f"{tid}.yaml").is_file(), tid
    code, out, err = nexo("--json", "run", "task", added)
    assert "não encontrada" not in err, err


def test_incident_story_is_a_demand_with_incident_origin(project: Path, nexo: Runner, tmp_path: Path) -> None:
    code, out, err = nexo("--json", "incident", "open", str(_incident_file(tmp_path)))
    assert code == 0, err
    story = json.loads(out)["story"]
    board = {r["id"]: r for r in j(nexo, "board", "--origin", "incident")}
    assert story in board and board[story]["origin"] == "incident"
    feature = next(i for i in board if i.startswith("F-"))
    assert board[feature]["stage"] != "functional"  # o incidente já é o requisito aprovado
    code, _, err = nexo("incident", "open", str(tmp_path / "nao-existe.json"))
    assert code != 0 and "não encontrado" in err


def test_events_written_in_sequence_are_read_in_sequence() -> None:
    from nexo.modules.demands.adapters.store import new_event_id

    ids = [new_event_id() for _ in range(500)]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)


def test_dry_run_never_writes_with_a_command_that_cannot_simulate(
    team: Path, nexo: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    as_(monkeypatch, "bia")
    code, _, err = nexo("--dry-run", "pm", "idea", "Não deveria existir")
    assert code == 0 and "Nada foi executado" in err
    assert j(nexo, "board", "--all") == []
    assert nexo("--dry-run", "board")[0] == 0  # leitura roda normalmente


def test_tracker_export_to_a_csv_file_and_back(team: Path, nexo: Runner, tmp_path: Path, monkeypatch: Any) -> None:
    as_(monkeypatch, "ana")
    j(nexo, "tl", "idea", "Trocar o logger")
    target = tmp_path / "saida" / "demandas.csv"
    code, out, err = nexo("tracker", "export", "--out", str(target))
    assert code == 0 and target.is_file(), err
    assert nexo("tracker", "import", str(target))[0] == 0
    code, _, err = nexo("tracker", "import", str(tmp_path / "nao-existe.csv"))
    assert code != 0 and "não encontrado" in err


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("docs", "target", "docusaurus", "--level", "projeto"), "use project ou personal"),
        (("context", "project", "new", "Loja"), "id inválido: 'Loja'"),
        (("metrics", "baseline"), "nexo tracker map --check"),
    ],
)
def test_clear_errors(project: Path, nexo: Runner, args: tuple[str, ...], message: str) -> None:
    code, _, err = nexo(*args)
    assert code != 0 and message in err
    assert "erro inesperado" not in err and "validation error" not in err and "pydantic" not in err


def test_propose_hint_matches_what_is_accepted(
    team_project: Path, nexo: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.integration.test_demands_flow import to_assigned

    story = to_assigned(nexo, monkeypatch)
    as_(monkeypatch, "dani")
    code, _, err = nexo("dev", "refine", story, "--propose", "texto solto")
    assert code != 0 and "tecnica=" in err


def test_qa_verify_checks_the_id_before_the_pipeline(team: Path, nexo: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    as_(monkeypatch, "caio")
    code, out, err = nexo("qa", "verify", "BUG-99")
    assert code != 0 and "demanda desconhecida" in err and "quality" not in out.lower()


def test_board_in_english(team: Path, nexo: Runner) -> None:
    out = nexo("--lang", "en", "board")[1]
    assert "PROJECT" in out and "demand(s)" in out and "PROJETO" not in out


def test_squad_list_includes_your_own_and_project_default_explains_itself(project: Path, nexo: Runner) -> None:
    assert nexo("squad", "set", "sq-demo")[0] == 0
    assert "sq-demo" in json.loads(nexo("--json", "squad", "list")[1])
    data = json.loads(nexo("--json", "squad", "set", "sq-novo", "--project")[1])
    assert "continua valendo sq-demo" in data["aviso"]
