"""Todo módulo passa pelo mesmo kit, e um módulo novo entra de verdade só com a pasta."""

from __future__ import annotations

from pathlib import Path

import pytest
import typer
import typer.main

from nexo.kernel import modules
from nexo.kernel.config.flags import flag_for_command
from nexo.testing import ModuleContract
from tests.conftest import Runner, dump_yaml

MANIFESTS = {m.id: m for m in modules.builtin()}


@pytest.fixture(scope="module")
def cli() -> object:
    from nexo.cli.main import app

    return typer.main.get_command(app)


@pytest.mark.parametrize("module_id", sorted(MANIFESTS))
def test_module_contract(module_id: str, cli: object) -> None:
    contract = ModuleContract(MANIFESTS[module_id])
    assert contract.manifest_problems() == []
    assert contract.missing_commands(cli) == []
    assert contract.commands_without_flag(flag_for_command) == []


def test_cli_survives_with_every_optional_flag_off(nexo: Runner, nexo_home: Path) -> None:
    keys = [f.key for m in modules.builtin() for f in m.flags if f.key not in modules.FLOOR]
    dump_yaml(nexo_home / "brain" / "org" / "flags.yaml", {"flags": dict.fromkeys(keys, False)})
    for args in (["help"], ["version"], ["doctor"], ["module", "list"], ["flags", "list"]):
        code, out, err = nexo(*args)
        assert code == 0, f"{args}: {err or out}"
    code, _, err = nexo("board")
    assert code != 0 and "demands.board" in err, "o comando desligado diz qual flag desligou"


def test_new_folder_adds_command_and_flag_turns_it_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem editar arquivo central: só a pasta do módulo."""
    package = tmp_path / "hello_mod"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "cli.py").write_text(
        'def register(root, tools):\n    root.command("hello")(lambda: print("oi"))\n', encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    root = tmp_path / "root"
    dump_yaml(
        root / "modules" / "hello" / "module.yaml",
        {
            "id": "hello",
            "version": "1.0.0",
            "title": "Olá",
            "flags": [{"key": "hello.world", "title": "Olá", "commands": ["hello"]}],
            "cli": "hello_mod.cli:register",
            "commands": [{"path": "hello", "summary": "Diz oi"}],
        },
    )
    modules.builtin.cache_clear()
    monkeypatch.setattr(modules, "BUILTIN_ROOT", root)
    try:
        from nexo.cli import surface

        app = typer.Typer()
        app.command("nada")(lambda: None)
        surface.register_modules(app, surface.Tools(None, None, None, None, lambda _a: 0))  # type: ignore[arg-type]
        assert "hello" in typer.main.get_command(app).commands  # type: ignore[attr-defined]
        assert [c.path for c in surface.commands_for()] == ["hello"]
        assert ModuleContract(modules.builtin()[0]).manifest_problems() == []
    finally:
        monkeypatch.undo()
        modules.builtin.cache_clear()
