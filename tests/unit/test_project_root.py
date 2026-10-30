"""Onde fica o projeto: a busca por `.nexo` sobe pelas pastas, mas para na raiz de um repositório git."""

from __future__ import annotations


def test_a_git_repository_inside_a_nexo_project_is_its_own_project(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Achado no autoteste: um repositório novo dentro de uma pasta com .nexo virava parte daquele projeto."""
    from nexo.kernel.context import find_project_root

    (tmp_path / ".nexo").mkdir()
    (tmp_path / ".nexo" / "nexo.yaml").write_text("project: pai\n", encoding="utf-8")
    plain = tmp_path / "docs" / "notas"
    plain.mkdir(parents=True)
    repo = tmp_path / "servico"
    (repo / ".git").mkdir(parents=True)
    (repo / "src").mkdir()
    assert find_project_root(plain) == tmp_path  # subpasta comum continua no projeto de cima
    assert find_project_root(repo / "src") is None  # repositório próprio: projeto novo
    (repo / ".nexo").mkdir()
    (repo / ".nexo" / "nexo.yaml").write_text("project: servico\n", encoding="utf-8")
    assert find_project_root(repo / "src") == repo
