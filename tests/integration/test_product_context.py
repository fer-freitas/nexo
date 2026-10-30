"""contexto do projeto: projeto no cérebro (ou rascunho), parte do projeto, contexto do repositório,
contexto nos prompts e impacto cruzado ao planejar."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import Runner, dump_yaml
from tests.integration.test_story import SPEC


def setup_product(nexo: Runner) -> None:
    assert nexo("context", "project", "new", "custodia", "--name", "Custódia", "--description", "posições")[0] == 0
    code, out, err = nexo(
        "context",
        "project",
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
            "project",
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
        "--project",
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
    assert data["project"] == "custodia" and data["part"] == "custodia-banco" and data["official"] is False
    show = json.loads(nexo("--json", "context", "project", "show", "custodia")[1])
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
    assert data["project"] == "custodia" and data["part"] == project.name.lower().replace("_", "-")
    show = json.loads(nexo("--json", "context", "project", "show", "custodia")[1])
    assert show["parts"][0]["kind"] == "componente" and show["description"] == "posições e movimentações"
    assert "context propose custodia" in out  # rascunho: como levar para os outros squads


def test_context_reaches_the_ai_and_planning_warns_the_cross_impact(
    project: Path, nexo: Runner, scenario, nexo_home: Path
) -> None:  # type: ignore[no-untyped-def]
    setup_product(nexo)
    nexo(
        "context",
        "project",
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
    (nexo_home / "projetos" / "custodia" / "glossario.md").write_text("Posição: saldo de um ativo\n", encoding="utf-8")
    nexo(
        "context",
        "init",
        "--project",
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
    assert "impacto no projeto" in out and "Carteira passiva" in out and "sq-a" in out


def test_new_folder_suggests_the_context_and_propose_without_brain_repo(
    project: Path, nexo: Runner, scenario, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    scenario([])
    monkeypatch.setattr("sys.stdin", io.StringIO("sair\n"))
    _, out, _ = nexo("console")
    assert "contar ao Nexo sobre este repositório" in out
    setup_product(nexo)
    code, out, _ = nexo("context", "propose", "custodia")
    assert code == 0 and "brain.github_repo" in out


def test_official_product_from_the_brain_is_used_and_copied_on_edit(
    project: Path, nexo: Runner, nexo_home: Path
) -> None:
    folder = nexo_home / "brain" / "projetos" / "custodia"
    dump_yaml(folder / "projeto.yaml", {"name": "Custódia", "parts": [{"id": "ui-kit", "kind": "componente"}]})
    data = json.loads(nexo("--json", "context", "project", "list")[1])
    assert data == [{"id": "custodia", "name": "Custódia", "parts": 1, "official": True}]
    nexo(
        "context",
        "init",
        "--project",
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
    assert (nexo_home / "projetos" / "custodia" / "projeto.yaml").is_file()  # editar cria o rascunho
    assert json.loads(nexo("--json", "context")[1])["official"] is False


def test_enrich_from_a_document_only_adds_what_was_accepted(
    project: Path, nexo: Runner, scenario: Any, tmp_path: Path
) -> None:
    source = tmp_path / "regras.md"
    source.write_text("Fechamento só depois das 18h.", encoding="utf-8")
    answer = {
        "done": {
            "structured": {
                "facts": ["Fechamento só depois das 18h"],
                "terms": [{"term": "D+1", "meaning": "um dia útil depois"}],
                "questions": ["Quem aprova o fechamento?"],
            }
        }
    }
    scenario([{"tool": "claude", "events": [answer]}, {"tool": "claude", "events": [answer]}])
    code, out, err = nexo("--json", "context", "enrich", "--from", str(source))
    assert code == 0, err + out
    data = json.loads(out)
    assert data["applied"] == 0 and data["questions"] == ["Quem aprova o fechamento?"]
    assert "(o que este repositório faz)" in " ".join(f"({g})" for g in data["gaps"])
    assert "18h" not in (project / ".nexo" / "contexto.md").read_text(encoding="utf-8")
    code, out, err = nexo("context", "enrich", "--from", str(source), "--yes")
    assert code == 0, err + out
    text = (project / ".nexo" / "contexto.md").read_text(encoding="utf-8")
    assert "- Fechamento só depois das 18h" in text and "**D+1:** um dia útil depois" in text


def test_add_and_term_grow_the_context_and_refuse_secrets(project: Path, nexo: Runner) -> None:
    assert nexo("context", "add", "Não mexer no legado de boletos")[0] == 0
    assert nexo("context", "add", "Não mexer no legado de boletos")[0] == 0
    assert nexo("context", "term", "PMO", "escritório de projetos")[0] == 0
    text = (project / ".nexo" / "contexto.md").read_text(encoding="utf-8")
    assert text.count("Não mexer no legado") == 1 and "**PMO:**" in text
    assert "regras, cuidados e decisões" not in text, "o texto de exemplo sai quando a seção ganha conteúdo"
    code, _, err = nexo("context", "add", "a senha é AKIA1234567890ABCDEF")
    assert code != 0 and "segredo" in err
