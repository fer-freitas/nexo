"""estrutura do código: todo módulo diz o seu papel, nenhum cresce sem limite e o mapa da arquitetura está
em dia com o código."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "nexo"
MAX_LINES = 700
DATA_TABLES = {"cli/registry.py"}  # tabelas de dados, não lógica


def modules() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "resources" not in p.relative_to(SRC).parts)


def test_every_module_states_its_role() -> None:
    missing = [
        p.relative_to(SRC).as_posix()
        for p in modules()
        if not ast.get_docstring(ast.parse(p.read_text(encoding="utf-8")))
    ]
    assert not missing, f"módulos sem docstring (o papel do arquivo): {missing}"


def test_no_module_grows_past_the_limit() -> None:
    big = {
        p.relative_to(SRC).as_posix(): n
        for p in modules()
        if (n := len(p.read_text(encoding="utf-8").splitlines())) > MAX_LINES
        and p.relative_to(SRC).as_posix() not in DATA_TABLES
    }
    assert not big, f"módulos acima de {MAX_LINES} linhas (dividir por responsabilidade): {big}"


def test_no_service_suffix_inside_contexts() -> None:
    legacy = [p.relative_to(SRC).as_posix() for p in (SRC / "app").rglob("*_service.py")]
    assert not legacy, f"dentro da pasta do contexto o módulo leva o nome do assunto: {legacy}"


TEXT_IO_DIRS = (SRC, ROOT / "scripts", ROOT / "tests")


def _encoding_violations(path: Path) -> list[str]:
    """Leitura/escrita de texto sem `encoding` explícito: no Windows o padrão é cp1252 e os acentos quebram."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        kw = {k.arg for k in node.keywords}
        if isinstance(func, ast.Attribute) and func.attr in ("read_text", "write_text"):
            positional = 1 if func.attr == "read_text" else 2  # read_text(encoding) / write_text(data, encoding)
            if "encoding" not in kw and len(node.args) < positional:
                out.append(f"{path.relative_to(ROOT)}:{node.lineno} {func.attr} sem encoding")
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
        is_builtin_open = isinstance(func, ast.Name) and func.id == "open"
        is_path_open = (
            isinstance(func, ast.Attribute)
            and func.attr == "open"
            and not (
                isinstance(func.value, ast.Name) and func.value.id in ("os", "tarfile", "zipfile", "webbrowser", "gzip")
            )
        )
        if is_builtin_open or is_path_open:
            mode_arg = (
                node.args[1]
                if is_builtin_open and len(node.args) > 1
                else (node.args[0] if is_path_open and node.args else None)
            )
            mode = next((k.value for k in node.keywords if k.arg == "mode"), mode_arg)
            text_mode = not (isinstance(mode, ast.Constant) and "b" in str(mode.value))
            if text_mode and "encoding" not in kw and not (is_builtin_open and len(node.args) > 3):
                out.append(f"{path.relative_to(ROOT)}:{node.lineno} open em modo texto sem encoding")
        if (
            name in ("run", "Popen", "check_output", "check_call")
            and {"text", "universal_newlines"} & kw
            and ("encoding" not in kw)
        ):
            out.append(f"{path.relative_to(ROOT)}:{node.lineno} {name}(text=True) sem encoding")
    return out


def test_every_text_read_and_write_is_utf8() -> None:
    """F50 (pedido do usuário): toda leitura e escrita em UTF-8, também a saída de git e das CLIs no Windows."""
    found = [v for d in TEXT_IO_DIRS for p in sorted(d.rglob("*.py")) for v in _encoding_violations(p)]
    assert found == [], "\n".join(found)
