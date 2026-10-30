"""código existente — mapa, baseline, portão no diff, caracterização, contexto e as-is."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import EXAMPLES, Runner, dump_yaml, git_cmd
from tests.integration.test_story import OK_REVIEWS, SPEC, run_json

LEGACY_RECIPE = {
    "stack": "python",
    "stages": [
        {
            "id": "lint",
            "kind": "lint",
            "tool": sys.executable,
            "run": [
                sys.executable,
                "-m",
                "ruff",
                "check",
                "--exit-zero",
                "--output-format",
                "sarif",
                "--output-file",
                "{out}/ruff.sarif",
                ".",
            ],
            "parser": "sarif",
            "report": "{out}/ruff.sarif",
            "default_severity": "medium",
            "fail_on": "any",
            "fix": [sys.executable, "-m", "ruff", "check", "--fix", "--exit-zero", "--quiet"],
            "changed_only": [".py"],
            "required": True,
        },
        {
            "id": "unit",
            "kind": "unit",
            "tool": sys.executable,
            "run": [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "--junitxml={out}/junit.xml",
                "--cov=src",
                "--cov-report=xml:{out}/coverage.xml",
            ],
            "parser": "junit",
            "report": "{out}/junit.xml",
            "coverage": "{out}/coverage.xml",
            "required": True,
        },
    ],
}
INVOICE = (EXAMPLES / "legacy-billing" / "src" / "billing" / "invoice.py").read_text(encoding="utf-8")
CUPOM = (
    INVOICE
    + "\n\ndef aplicarCupom(total, cupom):\n    if cupom == 'DEZ':\n        return round(total * 0.9, 2)\n    return total\n"
)
QA_TEST = "# @AC-1\nfrom billing.invoice import aplicarCupom\n\n\ndef test_ac_1_cupom_dez() -> None:\n    assert aplicarCupom(100, 'DEZ') == 90\n"
CHAR_TEST = (
    "from billing.invoice import calcTotal\n\n\ndef test_char_calc_total_atual() -> None:\n"
    "    assert calcTotal([{'preco': 10, 'qtd': 2}, {'preco': 5, 'qtd': 1, 'isento': True}]) == 22.0\n"
)


def backlog() -> dict[str, Any]:
    return {
        "done": {
            "structured": {
                "stories": [{"id": "US-1-1", "title": "Cupom", "ac_refs": ["AC-1"], "points": 2}],
                "tasks": [
                    {
                        "id": "T-1-1",
                        "story": "US-1-1",
                        "kind": "backend",
                        "title": "cupom",
                        "files_hint": ["src/billing/invoice.py"],
                    }
                ],
            }
        }
    }


def step(role: str, path: str, content: str, task: str | None = None, expect: str | None = None) -> dict[str, Any]:
    s: dict[str, Any] = {
        "tool": "claude",
        "role": role,
        "events": [{"edit": {"path": path, "content": content}}, {"progress": {"percent": 100}}],
    }
    if task:
        s["task"] = task
    if expect:
        s["expect_prompt"] = expect
    return s


@pytest.fixture
def legacy(workdir: Path, nexo: Runner, nexo_home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = workdir / "legacy-billing"
    shutil.copytree(EXAMPLES / "legacy-billing", root)
    git_cmd(root, "init", "-q")
    git_cmd(root, "add", "-A")
    git_cmd(root, "commit", "-qm", "2014")
    (root / "src/billing/invoice.py").write_text(INVOICE + "\n# ajuste de 2019\n", encoding="utf-8")
    git_cmd(root, "commit", "-qam", "2019")
    (root / "src/billing/invoice.py").write_text(INVOICE, encoding="utf-8")
    git_cmd(root, "commit", "-qam", "2021")
    monkeypatch.chdir(root)
    assert nexo("init")[0] == 0
    nexo("config", "set", "gates.thresholds.code", "0.5", "--project")
    dump_yaml(nexo_home / "brain" / "recipes" / "python.yaml", LEGACY_RECIPE)
    dump_yaml(nexo_home / "brain" / "recipes" / "common.yaml", {"stages": []})
    git_cmd(root, "add", "-A")
    git_cmd(root, "commit", "-qm", "nexo init")
    return root


def test_discover_maps_legacy_code(legacy: Path, nexo: Runner) -> None:
    code, data = run_json(nexo, "discover", "--as-is")
    assert code == 0 and data["languages"] == {"python": 3} and data["tests"] == 1
    assert data["conventions"]["naming"] == {"python": "camelCase"} and data["conventions"]["test_frameworks"] == [
        "pytest"
    ]
    assert data["baseline"]["findings"] >= 1 and data["baseline"]["failing_tests"] == 1
    assert data["hotspots"][0] == "src/billing/invoice.py"
    mapped = json.loads((legacy / ".nexo/map/map.json").read_text(encoding="utf-8"))
    invoice = next(f for f in mapped["files"] if f["path"] == "src/billing/invoice.py")
    assert "def calcTotal(itens, desconto)" in invoice["symbols"] and invoice["churn"] == 3
    assert invoice["coverage"] is not None and invoice["coverage"] < 60
    assert "camelCase" in (legacy / ".nexo/map/project-conventions/SKILL.md").read_text(encoding="utf-8")
    assert "(as-is)" in (legacy / ".nexo/map/design-tecnico-as-is.md").read_text(encoding="utf-8")
    assert "map/" in (legacy / ".nexo/.gitignore").read_text(encoding="utf-8")
    assert "map" not in git_cmd(legacy, "status", "--porcelain")


def plan_story(nexo: Runner, scenario, steps: list[dict[str, Any]]) -> None:  # type: ignore[no-untyped-def]
    scenario(
        [
            {"tool": "claude", "events": [SPEC]},
            {"tool": "claude", "expect_prompt": "def calcTotal(itens, desconto)", "events": [backlog()]},
            *steps,
        ]
    )
    assert nexo("plan", "cupom")[0] == 0
    assert nexo("plan", "approve", "F-1")[0] == 0


def test_story_on_legacy_code_with_characterization_and_diff_gate(legacy: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    nexo("discover")
    nexo("config", "set", "quality.min_diff_coverage", "70", "--project")
    plan_story(
        nexo,
        scenario,
        [
            step("qa-author", "tests/test_cupom.py", QA_TEST),
            step(
                "characterization",
                "tests/characterization/test_char_invoice.py",
                CHAR_TEST,
                expect="def calcTotal(itens, desconto)",
            ),
            step("developer", "src/billing/invoice.py", CUPOM, task="T-1-1", expect="Código existente relacionado"),
            *OK_REVIEWS,
        ],
    )
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert rep["state"] in ("Concluido", "AguardandoHumano"), rep
    stages = {q["id"]: q for q in rep["quality"]}
    assert stages["lint"]["status"] == "passed" and "preexistente" in stages["lint"]["summary"]
    assert stages["unit"]["status"] == "passed" and "1 preexistente" in stages["unit"]["summary"]
    assert "cobertura do diff 75% (3/4 linhas)" in stages["unit"]["summary"]  # `return total` sem teste
    logs = json.loads(nexo("--json", "logs", "-n", "400")[1])
    char = next(e["payload"] for e in logs if e["type"] == "story.characterized")
    assert char["tests"] == ["tests/characterization/test_char_invoice.py"]
    state = json.loads(next((legacy / ".nexo/runs").glob("*/story.json")).read_text(encoding="utf-8"))
    assert "tests/characterization/test_char_invoice.py" in state["locked"]


def test_new_lint_issue_in_touched_legacy_file_blocks(legacy: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    nexo("discover")
    nexo("config", "set", "brownfield.characterization", "false", "--project")
    nexo("config", "set", "bug_loop.max_attempts", "1", "--project")
    bad = CUPOM + "\n\ndef naoUsada():\n    x = 1\n"  # variável sem uso — novo
    plan_story(
        nexo,
        scenario,
        [
            step("qa-author", "tests/test_cupom.py", QA_TEST),
            step("developer", "src/billing/invoice.py", bad, task="T-1-1"),
            *OK_REVIEWS,
        ],
    )
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    lint = next(q for q in rep["quality"] if q["id"] == "lint")
    assert lint["status"] == "failed" and lint["summary"].startswith("1 achado(s) novo(s)") and rep["bugs"]


def test_failed_characterization_forces_human_gate(legacy: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    nexo("discover")
    wrong = CHAR_TEST.replace("== 22.0", "== 99.0")  # não descreve o comportamento atual
    plan_story(
        nexo,
        scenario,
        [
            step("qa-author", "tests/test_cupom.py", QA_TEST),
            step("characterization", "tests/characterization/test_char_invoice.py", wrong),
            step("characterization", "tests/characterization/test_char_invoice_2.py", wrong),
            step("developer", "src/billing/invoice.py", CUPOM, task="T-1-1"),
            *OK_REVIEWS,
        ],
    )
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert rep["state"] == "AguardandoHumano" and "código legado sem caracterização" in rep["reason"], rep
    branch_files = git_cmd(legacy, "ls-tree", "-r", "--name-only", "nexo/story/US-1-1")
    assert "test_char_invoice" not in branch_files  # testes inválidos removidos do branch


def test_agent_tests_are_linted_and_autofixed_before_locking(legacy: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    """Teste travado com problema de lint seria um impasse."""
    nexo("discover")
    nexo("config", "set", "brownfield.characterization", "false", "--project")
    fixable = "import os\n" + QA_TEST  # corrigido automaticamente antes de travar
    unfixable = QA_TEST.replace("    assert", "    sobra = 1\n    assert")  # volta para o agente
    plan_story(
        nexo,
        scenario,
        [
            step("qa-author", "tests/test_cupom.py", unfixable),
            step("qa-author", "tests/test_cupom.py", fixable),
            step("developer", "src/billing/invoice.py", CUPOM, task="T-1-1"),
            *OK_REVIEWS,
        ],
    )
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert rep["state"] in ("Concluido", "AguardandoHumano"), rep
    lint = next(q for q in rep["quality"] if q["id"] == "lint")
    assert lint["status"] == "passed"
    locked = (legacy / ".nexo/worktrees/US-1-1/tests/test_cupom.py").read_text(encoding="utf-8")
    assert "import os" not in locked and "sobra" not in locked
