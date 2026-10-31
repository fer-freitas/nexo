"""`nexo docs portal`: lê os repositórios do projeto, gera o site e aponta o que o código desmente."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import Runner, dump_yaml


@pytest.fixture
def repos(project: Path, nexo: Runner, nexo_home: Path, tmp_path: Path) -> Path:
    dump_yaml(nexo_home / "brain" / "org" / "flags.yaml", {"flags": {"docsite": True}})
    assert nexo("context", "project", "new", "custodia", "--name", "Custódia", "--sigla", "CDT")[0] == 0
    part = ("context", "project", "part", "custodia")
    assert nexo(*part, "ui-kit", "--kind", "componente", "--name", "UI Kit", "--squad", "sq-a")[0] == 0
    assert nexo(*part, "extrato", "--kind", "componente", "--name", "Extrato", "--squad", "sq-a")[0] == 0
    assert nexo(*part, "web", "--kind", "visao", "--name", "Web", "--uses", "ui-kit", "--squad", "sq-a")[0] == 0
    folder = tmp_path / "repos"
    for name in ("web", "ui-kit", "extrato"):
        (folder / name / "src").mkdir(parents=True)
        (folder / name / "src" / "app.py").write_text(f"NOME = '{name}'\n", encoding="utf-8")
    return folder


def _portal(nexo: Runner, repos: Path, out: Path, *extra: str) -> tuple[int, dict, str]:  # type: ignore[type-arg]
    args = ("--repos", str(repos), "--product", "custodia", "--out", str(out), *extra)
    code, stdout, err = nexo("--json", "docs", "portal", *args)
    return code, json.loads(stdout) if stdout.strip() else {}, err


def test_portal_reads_every_repository_and_shows_the_sigla(repos: Path, nexo: Runner, tmp_path: Path) -> None:
    out = tmp_path / "site"
    code, data, err = _portal(nexo, repos, out, "--check")
    assert code == 0, err
    assert data["repos_read"] == data["repos_total"] == 3
    assert data["attention"] == []
    assert "CDT · Custódia" in (out / "docs" / "index.md").read_text(encoding="utf-8")


def test_portal_is_the_same_when_nothing_changed(repos: Path, nexo: Runner, tmp_path: Path) -> None:
    out = tmp_path / "site"
    assert _portal(nexo, repos, out)[0] == 0
    first = {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}
    assert _portal(nexo, repos, out)[0] == 0
    assert {p: p.read_bytes() for p in out.rglob("*") if p.is_file()} == first


def test_check_fails_when_the_code_uses_what_the_project_does_not_declare(
    repos: Path, nexo: Runner, tmp_path: Path
) -> None:
    (repos / "web" / "src" / "app.py").write_text("from extrato import ler\n", encoding="utf-8")
    code, data, _ = _portal(nexo, repos, tmp_path / "site", "--check")
    assert code != 0
    assert [a["kind"] for a in data["attention"]] == ["desvio"]
    assert "Web usa Extrato" in data["attention"][0]["title"]


def test_missing_repository_is_reported_and_not_hidden(repos: Path, nexo: Runner, tmp_path: Path) -> None:
    (repos / "extrato" / "src" / "app.py").unlink()
    (repos / "extrato" / "src").rmdir()
    (repos / "extrato").rmdir()
    code, data, _ = _portal(nexo, repos, tmp_path / "site", "--check")
    assert code == 0 and data["repos_read"] == 2  # fica como aviso: quem gera o portal pode não ter esse acesso
    assert any(a["kind"] == "leitura" and "Extrato" in a["title"] for a in data["attention"])


def test_unknown_project_is_refused(repos: Path, nexo: Runner, tmp_path: Path) -> None:
    code, _, err = nexo(
        "docs", "portal", "--repos", str(repos), "--product", "nao-existe", "--out", str(tmp_path / "s")
    )
    assert code != 0 and "nao-existe" in err
