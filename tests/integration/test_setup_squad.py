"""`nexo setup` instala só com permissão, e todo mundo consegue dizer de qual squad participa."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nexo.modules.setup.app import install
from tests.conftest import Runner, dump_yaml


def test_repository_default_then_personal_choice_wins(project: Path, nexo: Runner) -> None:
    assert json.loads(nexo("--json", "squad")[1])["squad"] == ""
    assert nexo("squad", "set", "sq-cartoes", "--project")[0] == 0
    assert json.loads(nexo("--json", "squad")[1])["squad"] == "sq-cartoes"
    assert nexo("squad", "set", "sq-pagamentos")[0] == 0
    assert json.loads(nexo("--json", "squad")[1])["squad"] == "sq-pagamentos"
    assert nexo("squad", "set", "nome com espaço")[0] != 0


def test_squad_shows_up_in_doctor_and_list(project: Path, nexo: Runner, nexo_home: Path) -> None:
    (nexo_home / "brain" / "squads" / "sq-a").mkdir(parents=True)
    assert "sq-a" in json.loads(nexo("--json", "squad", "list")[1])
    checks = {c["name"]: c for c in json.loads(nexo("--json", "doctor")[1])["checks"]}
    assert "não informado" in checks["squad"]["detail"]


def fake_machine(monkeypatch: pytest.MonkeyPatch, has: set[str]) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(cmd: list[str], **_kw: Any) -> Any:
        calls.append(cmd)
        has.add(cmd[-1])
        return type("Done", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(install.system, "which", lambda n: f"/bin/{n}" if n in has else None)
    monkeypatch.setattr(install.shutil, "which", lambda n: "/bin/brew" if n == "brew" else None)
    monkeypatch.setattr(install.sys, "platform", "darwin")
    monkeypatch.setattr(install.subprocess, "run", run)
    monkeypatch.setattr(install.toolenv, "path_for", lambda _n: Path(__file__))
    return calls


def test_check_never_installs_and_fails_when_something_required_is_missing(
    nexo: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = fake_machine(monkeypatch, {"git"})
    code, out, _ = nexo("--json", "setup", "--check")
    assert code != 0 and calls == []
    assert {i["name"]: i["present"] for i in json.loads(out)["items"]}["gitleaks"] is False


def test_no_install_without_permission_and_with_yes_it_installs(nexo: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_machine(monkeypatch, {"git"})
    assert nexo("--json", "setup")[0] != 0 and calls == [], "sem terminal e sem --yes, não instala"
    code, out, err = nexo("--json", "setup", "--yes", "--squad", "sq-a")
    assert code == 0, err + out
    assert ["brew", "install", "gitleaks"] in calls
    assert json.loads(out)["squad"] == "sq-a"


def test_a_failed_install_is_reported_and_blocks(nexo: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_machine(monkeypatch, {"git"})
    monkeypatch.setattr(
        install.subprocess,
        "run",
        lambda *_a, **_k: type("Done", (), {"returncode": 1, "stdout": "", "stderr": "bloqueado pela TI"})(),
    )
    code, out, _ = nexo("--json", "setup", "--yes")
    assert code != 0 and "bloqueado pela TI" in out


def test_required_stack_tool_comes_from_the_recipe(
    project: Path, nexo: Runner, fast_recipe: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recipe = {
        "stages": [{"id": "x", "tool": "ferramenta-da-stack", "run": ["x"], "parser": "exitcode", "required": True}]
    }
    dump_yaml(fast_recipe / "recipes" / "python.yaml", recipe)
    code, out, _ = nexo("--json", "setup", "--check")
    assert "ferramenta-da-stack" in {i["name"] for i in json.loads(out)["items"]}
