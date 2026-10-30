"""agente writer: documentação viva no PR da história, só em docs/, e conhecimento no cérebro."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from nexo.kernel.runtime import http
from nexo.kernel.runtime.resources import load_yaml
from nexo.kernel.security.policy import Action, Policy, evaluate
from nexo.kernel.workspace import git
from tests.conftest import Runner, edit
from tests.fakes.http_fake import FakeCloud
from tests.integration.test_story import OK_REVIEWS, QA, impl, run_json

DONE = {"progress": {"percent": 100}}
SERVICOS = "# Serviços\n\n| Serviço | Responsabilidade |\n|---|---|\n| svc | Expõe version() (src/svc/version.py) |\n"


def writer(*events: dict) -> dict:  # type: ignore[type-arg]
    return {"tool": "claude", "role": "writer", "events": [*events, DONE]}


@pytest.fixture
def cloud() -> Iterator[FakeCloud]:
    fake = FakeCloud()
    http.set_transport(fake)
    yield fake
    http.set_transport(None)


def test_doc_writer_policy_only_writes_docs() -> None:
    policy = Policy.from_mapping(load_yaml("policy.yaml"))
    ok = evaluate(Action("write", "Write", path="/w/docs/servicos.md"), "/w", policy, "doc-writer")
    readme = evaluate(Action("write", "Write", path="/w/README.md"), "/w", policy, "doc-writer")
    code = evaluate(Action("write", "Write", path="/w/src/svc/app.py"), "/w", policy, "doc-writer")
    assert ok.allow and readme.allow and not code.allow and "só escreve documentação" in code.reason


def test_story_docs_go_in_the_same_branch(planned, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    root = planned(
        scenario,
        [QA, impl("T-1-1"), impl("T-1-2", path="README.md"), writer(edit("docs/servicos.md", SERVICOS)), *OK_REVIEWS],
    )
    nexo("config", "set", "docs.writer", "auto", "--project")
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert code == 0 and rep["state"] == "Concluido", rep
    assert "src/svc/version.py" in git.git(["show", "nexo/story/US-1-1:docs/servicos.md"], root)
    task = (root / ".nexo/tasks/US-1-1-DOC.yaml").read_text(encoding="utf-8")
    assert "src/svc/version.py" in task and "Arquivos alterados" in task  # contexto montado sem IA
    logs = json.loads(nexo("--json", "logs", "-n", "500")[1])
    assert any(e["type"] == "story.docs" for e in logs)


def test_writer_touching_code_is_blocked_and_story_goes_on(  # type: ignore[no-untyped-def]
    planned, nexo: Runner, scenario
) -> None:
    root = planned(
        scenario,
        [
            QA,
            impl("T-1-1"),
            impl("T-1-2", path="README.md"),
            writer(edit("src/svc/version.py", "def version() -> str:\n    return 'hack'\n")),
            *OK_REVIEWS,
        ],
    )
    nexo("config", "set", "docs.writer", "auto", "--project")
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert code == 0 and rep["state"] == "Concluido"
    assert "hack" not in git.git(["show", "nexo/story/US-1-1:src/svc/version.py"], root)
    logs = json.loads(nexo("--json", "logs", "-n", "500")[1])
    skipped = [e for e in logs if e["type"] == "story.docs_skipped"]
    assert skipped and any(e["type"] == "policy.violation" or "não permitido" in json.dumps(e) for e in logs)


def test_manual_update_and_knowledge_to_brain(  # type: ignore[no-untyped-def]
    project: Path, nexo: Runner, scenario, cloud: FakeCloud, memory_keyring
) -> None:
    scenario([writer(edit("docs/servicos.md", SERVICOS))])
    code, out, err = nexo("--json", "docs", "update")
    data = json.loads(out)
    assert code == 0 and data["state"] == "Concluido", err
    assert "Expõe version()" in git.git(["show", f"{data['branch']}:docs/servicos.md"], project)
    (project / "docs").mkdir(exist_ok=True)
    (project / "docs/servicos.md").write_text(SERVICOS, encoding="utf-8")
    (project / "docs/relacionamentos.md").write_text(
        "# Relacionamentos\n\n```mermaid\nflowchart LR\n  app --> svc\n```\n", encoding="utf-8"
    )
    code, out, _ = nexo("--json", "docs", "knowledge", "--dry-run")
    local = json.loads(out)
    text = Path(local["local"]).read_text(encoding="utf-8")
    assert (
        code == 0 and local["path"].startswith("knowledge/") and "## Serviços" in text and "## Relacionamentos" in text
    )
    memory_keyring.store[("nexo-cli", "github")] = cloud.github_token
    nexo("config", "set", "brain.github_repo", "empresa/nexo-brain")
    code, out, _ = nexo("--json", "docs", "knowledge")
    sent = json.loads(out)
    assert code == 0 and "pull/" in str(sent["pr"])
    (branch, path), content = next(iter(cloud.files.items()))
    assert path == local["path"] and branch.startswith("nexo/conhecimento/") and "Expõe version()" in content
    assert cloud.prs[-1]["head"] == branch
    (project / "docs/servicos.md").write_text(
        SERVICOS + "\nToken de acesso: ghp_abcdefghijklmnopqrstuvwxyz0123456789ABCD\n", encoding="utf-8"
    )
    code, _, err = nexo("docs", "knowledge", "--dry-run")
    assert code != 0 and "dado sensível" in err


def test_graph_links_docs_to_code(project: Path, nexo: Runner) -> None:
    (project / "docs").mkdir(exist_ok=True)
    (project / "docs/servicos.md").write_text(
        "# Serviços\n\nPacote principal em src/svc/__init__.py.\n", encoding="utf-8"
    )
    rel = json.loads(nexo("--json", "graph", "related", "src/svc/__init__.py")[1])
    assert rel["docs"] == ["docs/servicos.md"]
    out = json.loads(nexo("--json", "graph", "impact", "src/svc/__init__.py")[1])
    assert "docs/servicos.md" in out["decisions"]
