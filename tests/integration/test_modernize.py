"""F27 — modernização de legado em ondas: roteiro, ordem pelos imports, histórias e progresso sem IA."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexo.modules.legacy.domain.modernize import Module, Playbook, chunks, modules, progress, waves
from tests.conftest import Runner, git_cmd


def test_waves_follow_imports_and_cycles_merge() -> None:
    affected = {"src/core/http.py": 2, "src/api/handler.py": 1, "src/jobs/sync.py": 1}
    imports = {"src/api/handler.py": ["core.http"], "src/jobs/sync.py": ["api.handler", "core"]}
    mods = modules(affected, imports)
    assert mods["src/api"].depends_on == {"src/core"} and mods["src/core"].count == 2
    assert waves(mods) == [["src/core"], ["src/api"], ["src/jobs"]]
    cyc = {"a": Module("a", depends_on={"b"}), "b": Module("b", depends_on={"a"}), "c": Module("c")}
    assert waves(cyc) == [["c"], ["a", "b"]]
    assert [len(c) for c in chunks([f"f{i}" for i in range(19)])] == [8, 8, 3]
    assert progress(10, 4) == 60.0 and progress(0, 0) == 100.0
    with pytest.raises(ValueError):
        Playbook.from_mapping("x", {"detect": ""})


def test_builtin_playbooks_listed(project: Path, nexo: Runner) -> None:
    code, out, _ = nexo("--json", "modernize", "playbooks")
    names = {p["name"] for p in json.loads(out)}
    assert {"dotnet-net8", "python-httpx", "js-date-fns"} <= names


def test_plan_and_progress(project: Path, nexo: Runner) -> None:
    src = project / "src" / "svc"
    (src / "client.py").write_text(
        "import requests\n\n\ndef get(url):\n    return requests.get(url)\n", encoding="utf-8"
    )
    (src / "api").mkdir()
    (src / "api" / "__init__.py").write_text("", encoding="utf-8")
    (src / "api" / "handler.py").write_text(
        "import requests\nfrom svc import client\n\n\ndef h():\n    return client.get('x')\n", encoding="utf-8"
    )
    git_cmd(project, "add", "-A")
    git_cmd(project, "commit", "-qm", "legado")
    code, out, err = nexo("--json", "modernize", "plan", "--playbook", "python-httpx")
    assert code == 0, err
    p = json.loads(out)
    assert p["feature"] == "F-1" and p["occurrences"] == 2
    assert p["waves"] == [["src/svc"], ["src/svc/api"]]  # o módulo usado migra antes de quem o usa
    task2 = (project / ".nexo" / "tasks" / "T-1-2.yaml").read_text(encoding="utf-8")
    assert "T-1-1" in task2 and "httpx" in task2 and "src/svc/api/handler.py" in task2
    spec = next(project.glob(".nexo/specs/F-1-*")) / "spec.yaml"
    assert "não resta ocorrência" in spec.read_text(encoding="utf-8")
    # progresso recontado sem IA
    (src / "client.py").write_text(
        "import httpx\n\n\ndef get(url):\n    return httpx.get(url, timeout=10)\n", encoding="utf-8"
    )
    code, out, _ = nexo("--json", "modernize", "status", "F-1")
    st = json.loads(out)
    assert (st["before"], st["now"], st["progress"]) == (2, 1, 50.0)
    assert {m["module"]: m["progress"] for m in st["modules"]} == {"src/svc": 100.0, "src/svc/api": 0.0}
    code, out, _ = nexo(
        "--json", "modernize", "plan", "--find", "timeout=10", "--goal", "timeout vindo da configuração"
    )
    assert json.loads(out)["feature"] == "F-2"
    code, _, err = nexo("modernize", "plan", "--playbook", "nao-existe")
    assert code != 0 and "roteiro desconhecido" in err
