"""Executor de receitas e `nexo quality run|ci` com comandos reais (sem rede)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from nexo.modules.quality.adapters.runner import run_pipeline, run_stage
from tests.conftest import EXAMPLES, Runner, dump_yaml


def stage(**kw):  # type: ignore[no-untyped-def]
    return {"id": "s", "kind": "lint", "parser": "exitcode", "required": True, **kw}


def test_stage_conditions_and_errors(tmp_path: Path) -> None:
    out = tmp_path / "out"
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    py = sys.executable
    assert run_stage(stage(run=[py, "-c", "print(1)"]), tmp_path, out, {"out": str(out)}).status == "passed"
    r = run_stage(
        stage(run=[py, "-c", "import sys; print('erro final'); sys.exit(2)"], default_severity="high"),
        tmp_path,
        out,
        {"out": str(out)},
    )
    assert r.status == "failed" and r.summary == "erro final" and r.findings[0].severity == "high"
    assert run_stage(stage(when_exists="nope", run=["x"]), tmp_path, out, {}).status == "skipped"
    assert run_stage(stage(when_glob="*.md", run=["x"]), tmp_path, out, {}).status == "skipped"
    assert (
        run_stage(stage(when_contains={"file": "a.txt", "text": "zzz"}, run=["x"]), tmp_path, out, {}).status
        == "skipped"
    )
    missing = run_stage(stage(tool="nao-existe-xyz", run=["x"]), tmp_path, out, {})
    assert missing.status == "error" and "ausente" in missing.summary
    optional = run_stage(stage(tool="nao-existe-xyz", run=["x"], required=False), tmp_path, out, {})
    assert optional.status == "skipped"
    pre = run_stage(stage(pre=[[py, "-c", "import sys; sys.exit(1)"]], run=[py, "-c", "1"]), tmp_path, out, {})
    assert pre.status == "error" and "preparação" in pre.summary
    slow = run_stage(stage(run=[py, "-c", "import time; time.sleep(5)"], timeout=1), tmp_path, out, {})
    assert slow.status == "error" and "tempo" in slow.summary
    junit_missing = run_stage(
        stage(parser="junit", report="{out}/j.xml", run=[py, "-c", "1"]), tmp_path, out, {"out": str(out)}
    )
    assert junit_missing.status == "failed"
    sarif_err = run_stage(
        stage(parser="sarif", report="{out}/none.sarif", run=[py, "-c", "import sys; sys.exit(3)"]),
        tmp_path,
        out,
        {"out": str(out)},
    )
    assert sarif_err.status == "error"


def test_python_example_real_tools(tmp_path: Path, fast_recipe: Path) -> None:
    work = tmp_path / "svc"
    shutil.copytree(EXAMPLES / "python-svc", work)
    report = run_pipeline("python", work, tmp_path / "out", fast_recipe)
    assert [(s.id, s.status) for s in report.stages] == [("lint", "passed"), ("unit", "passed")]
    assert report.coverage == 100.0 and report.tests[0].name == "test_health_ok"


def test_quality_cli_and_ci(project: Path, nexo: Runner, fast_recipe: Path) -> None:
    code, out, _ = nexo("quality", "run")
    assert code == 0 and "unit" in out and "cobertura" in out
    data = json.loads(nexo("--json", "quality", "run", "--stage", "lint")[1])
    assert [s["id"] for s in data["stages"]] == ["lint"]
    (project / "src/svc/bad.py").write_text("import os\n", encoding="utf-8")
    assert nexo("quality", "run")[0] == 1
    code, out, _ = nexo("quality", "ci")
    wf = project / ".github/workflows/nexo-quality.yml"
    assert code == 0 and wf.is_file() and "pytest" in wf.read_text(encoding="utf-8")
    if shutil.which("actionlint"):
        proc = subprocess.run(
            ["actionlint", str(wf)], capture_output=True, text=True, check=False, encoding="utf-8", errors="replace"
        )
        assert proc.returncode == 0, proc.stdout


def test_ci_for_all_stacks_is_valid(project: Path, nexo: Runner) -> None:
    for stack in ("python", "dotnet", "react"):
        assert nexo("quality", "ci", "--stack", stack)[0] == 0
        wf = project / ".github/workflows/nexo-quality.yml"
        if shutil.which("actionlint"):
            proc = subprocess.run(
                ["actionlint", str(wf)], capture_output=True, text=True, check=False, encoding="utf-8", errors="replace"
            )
            assert proc.returncode == 0, (stack, proc.stdout)


def test_quality_requires_stack(workdir: Path, nexo: Runner) -> None:
    nexo("init")
    code, _, err = nexo("quality", "run")
    assert code == 1 and "stack" in err
    dump_yaml(workdir / "x.yaml", {})


@pytest.mark.skipif(sys.platform == "win32", reason="o passo roda no runner Ubuntu do Actions; o Windows não tem bash")
def test_ci_step_with_file_condition_only_runs_when_it_applies(project: Path, nexo: Runner, tmp_path: Path) -> None:
    """Achado no CI real: a etapa `architecture` (import-linter) rodava sempre e reprovava projeto sem a ferramenta."""
    from ruamel.yaml import YAML

    assert nexo("quality", "ci", "--stack", "python")[0] == 0
    wf = YAML(typ="safe").load((project / ".github/workflows/nexo-quality.yml").read_text(encoding="utf-8"))
    step = next(s for s in wf["jobs"]["quality"]["steps"] if str(s.get("name", "")).startswith("architecture"))
    assert step["shell"] == "bash"
    work = tmp_path / "w"
    work.mkdir()
    (work / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    script = step["run"].replace(".venv/bin/lint-imports", "echo RODOU")
    out = subprocess.run(
        ["bash", "-c", script], cwd=work, capture_output=True, text=True, check=True, encoding="utf-8", errors="replace"
    ).stdout
    assert "não se aplica" in out and "RODOU" not in out
    (work / "pyproject.toml").write_text("[tool.importlinter]\nroot_package = 'x'\n", encoding="utf-8")
    out = subprocess.run(
        ["bash", "-c", script], cwd=work, capture_output=True, text=True, check=True, encoding="utf-8", errors="replace"
    ).stdout
    assert "RODOU" in out
