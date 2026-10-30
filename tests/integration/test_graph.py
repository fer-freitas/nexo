"""grafo do código e do cérebro: impacto, cache pela versão e contexto da tarefa."""

from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import DEFAULT_TASK, Runner, dump_yaml, git_cmd


def _write(root: Path) -> None:
    (root / "src/svc/moeda.py").write_text(
        "from decimal import Decimal\n\n\ndef real(v): return Decimal(v)\n", encoding="utf-8"
    )
    (root / "src/svc/pagamento.py").write_text(
        "from svc.moeda import real\n\n\ndef estornar(v): return real(v)\n", encoding="utf-8"
    )
    (root / "tests/test_pagamento.py").write_text(
        "from svc.pagamento import estornar\n\n\ndef test_e(): ...\n", encoding="utf-8"
    )
    (root / "spec/adr").mkdir(parents=True)
    (root / "spec/adr/0001-moeda.md").write_text(
        "# Moeda\nValores sempre em Decimal (src/svc/moeda.py).\n", encoding="utf-8"
    )
    git_cmd(root, "add", "-A")
    git_cmd(root, "commit", "-qm", "pagamento")


def gjson(nexo: Runner, *args: str) -> dict:  # type: ignore[type-arg]
    code, out, err = nexo("--json", "graph", *args)
    assert code == 0, err
    return dict(json.loads(out))


def test_impact_and_cache(project: Path, nexo: Runner) -> None:
    _write(project)
    first = gjson(nexo, "impact", "src/svc/moeda.py")
    assert first["rebuilt"] and first["dependents"] == ["src/svc/pagamento.py"]
    assert first["tests"] == ["tests/test_pagamento.py"] and first["decisions"] == ["spec/adr/0001-moeda.md"]
    assert gjson(nexo, "impact", "src/svc/moeda.py")["rebuilt"] is False  # nada mudou: usa o cache
    (project / "src/svc/checkout.py").write_text("from svc.pagamento import estornar\n", encoding="utf-8")
    again = gjson(nexo, "impact", "src/svc/moeda.py", "nao/existe.py")
    assert again["rebuilt"] and "src/svc/checkout.py" in again["dependents"] and again["unknown"] == ["nao/existe.py"]
    assert gjson(nexo, "impact", "src/svc/moeda.py", "--depth", "1")["dependents"] == ["src/svc/pagamento.py"]
    built = gjson(nexo, "build")
    assert built["stats"]["test"] >= 2 and built["stats"]["adr"] == 1
    code, out, _ = nexo("graph", "related", "src/svc/pagamento.py")
    assert code == 0 and "tests/test_pagamento.py" in out and "src/svc/moeda.py" in out
    assert "map/" in (project / ".nexo/.gitignore").read_text(encoding="utf-8")  # índice derivado fica fora do git
    logs = json.loads(nexo("--json", "logs", "-n", "50")[1])
    assert any(e["type"] == "graph.built" for e in logs)


def test_task_prompt_gets_linked_paths_and_flag_off(project: Path, nexo: Runner, nexo_home: Path) -> None:
    _write(project)
    dump_yaml(project / ".nexo/tasks/T-2.yaml", {**DEFAULT_TASK, "files_hint": ["src/svc/pagamento.py"]})
    from nexo.kernel.context import AppContext, Options
    from nexo.kernel.models.work import Task
    from nexo.modules.execution.app.run import Orchestrator, RunOptions
    from nexo.modules.execution.app.run import context as run_context

    ctx = AppContext(cwd=project, home=nexo_home, options=Options())
    orch = Orchestrator(ctx, RunOptions())
    prompt = run_context.prompt(orch, Task(id="T-2", title="x", files_hint=["src/svc/pagamento.py"]), None)
    assert "Ligado aos arquivos da tarefa (grafo)" in prompt and "Testes que cobrem: tests/test_pagamento.py" in prompt
    ctx.close()
    dump_yaml(nexo_home / "brain" / "org" / "flags.yaml", {"flags": {"graph": False}})
    code, _, err = nexo("graph", "build")
    assert code != 0 and "feature flag" in err
    ctx = AppContext(cwd=project, home=nexo_home, options=Options())
    prompt = run_context.prompt(
        Orchestrator(ctx, RunOptions()), Task(id="T-2", title="x", files_hint=["src/svc/pagamento.py"]), None
    )
    assert "grafo" not in prompt  # flag desligada: prompt como antes
    ctx.close()
