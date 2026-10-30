"""F28 — feature em vários repositórios: planejamento único, distribuição, execução em ordem e PRs ligados."""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from nexo.kernel.runtime import http
from nexo.kernel.secrets import credentials
from tests.conftest import EXAMPLES, Runner, git_cmd
from tests.fakes.http_fake import FakeCloud
from tests.integration.test_plan import ROUND1, ROUND2
from tests.integration.test_story import OK_REVIEWS, QA, impl

BACKLOG = {
    "done": {
        "structured": {
            "stories": [
                {"id": "US-1-1", "title": "Expor versão na API", "ac_refs": ["AC-1"], "points": 2, "repo": "api"},
                {"id": "US-1-2", "title": "Mostrar versão no app", "ac_refs": ["AC-1"], "points": 1, "repo": "app"},
            ],
            "tasks": [
                {
                    "id": "T-1-1",
                    "story": "US-1-1",
                    "kind": "backend",
                    "title": "Versão",
                    "files_hint": ["src/svc/version.py"],
                },
                {
                    "id": "T-1-2",
                    "story": "US-1-2",
                    "kind": "backend",
                    "title": "Consumir versão",
                    "depends_on": ["T-1-1"],
                    "files_hint": ["README.md"],
                },
            ],
        }
    }
}


@pytest.fixture
def cloud() -> Iterator[FakeCloud]:
    fake = FakeCloud()
    http.set_transport(fake)
    yield fake
    http.set_transport(None)


def make_repo(workdir: Path, name: str, nexo: Runner, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = workdir / name
    shutil.copytree(EXAMPLES / "python-svc", root)
    git_cmd(root, "init", "-q")
    monkeypatch.chdir(root)
    assert nexo("init")[0] == 0
    nexo("config", "set", "gates.thresholds.code", "0.9", "--project")
    nexo("config", "set", "scm.provider", "github", "--project")
    nexo("config", "set", "scm.repo", f"acme/{name}", "--project")
    git_cmd(root, "add", "-A")
    git_cmd(root, "commit", "-qm", "init")
    return root


def test_program_across_two_repos(  # type: ignore[no-untyped-def]
    workdir: Path,
    nexo: Runner,
    scenario,
    fast_recipe: Path,
    cloud: FakeCloud,
    monkeypatch: pytest.MonkeyPatch,
    nexo_home: Path,
) -> None:
    credentials.set_secret("github", "tok")
    api = make_repo(workdir, "api", nexo, monkeypatch)
    app = make_repo(workdir, "app", nexo, monkeypatch)
    for r in (api, app):  # o push do PR vai para um remoto local
        bare = workdir / f"{r.name}.git"
        git_cmd(workdir, "init", "-q", "--bare", str(bare))
        git_cmd(r, "remote", "add", "origin", str(bare))
    monkeypatch.chdir(workdir)
    code, _, err = nexo("program", "repo", "add", "api", str(api), "--workspace", "Versao")
    assert code == 0, err
    code, _, err = nexo("program", "repo", "add", "app", str(app), "--after", "api", "--workspace", "Versao")
    assert code == 0, err
    scenario([{"tool": "claude", "events": [e]} for e in (ROUND1, ROUND2, BACKLOG)])
    assert nexo("plan", "versão em api e app", "--workspace", "Versao")[0] == 0
    assert nexo("plan", "answer", "F-1", "Q-1=interna", "--workspace", "Versao")[0] == 0
    code, _, err = nexo("plan", "approve", "F-1", "--workspace", "Versao")
    assert code == 0, err
    code, out, err = nexo("--json", "program", "split", "F-1", "--workspace", "Versao")
    assert code == 0, err
    state = json.loads(out)
    assert state["order"] == ["api", "app"]
    assert state["repos"]["api"]["feature"] == "F-1" and state["repos"]["app"]["stories"] == {"US-1-2": "US-1-2"}
    assert (api / ".nexo" / "tasks" / "T-1-1.yaml").is_file() and not (api / ".nexo" / "tasks" / "T-1-2.yaml").is_file()
    app_task = (app / ".nexo" / "tasks" / "T-1-2.yaml").read_text(encoding="utf-8")
    assert "T-1-1" not in app_task  # dependência entre repositórios vira ordem do programa
    # execução: api primeiro, depois app; cada história abre PR e os PRs são ligados entre si
    scenario([QA, impl("T-1-1"), *OK_REVIEWS, QA, impl("T-1-2"), *OK_REVIEWS])
    code, out, err = nexo("--json", "program", "run", "F-1", "--workspace", "Versao")
    assert code == 0, err
    result = json.loads(out)
    assert not result["stopped"] and len(result["prs"]) == 2
    bodies = [p.get("body", "") for p in cloud.prs]
    assert all("Programa F-1 (Nexo)" in b for b in bodies) and "api → app" in bodies[0]
    st = json.loads(nexo("--json", "program", "status", "F-1", "--workspace", "Versao")[1])
    assert st["done"] and [r["repo"] for r in st["repos"]] == ["api", "app"]


def test_split_requires_declared_repos(workdir: Path, nexo: Runner, scenario, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(workdir)
    scenario([{"tool": "claude", "events": [e]} for e in (ROUND1, ROUND2, BACKLOG)])
    nexo("plan", "versão", "--workspace", "Solto")
    nexo("plan", "answer", "F-1", "Q-1=interna", "--workspace", "Solto")
    nexo("plan", "approve", "F-1", "--workspace", "Solto")
    code, _, err = nexo("program", "split", "F-1", "--workspace", "Solto")
    assert code != 0 and "sem repositório declarado" in err
    code, _, err = nexo("program", "repo", "add", "x", str(workdir), "--workspace", "Solto")
    assert code != 0 and "não é um projeto Nexo" in err


def test_repo_order_detects_cycles() -> None:
    from nexo.kernel.errors import NexoError
    from nexo.modules.program.app.program import repo_order

    declared: dict[str, dict[str, Any]] = {"a": {"after": []}, "b": {"after": ["a"]}, "c": {"after": []}}
    assert repo_order(declared, {"c": {"b"}}) == ["a", "b", "c"]
    with pytest.raises(NexoError):
        repo_order({"a": {"after": ["b"]}, "b": {"after": ["a"]}})
