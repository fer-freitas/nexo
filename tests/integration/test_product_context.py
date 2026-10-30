"""contexto do produto: produto no cérebro (ou rascunho), parte do produto, contexto do repositório,
contexto nos prompts e impacto cruzado ao planejar."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from tests.conftest import Runner, dump_yaml
from tests.integration.test_story import SPEC


def setup_product(nexo: Runner) -> None:
    assert nexo("context", "product", "new", "custodia", "--name", "Custódia", "--description", "posições")[0] == 0
    code, out, err = nexo(
        "context",
        "product",
        "part",
        "custodia",
        "motor-posicao",
        "--kind",
        "componente",
        "--name",
        "Motor de posição",
        "--squad",
        "sq-core",
    )
    assert code == 0, out + err
    assert (
        nexo(
            "context",
            "product",
            "part",
            "custodia",
            "carteira-passiva",
            "--name",
            "Carteira passiva",
            "--uses",
            "motor-posicao",
            "--squad",
            "sq-a",
        )[0]
        == 0
    )


def test_non_interactive_init_links_the_repository_to_the_product(project: Path, nexo: Runner) -> None:
    setup_product(nexo)
    code, out, err = nexo(
        "context",
        "init",
        "--product",
        "custodia",
        "--part",
        "custodia-banco",
        "--kind",
        "visao",
        "--uses",
        "motor-posicao",
        "--what",
        "consulta de posições do banco",
        "--users",
        "backoffice",
    )
    assert code == 0, out + err
    text = (project / ".nexo" / "contexto.md").read_text(encoding="utf-8")
    assert "consulta de posições do banco" in text and "backoffice" in text and "Custódia" in text
    data = json.loads(nexo("--json", "context")[1])
    assert data["product"] == "custodia" and data["part"] == "custodia-banco" and data["official"] is False
    show = json.loads(nexo("--json", "context", "product", "show", "custodia")[1])
    banco = next(p for p in show["parts"] if p["id"] == "custodia-banco")
    assert banco["uses"] == ["motor-posicao"] and banco["kind"] == "visao"
    code, out, _ = nexo("context")
    assert "rascunho local" in out and "Este repositório é visão" in out


def test_wizard_for_a_new_folder(project: Path, nexo: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    answers = ["s", "1", "Custódia", "", "posições e movimentações", "1", "", "2", "calcula posições", "as visões"]
    monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(answers) + "\n"))
    code, out, err = nexo("context", "init")
    assert code == 0, out + err
    data = json.loads(nexo("--json", "context")[1])
    assert data["product"] == "custodia" and data["part"] == project.name.lower().replace("_", "-")
    show = json.loads(nexo("--json", "context", "product", "show", "custodia")[1])
    assert show["parts"][0]["kind"] == "componente" and show["description"] == "posições e movimentações"
    assert "context propose custodia" in out  # rascunho: como levar para os outros squads


def test_context_reaches_the_ai_and_planning_warns_the_cross_impact(
    project: Path, nexo: Runner, scenario, nexo_home: Path
) -> None:  # type: ignore[no-untyped-def]
    setup_product(nexo)
    nexo(
        "context",
        "product",
        "part",
        "custodia",
        "motor-posicao",
        "--kind",
        "componente",
        "--name",
        "Motor de posição",
        "--squad",
        "sq-core",
        "--alias",
        "versão",  # o apelido faz a spec de teste (endpoint de versão) citar o componente
    )
    (nexo_home / "produtos" / "custodia" / "glossario.md").write_text("Posição: saldo de um ativo\n", encoding="utf-8")
    nexo(
        "context",
        "init",
        "--product",
        "custodia",
        "--part",
        "custodia-banco",
        "--uses",
        "motor-posicao",
        "--what",
        "consulta de posições",
        "--users",
        "backoffice",
    )
    scenario([{"tool": "claude", "events": [SPEC], "expect_prompt": "Posição: saldo de um ativo"}])
    code, out, err = nexo("plan", "consulta de versão do serviço")
    assert code == 0, out + err
    assert "impacto no produto" in out and "Carteira passiva" in out and "sq-a" in out


def test_new_folder_suggests_the_context_and_propose_without_brain_repo(
    project: Path, nexo: Runner, scenario, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    scenario([])
    monkeypatch.setattr("sys.stdin", io.StringIO("sair\n"))
    _, out, _ = nexo("console")
    assert "contar ao Nexo sobre este projeto" in out
    setup_product(nexo)
    code, out, _ = nexo("context", "propose", "custodia")
    assert code == 0 and "brain.github_repo" in out


def test_official_product_from_the_brain_is_used_and_copied_on_edit(
    project: Path, nexo: Runner, nexo_home: Path
) -> None:
    folder = nexo_home / "brain" / "produtos" / "custodia"
    dump_yaml(folder / "produto.yaml", {"name": "Custódia", "parts": [{"id": "ui-kit", "kind": "componente"}]})
    data = json.loads(nexo("--json", "context", "product", "list")[1])
    assert data == [{"id": "custodia", "name": "Custódia", "parts": 1, "official": True}]
    nexo(
        "context",
        "init",
        "--product",
        "custodia",
        "--part",
        "extrato",
        "--uses",
        "ui-kit",
        "--what",
        "x",
        "--users",
        "y",
    )
    assert (nexo_home / "produtos" / "custodia" / "produto.yaml").is_file()  # editar cria o rascunho
    assert json.loads(nexo("--json", "context")[1])["official"] is False
