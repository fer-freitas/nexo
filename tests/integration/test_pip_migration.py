"""o pip no lugar do uv: ambiente do projeto, receita Python, ferramentas em ambiente próprio, dependências e
o CI gerado."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from nexo.kernel.runtime import toolenv
from nexo.modules.execution.app.quality import workflow
from nexo.modules.quality.adapters.runner import ARGS_SEP, expand, load_recipe, python_env, run_pipeline, run_stage

PY = sys.executable


def test_python_env_installs_what_the_project_declares(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "1"\n[project.optional-dependencies]\ndev = ["pytest"]\n', encoding="utf-8"
    )
    env = python_env(tmp_path)
    assert env["pip_install"].split(ARGS_SEP) == ["-e", ".[dev]"] and env["pip_project"] == ""
    assert env["venv"] == ".venv"
    assert env["venv_python"].endswith(("python.exe", "python")) and ".venv" in env["venv_python"]
    (tmp_path / "requirements-dev.txt").write_text("pytest==9.1.1\n", encoding="utf-8")
    env = python_env(tmp_path)
    assert env["pip_install"].split(ARGS_SEP) == ["-r", "requirements-dev.txt"]
    assert env["pip_project"].split(ARGS_SEP) == ["-e", "."]  # projeto entra depois, sem dependências (hashes)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n[dependency-groups]\ndev = ["pytest"]\n', encoding="utf-8"
    )
    (tmp_path / "requirements-dev.txt").unlink()
    assert python_env(tmp_path)["pip_install"].split(ARGS_SEP) == ["-e", ".", "--group", "dev"]
    venv = tmp_path / "venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
    assert python_env(tmp_path)["venv"] == "venv"  # ambiente já existente do projeto é respeitado
    ci = python_env(tmp_path, ci=True)
    assert ci["venv_python"] == "venv/bin/python" and ci["python"] == "python"


def test_expand_unless_exists_and_pre_stdout(tmp_path: Path) -> None:
    assert expand(["pip", "install", f"-r{ARGS_SEP}req.txt", "", "x"], {}) == ["pip", "install", "-r", "req.txt", "x"]
    out = tmp_path / "o"
    stage = {
        "id": "s",
        "kind": "build",
        "parser": "exitcode",
        "required": True,
        "unless_exists": "{venv}",
        "pre": [{"run": [PY, "-c", "print('requests==2.25.0')"], "stdout": "{out}/freeze.txt"}],
        "run": [PY, "-c", "1"],
    }
    subs = {"out": str(out), "venv": ".venv"}
    assert run_stage(stage, tmp_path, out, subs).status == "passed"
    assert (out / "freeze.txt").read_text(encoding="utf-8").strip() == "requests==2.25.0"
    (tmp_path / ".venv").mkdir()
    skipped = run_stage(stage, tmp_path, out, subs)
    assert skipped.status == "skipped" and ".venv já existe" in skipped.summary


def test_ci_workflow_for_python_uses_pip(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    (tmp_path / "requirements-dev.txt").write_text("pytest\n", encoding="utf-8")
    steps = workflow("python", load_recipe("python"), tmp_path)["jobs"]["quality"]["steps"]
    text = "\n".join(str(s.get("run", "")) + str(s.get("uses", "")) for s in steps)
    assert "actions/setup-python" in text and "setup-uv" not in text and "uv " not in text
    by = {str(s.get("name", "")).split(" ")[0]: s for s in steps}
    assert by["venv"]["run"].startswith("if [ ! -e .venv ]; then\n  python -m venv .venv")
    assert (
        by["install"]["run"]
        == ".venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements-dev.txt"
    )
    assert "--no-deps -e ." in by["install-project"]["run"]
    assert (
        '.venv/bin/python -m pip freeze --exclude-editable --disable-pip-version-check > "$RUNNER_TEMP/nexo/requirements-freeze.txt"'
        in by["sca"]["run"]
    )
    assert by["sca"]["run"].splitlines()[-1].startswith("pip-audit --strict")
    assert any("pip install --quiet pip-audit semgrep" in str(s.get("run", "")) for s in steps)


def test_tool_in_own_environment_is_reused_and_reports_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NX_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(toolenv.shutil, "which", lambda name: None)
    target = toolenv.path_for("fake-tool")
    target.parent.mkdir(parents=True)
    target.write_text("", encoding="utf-8")
    assert toolenv.ensure("fake-tool") == (str(target), "")  # já instalada: reaproveita, sem rede
    calls: list[list[str]] = []

    class Failed:
        returncode, stdout, stderr = 1, "", "No matching distribution found for nao-existe"

    def fake_run(cmd: list[str], **_kw: object) -> Failed:
        calls.append(cmd)
        return Failed()

    monkeypatch.setattr(toolenv.subprocess, "run", fake_run)
    path, problem = toolenv.ensure("nao-existe", "nao-existe==1.0")
    assert path == "" and "nao-existe==1.0" in problem and "No matching distribution" in problem
    assert calls[0][1:3] == ["-m", "venv"] and calls[1][1:4] == ["-m", "pip", "install"]


@pytest.mark.skipif(shutil.which("git") is None, reason="git ausente")
def test_real_python_recipe_with_pip(tmp_path: Path) -> None:
    """Receita real (rede): cria o .venv, instala pelos requirements, roda ruff, pytest e o pip-audit."""
    from nexo.kernel.runtime.resources import embedded_dir

    work = tmp_path / "svc"
    for src in embedded_dir("selftest/python-svc").rglob("*"):
        if src.is_file() and "__pycache__" not in src.parts:
            rel = src.relative_to(embedded_dir("selftest/python-svc")).as_posix()
            rel = (
                ".gitignore" if rel == "gitignore.txt" else rel.removesuffix(".txt") if rel.endswith(".py.txt") else rel
            )
            (work / rel).parent.mkdir(parents=True, exist_ok=True)
            (work / rel).write_bytes(src.read_bytes())
    import subprocess

    git = ["git", "-c", "user.email=t@x", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q"], cwd=work, check=True)
    (work / ".gitignore").write_text("", encoding="utf-8")  # sem .gitignore do time: o Nexo protege sozinho
    subprocess.run([*git, "add", "-A"], cwd=work, check=True)
    subprocess.run([*git, "commit", "-qm", "base"], cwd=work, check=True)
    report = run_pipeline(
        "python", work, tmp_path / "out", None, ["venv", "install", "install-project", "lint", "unit"]
    )
    got = {s.id: s.status for s in report.stages}
    assert got == {
        "venv": "passed",
        "install": "passed",
        "install-project": "passed",
        "lint": "passed",
        "unit": "passed",
    }, [(s.id, s.summary) for s in report.stages]
    assert (work / ".venv" / "pyvenv.cfg").is_file()
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=work, capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout
    assert ".venv" not in status and "egg-info" not in status, status  # ambiente local nunca vira alteração


# ---------------- login das ferramentas de IA pelo Nexo ----------------


def test_login_runs_the_official_command_and_checks(nexo, workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """`nexo login devin`: chama o login oficial (herda o terminal) e confere com o status; não guarda nada."""
    from nexo.kernel.models.agent import AuthStatus, ToolInfo
    from nexo.modules.execution.adapters.agents.generic import GenericTextAdapter
    from nexo.modules.execution.app import agents as agents_app

    calls: list[list[str]] = []

    class Done:
        returncode = 0

    monkeypatch.setattr("subprocess.run", lambda cmd, **_kw: calls.append(list(cmd)) or Done())
    monkeypatch.setattr(
        GenericTextAdapter, "detect", lambda self: ToolInfo(self.name, True, "1.0", f"/bin/{self.name}")
    )
    monkeypatch.setattr(GenericTextAdapter, "auth_status", lambda self: AuthStatus(True, "Logged in"))
    code, out, err = nexo("login", "devin")
    assert code == 0 and "devin: logado" in out, err
    assert calls[-1][-2:] == ["auth", "login"]
    with pytest.raises(Exception, match="ferramenta desconhecida"):
        from nexo.kernel.context import AppContext

        agents_app.login(AppContext(), "nao-existe")


def test_console_understands_login_phrase() -> None:
    from nexo.cli.registry import top_levels
    from nexo.modules.console.domain.intents import parse

    assert parse("faz login no devin", set(top_levels())).argv == ("login", "devin")
    assert parse("preciso autenticar o claude de novo", set(top_levels())).argv == ("login", "claude")
