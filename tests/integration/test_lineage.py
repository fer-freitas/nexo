"""Linhagem de ponta a ponta com Git real: dois times, repositórios separados, sem CI e sem servidor."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import Runner, dump_yaml
from tests.unit.test_lineage_model import GESTAO, TRADUTOR


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, encoding="utf-8").stdout


def identity(root: Path) -> None:
    git(root, "config", "user.email", "t@t")
    git(root, "config", "user.name", "t")


def repo(tmp: Path, name: str, tree: dict[str, str]) -> tuple[Path, Path]:
    bare = tmp / "remote" / f"{name}.git"
    bare.parent.mkdir(parents=True, exist_ok=True)
    git(tmp, "init", "-q", "--bare", "-b", "main", str(bare))
    work = tmp / "work" / name
    work.parent.mkdir(parents=True, exist_ok=True)
    git(tmp, "clone", "-q", str(bare), str(work))
    identity(work)
    git(work, "checkout", "-q", "-b", "main")
    for rel, text in tree.items():
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_text(text, encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "i")
    git(work, "push", "-q", "-u", "origin", "main")
    return bare, work


def team_repo(tmp: Path, name: str) -> Path:
    bare = tmp / "remote" / f"nexo-{name}.git"
    bare.parent.mkdir(parents=True, exist_ok=True)
    git(tmp, "init", "-q", "--bare", "-b", "main", str(bare))
    seed = tmp / "seed" / name
    seed.parent.mkdir(parents=True, exist_ok=True)
    git(tmp, "clone", "-q", str(bare), str(seed))
    identity(seed)
    git(seed, "checkout", "-q", "-b", "main")
    dump_yaml(seed / "team.yaml", {"name": name, "mode": "team", "members": []})
    git(seed, "add", "-A")
    git(seed, "commit", "-qm", "i")
    git(seed, "push", "-q", "-u", "origin", "main")
    return bare


@pytest.fixture
def world(tmp_path: Path, nexo: Runner, nexo_home: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    for k, v in {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }.items():
        monkeypatch.setenv(k, v)
    out: dict[str, Any] = {}
    for team, name, tree in (("time-traducao", "tradutor", TRADUTOR), ("time-posicao", "gestao", GESTAO)):
        team_bare = team_repo(tmp_path, team)
        _bare, work = repo(tmp_path, name, tree)
        monkeypatch.chdir(work)
        assert nexo("init")[0] == 0
        code, o, err = nexo("--json", "team", "link", team, "--repo", str(team_bare))
        assert code == 0, err + o
        out[name] = work
        out[f"{name}.team"] = team_bare
    return out


def j(nexo: Runner, *args: str) -> Any:
    code, out, err = nexo("--json", *args)
    assert code == 0, err + out
    return json.loads(out)


def test_two_teams_linked_by_topic_with_no_ci(
    world: dict[str, Any], nexo: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("tradutor", "gestao"):
        monkeypatch.chdir(world[name])
        out = j(nexo, "lineage", "publish")
        assert out["published"] and out["sent"], out
    monkeypatch.chdir(world["gestao"])
    show = j(nexo, "lineage", "show", "gestao-api")
    entry = next(r for r in show["entries"] if r["channel"] == "kafka:posicao.traduzida")
    assert entry["component"] == "time-traducao/tradutor-worker" and entry["state"] == "confirmado"
    assert j(nexo, "lineage", "upstream", "gestao-api")["upstream"] == ["time-traducao/tradutor-worker"]
    impact = j(nexo, "lineage", "impact", "kafka:posicao.traduzida")
    assert impact["known_consumers"][0]["component"] == "time-posicao/gestao-api" and impact["complete"]
    # nenhum workflow foi criado em lugar nenhum
    assert not (world["gestao"] / ".github").exists()
    log = git(Path(world["gestao.team"]), "log", "--oneline", "main")
    assert "linhagem de gestao" in log


def test_scan_reads_origin_main_never_the_working_tree(
    world: dict[str, Any], nexo: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = world["tradutor"]
    monkeypatch.chdir(work)
    git(work, "checkout", "-q", "-b", "feature/x")
    (work / "Tradutor.Worker" / "Novo.cs").write_text('p.ProduceAsync("nao-publicado", m);', encoding="utf-8")
    (work / "Tradutor.Worker" / "Publisher.cs").write_text("// editado sem commit", encoding="utf-8")
    data = j(nexo, "lineage", "scan")
    channels = {x["channel"] for c in data["components"] for x in c.get("publishes", [])}
    assert channels == {"kafka:posicao.traduzida"}
    assert (work / "Tradutor.Worker" / "Publisher.cs").read_text(encoding="utf-8") == "// editado sem commit"
    assert git(work, "rev-parse", "--abbrev-ref", "HEAD").strip() == "feature/x"


def test_second_machine_same_commit_does_not_duplicate_and_old_never_overwrites(
    world: dict[str, Any], nexo: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = world["tradutor"]
    monkeypatch.chdir(work)
    assert j(nexo, "lineage", "publish")["published"]
    assert j(nexo, "lineage", "publish")["reason"] == "já publicado"
    old = git(work, "rev-parse", "origin/main").strip()
    (work / "Tradutor.Worker" / "Outro.cs").write_text('p.ProduceAsync("outro.topico", m);', encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "novo")
    git(work, "push", "-q", "origin", "main")
    git(work, "fetch", "-q")
    assert j(nexo, "lineage", "publish")["published"]
    from nexo.kernel.context import AppContext, Options
    from nexo.modules.lineage.app import lineage

    ctx = AppContext(options=Options())
    stale = lineage.scan(ctx, work, old)
    result = lineage.publish(ctx, work, stale)
    assert not result["published"] and "mais antigo" in result["reason"]


def test_agent_finishes_without_cost_when_nothing_changed_and_reports_errors(
    world: dict[str, Any], nexo: Runner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from nexo.modules.lineage.app import lineage

    work = world["gestao"]
    monkeypatch.chdir(work)
    lineage.register_project(work)
    first = j(nexo, "lineage", "agent", "run", "--repo", ".")
    assert first["results"][0]["changed"]
    second = j(nexo, "lineage", "agent", "run", "--repo", ".")
    assert second["results"][0] == {"repo": work.name, "changed": False}
    git(work, "remote", "set-url", "origin", str(tmp_path / "nao-existe.git"))
    third = j(nexo, "lineage", "agent", "run", "--repo", ".")
    assert third["errors"] and j(nexo, "lineage", "agent", "status")["last_error"]


def test_invalid_manifest_in_another_team_is_ignored_and_reported(
    world: dict[str, Any], nexo: Runner, nexo_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(world["gestao"])
    j(nexo, "lineage", "publish")
    target = nexo_home / "teams" / "time-traducao" / "lineage" / "invasor.yaml"
    dump_yaml(
        target, {"schema": "nexo.lineage/v1", "team": "time-posicao", "repo": "x", "commit": "y", "components": []}
    )
    data = j(nexo, "lineage", "coverage")
    assert data["invalid"] and "difere" in data["invalid"][0]["problem"]


def test_export_to_obsidian_has_links(
    world: dict[str, Any], nexo: Runner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(world["gestao"])
    j(nexo, "lineage", "publish")
    out = tmp_path / "vault" / "_linhagem"
    assert j(nexo, "lineage", "export", "--obsidian", str(out))["notes"] >= 3
    note = (out / "time-posicao-gestao-api.md").read_text(encoding="utf-8")
    assert "[[kafka-posicao-traduzida]]" in note and "type: component" in note


def test_declared_component_for_what_scanners_cannot_reach(
    world: dict[str, Any], nexo: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = world["tradutor"]
    monkeypatch.chdir(work)
    dump_yaml(
        work / ".nexo" / "component.yaml",
        {"components": [{"name": "tradutor-worker", "publishes": [{"kind": "kafka", "name": "topico.declarado"}]}]},
    )
    git(work, "add", "-A")
    git(work, "commit", "-qm", "declara")
    git(work, "push", "-q", "origin", "main")
    git(work, "fetch", "-q")
    data = j(nexo, "lineage", "scan")
    links = {x["channel"]: x for c in data["components"] for x in c.get("publishes", [])}
    assert links["kafka:topico.declarado"]["evidence"]["source"] == "declared"
