"""achados do autoteste com o Devin: teste travado com valor errado vira decisão humana (sem gastar
tentativas) e lint que a ferramenta corrige sozinha não custa rodada de agente."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

from tests.conftest import FAST_PYTHON_RECIPE, Runner, dump_yaml
from tests.integration.test_story import OK_REVIEWS, QA, impl, run_json

# a correção mexe no código (comentário), mas continua devolvendo 0.0.9: o teste travado falha igual
FIX_SAME_RESULT = {
    "tool": "claude",
    "task": "US-1-1-FIX1",
    "events": [
        {
            "edit": {
                "path": "src/svc/version.py",
                "content": '# versão conforme a regra\ndef version() -> str:\n    return "0.0.9"\n',
            }
        },
        {"progress": {"percent": 100}},
    ],
}


def test_same_locked_failure_after_fix_goes_to_human_and_resume_relocks(planned, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    # o QA travou um teste que espera 0.1.0; o dev entrega 0.0.9 e a correção não muda nada
    root = planned(
        scenario,
        [QA, impl("T-1-1", "0.0.9"), impl("T-1-2", path="README.md"), FIX_SAME_RESULT],
    )
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert rep["state"] == "Escalado", rep
    assert "o teste pode estar errado" in rep["reason"] and "0.0.9" in rep["reason"]
    assert "US-1-1-FIX2" not in rep.get("tasks", {})  # não gastou a terceira tentativa
    # a pessoa confere: o valor certo é 0.0.9 — corrige o teste travado e retoma
    wt = Path(rep["worktree"])
    test = wt / "tests" / "test_version.py"
    test.write_text(test.read_text(encoding="utf-8").replace("0.1.0", "0.0.9"), encoding="utf-8")
    scenario([*OK_REVIEWS])
    code, rep = run_json(nexo, "resume", "US-1-1")
    assert rep["state"] == "Concluido", rep
    events = json.loads(nexo("--json", "logs", "-n", "500")[1])
    updated = [e for e in events if e["type"] == "story.locked_tests_updated"]
    assert updated and updated[0]["payload"]["files"] == ["tests/test_version.py"]
    assert root.is_dir()


def test_lint_autofix_on_dev_code_before_the_pipeline(planned, nexo: Runner, scenario, nexo_home: Path) -> None:  # type: ignore[no-untyped-def]
    recipe: dict[str, Any] = copy.deepcopy(FAST_PYTHON_RECIPE)
    lint = recipe["stages"][0]
    lint["changed_only"] = [".py"]
    lint["fix"] = [sys.executable, "-m", "ruff", "check", "--fix", "--exit-zero", "--quiet"]
    dump_yaml(nexo_home / "brain" / "recipes" / "python.yaml", recipe)
    messy = 'import os\n\n\ndef version() -> str:\n    return "0.1.0"\n'  # import sem uso: o ruff remove sozinho
    dev = {
        "tool": "claude",
        "task": "T-1-1",
        "events": [{"edit": {"path": "src/svc/version.py", "content": messy}}, {"progress": {"percent": 100}}],
    }
    planned(scenario, [QA, dev, impl("T-1-2", path="README.md"), *OK_REVIEWS])
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert rep["state"] == "Concluido", rep
    assert {q["id"]: q["status"] for q in rep["quality"]}["lint"] == "passed"
    assert "import os" not in (Path(rep["worktree"]) / "src/svc/version.py").read_text(encoding="utf-8")
    events = json.loads(nexo("--json", "logs", "-n", "500")[1])
    assert any(e["type"] == "story.autofix" for e in events)
    assert not any(e["type"] == "bug.created" for e in events)  # nenhuma rodada de agente gasta com lint
