from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import keyring
import pytest
from keyring.backend import KeyringBackend

from nexo.cli import main as cli_main

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


class MemoryKeyring(KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        from keyring.errors import PasswordDeleteError

        if (service, username) not in self.store:
            raise PasswordDeleteError(username)
        del self.store[(service, username)]


@pytest.fixture(autouse=True)
def memory_keyring() -> Iterator[MemoryKeyring]:
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)


@pytest.fixture(autouse=True)
def local_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Testes usam identidade local (como uma org que libera `identity.mode: os`); os de identidade
    verificada revertem com `verified_identity`."""
    from nexo.kernel.config.defaults import DEFAULTS

    monkeypatch.setitem(DEFAULTS["identity"], "mode", "os")


@pytest.fixture(autouse=True)
def isolated_claude_config(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nenhum teste toca o Claude Code real: a guarda (instalada sozinha pelo piloto automático) vai para uma pasta
    temporária."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path_factory.mktemp("claude-config")))


@pytest.fixture
def nexo_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "nexo-home"
    monkeypatch.setenv("NX_HOME", str(home))
    # preparar o .venv de cada worktree faz pip install com rede; nos testes, só onde o teste liga
    monkeypatch.setenv("NX_PREPARE_WORKTREE", "0")
    monkeypatch.setenv("NX_REVIEW_BREAKDOWN", "0")  # 0.53.7: revisão da quebra só nos testes dela
    # o piloto automático em segundo plano do console sobrevive ao teste e mexeria nas pastas do próximo;
    # os testes dele chamam `autopilot.run` direto
    monkeypatch.setenv("NX_AUTOPILOT", "0")
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("NO_COLOR", "1")
    return home


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, nexo_home: Path) -> Path:
    wd = tmp_path / "work"
    wd.mkdir()
    monkeypatch.chdir(wd)
    return wd


class Runner:
    def __init__(self, capsys: pytest.CaptureFixture[str]) -> None:
        self.capsys = capsys

    def __call__(self, *args: str) -> tuple[int, str, str]:
        self.capsys.readouterr()
        code = cli_main.main(list(args))
        out = self.capsys.readouterr()
        return code, out.out, out.err


@pytest.fixture
def nexo(capsys: pytest.CaptureFixture[str], workdir: Path) -> Runner:
    return Runner(capsys)


# ---------------- projeto git + cenário do agente fake ----------------

import shutil  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
from typing import Any  # noqa: E402

from ruamel.yaml import YAML  # noqa: E402

DEFAULT_TASK: dict[str, Any] = {
    "title": "Adicionar função version()",
    "kind": "backend",
    "stack": "python",
    "description": "Criar src/svc/version.py com version() retornando 0.1.0",
    "acceptance": ["version() retorna 0.1.0"],
    "files_hint": ["src/svc/version.py"],
}


def dump_yaml(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    y = YAML()
    with path.open("w", encoding="utf-8") as fh:
        y.dump(data, fh)
    return path


def git_cmd(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()


@pytest.fixture
def project(workdir: Path, monkeypatch: pytest.MonkeyPatch, nexo: Runner) -> Path:
    root = workdir / "svc"
    shutil.copytree(EXAMPLES / "python-svc", root)
    git_cmd(root, "init", "-q")
    monkeypatch.chdir(root)
    assert nexo("init")[0] == 0
    dump_yaml(root / ".nexo" / "tasks" / "T-1.yaml", DEFAULT_TASK)
    git_cmd(root, "add", "-A")
    git_cmd(root, "commit", "-qm", "init")
    return root


@pytest.fixture
def scenario(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    def make(steps: list[dict[str, Any]], tools: dict[str, Any] | None = None) -> Path:
        data = {"tools": tools or {"claude": {}, "copilot": {}}, "steps": steps}
        path = dump_yaml(tmp_path / "scenario.yaml", data)
        monkeypatch.setenv("NX_FAKE_SCENARIO", str(path))
        return path

    return make


def edit(
    path: str = "src/svc/version.py", content: str = "def version() -> str:\n    return '0.1.0'\n"
) -> dict[str, Any]:
    return {"edit": {"path": path, "content": content}}


# ---------------- receita rápida e offline (override do cérebro) ----------------

FAST_PYTHON_RECIPE = {
    "stack": "python",
    "stages": [
        {
            "id": "lint",
            "kind": "lint",
            "tool": sys.executable,
            "run": [sys.executable, "-m", "ruff", "check", "."],
            "parser": "exitcode",
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


@pytest.fixture
def fast_recipe(nexo_home: Path) -> Path:
    dump_yaml(nexo_home / "brain" / "recipes" / "python.yaml", FAST_PYTHON_RECIPE)
    dump_yaml(nexo_home / "brain" / "recipes" / "common.yaml", {"stages": []})
    return nexo_home / "brain"
