"""coletores, curadoria de lições, mudança de padrão, tarefas de referência, avaliação e roteador que aprende."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from nexo.kernel.runtime import http
from tests.conftest import Runner, dump_yaml, git_cmd
from tests.fakes.http_fake import FakeCloud
from tests.integration.test_story import OK_REVIEWS, QA, SPEC, backlog, impl


@pytest.fixture
def cloud(memory_keyring) -> Iterator[FakeCloud]:  # type: ignore[no-untyped-def]
    fake = FakeCloud()

    def extra(method: str, url: str, headers: dict[str, str], data: bytes | None) -> http.Response:
        if "/pulls/comments" in url:
            return http.Response(
                200,
                [
                    {
                        "id": 11,
                        "body": "Evite float para dinheiro; use Decimal.",
                        "path": "src/svc/refund.py",
                        "html_url": "https://github.com/x/pull/1#c11",
                        "created_at": "2026-09-20T10:00:00Z",
                    },
                    {"id": 12, "body": "  ", "created_at": "2026-09-20T10:01:00Z"},
                ],
            )
        if "service-now.com" in url:
            return http.Response(
                200,
                {
                    "result": [
                        {
                            "number": "INC0012",
                            "short_description": "Timeout no PIX",
                            "cause": "pool de conexões pequeno",
                            "sys_updated_on": "2026-09-01",
                        }
                    ]
                },
            )
        return fake(method, url, headers, data)

    http.set_transport(extra)
    memory_keyring.store[("nexo-cli", "github")] = fake.github_token
    memory_keyring.store[("nexo-cli", "servicenow")] = "snow-token"
    yield fake
    http.set_transport(None)


def run_json(nexo: Runner, *args: str) -> tuple[int, dict]:  # type: ignore[type-arg]
    code, out, err = nexo("--json", *args)
    return code, (json.loads(out) if out.strip() else {"err": err})


def test_collectors(project: Path, nexo: Runner, cloud: FakeCloud) -> None:
    for k, v in (
        ("scm.repo", "empresa/app"),
        ("learning.sources.servicenow.instance", "empresa.service-now.com"),
    ):
        nexo("config", "set", k, v, "--project")
    (project / "docs/adr").mkdir(parents=True)
    (project / "docs/adr/0001-decimal.md").write_text("# ADR 1\nDinheiro em Decimal.\n", encoding="utf-8")
    (project / ".nexo/bugs").mkdir(parents=True)
    dump_yaml(
        project / ".nexo/bugs/B-1.yaml",
        {
            "id": "B-1",
            "story": "US-1",
            "failures": ["teste falhou"],
            "evidence": {"failed_tests": ["test_x: esperado 1"]},
            "state": "Resolvido",
        },
    )
    # correção humana em arquivo entregue pelo Nexo
    (project / "src/svc/refund.py").write_text("LIMIT = 10.0\n", encoding="utf-8")
    git_cmd(project, "add", "-A")
    git_cmd(project, "commit", "-qm", "feat(T-1): estorno\n\nNexo-Run: run-1")
    (project / "src/svc/refund.py").write_text("from decimal import Decimal\nLIMIT = Decimal('10')\n", encoding="utf-8")
    git_cmd(project, "commit", "-qam", "fix: dinheiro em Decimal")
    code, data = run_json(nexo, "learn", "collect")
    assert code == 0
    assert data["pr_comments"] == {"found": 1, "new": 1}
    assert data["bugs"]["new"] == 1 and data["adrs"]["new"] == 1 and data["human_fixes"]["new"] == 1
    assert data["incidents"]["new"] == 1
    again = run_json(nexo, "learn", "collect")[1]
    assert again["pr_comments"]["new"] == 0 and again["human_fixes"]["new"] == 0  # incremental
    status = run_json(nexo, "learn", "status")[1]
    assert status["sources"]["incidents"]["pending"] == 1


def test_incident_source_requires_credential(project: Path, nexo: Runner, memory_keyring) -> None:  # type: ignore[no-untyped-def]
    nexo("config", "set", "learning.sources.servicenow.instance", "empresa.service-now.com", "--project")
    data = run_json(nexo, "learn", "collect", "--source", "incidents")[1]
    assert "servicenow" in data["incidents"]["error"]


def test_review_proposes_lessons_with_dedup(project: Path, nexo: Runner, cloud: FakeCloud, scenario) -> None:  # type: ignore[no-untyped-def]
    nexo("config", "set", "scm.repo", "empresa/app", "--project")
    nexo("config", "set", "brain.github_repo", "empresa/nexo-brain")
    nexo("learn", "collect", "--source", "pr_comments")
    lessons = {
        "done": {
            "structured": {
                "lessons": [
                    {
                        "type": "standard",
                        "title": "Dinheiro em Decimal",
                        "body": "Nunca usar float para valores monetários.",
                        "evidence_refs": ["prc:11"],
                        "proposed_target": "org/standards/python.md",
                    },
                    {"type": "pitfall", "title": "Sem evidência", "body": "Inventada.", "evidence_refs": ["nada:1"]},
                    {
                        "type": "standard",
                        "title": "Dinheiro em Decimal",
                        "body": "Nunca usar float para valores monetários.",
                        "evidence_refs": ["prc:11"],
                    },
                ]
            }
        }
    }
    scenario([{"tool": "claude", "role": "curator", "events": [lessons]}])
    code, rep = run_json(nexo, "learn", "review")
    assert code == 0 and rep["events"] == 1 and len(rep["proposed"]) == 1 and rep["duplicates"] == 1
    issue = cloud.issues[1]
    assert issue["title"] == "Lição: Dinheiro em Decimal" and "prc:11" in issue["body"]
    assert {"licao", "standard"} <= {x["name"] for x in issue["labels"]}
    assert run_json(nexo, "learn", "review")[1]["events"] == 0  # já processado
    logs = json.loads(nexo("--json", "logs", "-n", "100")[1])
    route = next(e for e in logs if e["type"] == "route.decision" and e["payload"].get("role") == "curator")
    assert route["payload"]["route"] == "claude/haiku"  # modelo barato (economia de tokens)


def test_pattern_change_detection(project: Path, nexo: Runner, cloud: FakeCloud) -> None:
    nexo("config", "set", "brain.github_repo", "empresa/nexo-brain")
    pyproject = (project / "pyproject.toml").read_text(encoding="utf-8")
    for i, dep in enumerate(("httpx>=0.27", "structlog>=24")):
        text = pyproject.replace("dependencies = []", f'dependencies = [\n    "{dep}",\n]')
        (project / "pyproject.toml").write_text(text, encoding="utf-8")
        git_cmd(project, "commit", "-qam", f"deps {i}")
        (project / "pyproject.toml").write_text(pyproject, encoding="utf-8")
        git_cmd(project, "commit", "-qam", f"revert {i}")
    (project / "pyproject.toml").write_text(
        pyproject.replace("dependencies = []", 'dependencies = [\n    "httpx>=0.27",\n]'), encoding="utf-8"
    )
    git_cmd(project, "commit", "-qam", "deps de novo")
    code, found = run_json(nexo, "learn", "patterns")
    assert code == 0 and [f["package"] for f in found] == ["httpx"] and found[0]["issue"]
    assert "Mudança de padrão: adoção de httpx" in cloud.issues[1]["title"]
    assert run_json(nexo, "learn", "patterns")[1] == []  # não repete


@pytest.fixture
def delivered(project: Path, nexo: Runner, scenario, fast_recipe: Path) -> Path:  # type: ignore[no-untyped-def]
    nexo("config", "set", "gates.thresholds.code", "0.9", "--project")
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
    nexo("plan", "versão")
    nexo("plan", "approve", "F-1")
    assert run_json(nexo, "run", "story", "US-1-1")[1]["state"] == "Concluido"
    return project


def test_golden_tasks_from_story_and_commit(delivered: Path, nexo: Runner, tmp_path: Path) -> None:
    code, items = run_json(nexo, "eval", "tasks")
    g = next(i for i in items if i["id"] == "G-US-1-1")
    assert g["tests"] == ["tests/test_version.py"] and "version" in g["description"]
    model = tmp_path / "modelo"
    model.mkdir()
    git_cmd(model, "init", "-q")
    (model / "a.py").write_text("x = 1\n", encoding="utf-8")
    git_cmd(model, "add", "-A")
    git_cmd(model, "commit", "-qm", "base")
    (model / "tests").mkdir()
    (model / "tests/test_a.py").write_text("def test_a():\n    assert True\n", encoding="utf-8")
    git_cmd(model, "add", "-A")
    git_cmd(model, "commit", "-qm", "feat: a com teste")
    sha = git_cmd(model, "rev-parse", "HEAD")
    code, items = run_json(nexo, "eval", "tasks", "--from-repo", str(model), "--commit", sha)
    assert any(i["id"].startswith("G-modelo-") for i in items)
    git_cmd(model, "commit", "--allow-empty", "-qm", "sem teste")
    assert nexo("eval", "tasks", "--from-repo", str(model), "--commit", git_cmd(model, "rev-parse", "HEAD"))[0] == 1
    assert nexo("eval", "tasks", "--from-repo", str(model))[0] == 1


def test_eval_model_compares_routes_and_proposes(delivered: Path, nexo: Runner, scenario, cloud: FakeCloud) -> None:  # type: ignore[no-untyped-def]
    nexo("config", "set", "brain.github_repo", "empresa/nexo-brain")
    nexo("eval", "tasks")
    good = {
        "events": [
            {"edit": {"path": "src/svc/version.py", "content": 'def version() -> str:\n    return "0.1.0"\n'}},
            {"progress": {"percent": 100}},
            {"usage": {"api_equiv_usd": 0.1}},
        ]
    }
    bad = {
        "events": [
            {"edit": {"path": "src/svc/version.py", "content": 'def version() -> str:\n    return "9"\n'}},
            {"progress": {"percent": 100}},
            {"usage": {"premium_request": 1}},
        ]
    }
    scenario([*[{"tool": "claude", **good} for _ in range(5)], *[{"tool": "copilot", **bad} for _ in range(5)]])
    code, rep = run_json(
        nexo, "eval", "model", "--task", "G-US-1-1", "--routes", "claude/sonnet,copilot/gpt-5", "--runs", "5"
    )
    assert code == 0
    by = {v["variant"]: v for v in rep["variants"]}
    assert by["claude/sonnet"]["success_rate"] == 1.0 and by["copilot/gpt-5"]["success_rate"] == 0.0
    assert rep["comparison"]["winner"] == "claude/sonnet"
    assert not list((delivered / ".nexo/tasks").glob("EVAL-*"))  # limpeza
    code, rep2 = run_json(nexo, "eval", "report", "--propose")
    assert rep2["issue"] and "claude/sonnet vence" in cloud.issues[1]["title"]
    assert any(c["route"] == "copilot/gpt-5" for c in rep2["routing"]["changes"])
    code, out, _ = nexo("eval", "report")
    assert "IC 90%" in out


def test_eval_skill_injects_variant(delivered: Path, nexo: Runner, scenario, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    for name in ("estilo-a", "estilo-b"):
        src = tmp_path / name / "SKILL.md"
        src.parent.mkdir()
        src.write_text(
            f"---\nname: {name}\ndescription: estilo {name}\n---\n# {name}\nRegra {name}.\n", encoding="utf-8"
        )
        nexo("config", "set", "agents.claude.home", str(tmp_path / "uh"))
        nexo("skill", "add", str(src))
    nexo("eval", "tasks")
    step = {
        "tool": "claude",
        "events": [
            {"edit": {"path": "src/svc/version.py", "content": 'def version() -> str:\n    return "0.1.0"\n'}},
            {"progress": {"percent": 100}},
        ],
    }
    scenario([step, step])
    code, rep = run_json(nexo, "eval", "skill", "estilo-a", "estilo-b", "--task", "G-US-1-1", "--runs", "1")
    assert code == 0, rep
    assert {v["variant"] for v in rep["variants"]} == {"skill:estilo-a", "skill:estilo-b"}
    assert "insuficiente" in rep["comparison"]["verdict"]
    logs = json.loads(nexo("--json", "logs", "-n", "400")[1])
    sends = [e for e in logs if e["type"] == "hook.pre_send"]
    assert sends  # prompt com a variante foi enviado pela rota
    assert nexo("eval", "report", "--batch", "nao-existe")[0] in (0, 1)


def test_router_learns_in_auto_mode(project: Path, nexo: Runner, scenario, nexo_home: Path) -> None:  # type: ignore[no-untyped-def]
    import sqlite3

    from nexo.store.runs import project_key

    db = sqlite3.connect(nexo_home / "nexo.db")
    scope = project_key(project)
    for _ in range(10):
        db.execute(
            "INSERT INTO eval_results(project, batch, task, kind, variant, route, success, cost_brl, seconds,"
            " detail, at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (scope, "b", "G", "backend", "x", "claude/sonnet", 0, 0.1, 1, "{}", "t"),
        )
        db.execute(
            "INSERT INTO eval_results(project, batch, task, kind, variant, route, success, cost_brl, seconds,"
            " detail, at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (scope, "b", "G", "backend", "y", "copilot/claude-sonnet-4.5", 1, 0.1, 1, "{}", "t"),
        )
    db.commit()
    db.close()
    scenario([])
    dry = run_json(nexo, "--dry-run", "run", "task", "T-1")[1]
    assert dry["decision"]["route"] == "claude/sonnet"  # modo padrão: só propõe
    nexo("config", "set", "routing.learning", "auto", "--project")
    dry = run_json(nexo, "--dry-run", "run", "task", "T-1")[1]
    assert dry["decision"]["route"] == "copilot/claude-sonnet-4.5"
    assert re.search(r"claude/sonnet", json.dumps(dry["decision"]["candidates"]))
