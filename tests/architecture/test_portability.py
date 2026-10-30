"""Windows é o destino, mas o desenvolvimento roda em macOS: o que dá para provar sem Windows é provado aqui."""

from __future__ import annotations

import ast
import ntpath
from collections.abc import Iterator
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "nexo"
# Só estes pontos chamam um shell, e os dois escolhem pelo sistema: no Windows, `cmd /c` e `powershell`.
SHELL_ALLOWED = {"modules/execution/adapters/agents/api.py", "modules/setup/app/selfmanage.py"}


def trees() -> Iterator[tuple[str, ast.AST]]:
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if "resources" not in path.relative_to(SRC).parts:
            yield rel, ast.parse(path.read_text(encoding="utf-8"))


def calls(tree: ast.AST) -> Iterator[ast.Call]:
    return (n for n in ast.walk(tree) if isinstance(n, ast.Call))


def name_of(call: ast.Call) -> str:
    return call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")


def kw(call: ast.Call, key: str) -> ast.expr | None:
    return next((k.value for k in call.keywords if k.arg == key), None)


def test_no_shell_true() -> None:
    found = []
    for rel, tree in trees():
        for call in calls(tree):
            value = kw(call, "shell")
            if isinstance(value, ast.Constant) and value.value is True:
                found.append(f"{rel}:{call.lineno}")
    assert not found, f"shell=True não existe no Nexo: {found}"


def test_no_sh_or_bash_in_commands() -> None:
    found = []
    for rel, tree in trees():
        if rel in SHELL_ALLOWED:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.List) and node.elts:
                first = node.elts[0]
                if isinstance(first, ast.Constant) and first.value in ("sh", "bash", "/bin/sh", "/bin/bash"):
                    found.append(f"{rel}:{node.lineno}")
    assert not found, f"o fluxo não pode depender de sh: {found}"


def test_no_fixed_tmp_path() -> None:
    found = []
    for rel, tree in trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("/tmp"):
                found.append(f"{rel}:{node.lineno}")
    assert not found, f"use tempfile: {found}"


def test_text_files_always_declare_the_encoding() -> None:
    """Sem isso, no Windows o padrão é cp1252: o mesmo arquivo teria outro conteúdo e outro hash."""
    found = []
    for rel, tree in trees():
        for call in calls(tree):
            name = name_of(call)
            if name == "read_text" and not (call.args or kw(call, "encoding")):
                found.append(f"{rel}:{call.lineno} read_text")
            elif name == "write_text" and len(call.args) < 2 and not kw(call, "encoding"):
                found.append(f"{rel}:{call.lineno} write_text")
    assert not found, found


def test_subprocess_with_text_declares_the_encoding() -> None:
    found = []
    for rel, tree in trees():
        for call in calls(tree):
            text = kw(call, "text")
            if (
                name_of(call) in ("run", "Popen", "check_output")
                and isinstance(text, ast.Constant)
                and text.value is True
                and kw(call, "encoding") is None
            ):
                found.append(f"{rel}:{call.lineno}")
    assert not found, f"saída de processo sem encoding quebra com acento no Windows: {found}"


def test_policy_understands_windows_paths() -> None:
    from nexo.kernel.security.policy import _relative

    work = "C:\\Users\\ana\\AppData\\Local\\nexo\\wt\\ab12\\T-1"
    assert _relative(work + "\\src\\a.cs", work) == "src/a.cs"
    assert _relative("src\\a.cs", work) == "src/a.cs"
    assert _relative("C:\\Windows\\system32\\x", work) is None
    assert _relative(work.lower() + "\\b.cs", work) == "b.cs", "o sistema de arquivos não diferencia maiúscula"
    assert _relative(work + "\\..\\..\\fora.txt", work) is None


def test_worktree_stays_short_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    from nexo.kernel.workspace import git

    monkeypatch.setenv("LOCALAPPDATA", "C:\\Users\\ana.silva\\AppData\\Local")
    deep = Path("C:/Users/ana.silva/source/repos/empresa/time-posicao/gestao-de-posicao-servico")
    path = git.worktree_path(deep, "US-12-3", "win32")
    text = ntpath.normpath(str(path))
    assert "time-posicao" not in text and "wt" in text
    assert len(text) < 80, "sobra espaço para os caminhos profundos de um repositório .NET"
    assert git.worktree_path(deep, "US-12-3", "win32") == path, "mesmo projeto, mesma pasta"
    assert git.worktree_path(deep, "US-12-3", "darwin") == deep / ".nexo" / "worktrees" / "US-12-3"


def test_git_uses_long_paths_on_windows() -> None:
    from nexo.kernel.workspace import git

    assert git.git_prefix("win32")[:3] == ["git", "-c", "core.longpaths=true"]
    assert git.git_prefix("darwin") == ["git"]


def test_removal_retries_while_an_antivirus_holds_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil

    from nexo.kernel.workspace import git

    target = tmp_path / "wt"
    target.mkdir()
    real, attempts = shutil.rmtree, []

    def flaky(path: Path, **kwargs: object) -> None:
        attempts.append(path)
        if len(attempts) < 3:
            raise PermissionError("em uso por outro processo")
        real(path)

    monkeypatch.setattr(shutil, "rmtree", flaky)
    monkeypatch.setattr(git.time, "sleep", lambda _s: None)
    git.remove_tree(target)
    assert len(attempts) == 3 and not target.exists()


def test_section_hash_ignores_line_endings_and_bom() -> None:
    from nexo.modules.demands.app import sections

    lf = sections._sha({"text": "linha 1\nlinha 2\n"})
    assert sections._sha({"text": "linha 1\r\nlinha 2\r\n"}) == lf
    assert sections._sha({"text": "\ufefflinha 1\nlinha 2\n"}) == lf


def test_note_with_crlf_and_bom_is_read_the_same() -> None:
    from nexo.modules.knowledge.domain.notes import parse

    text = "---\nid: a\ntype: lesson\nscope: global\n---\n\nTexto.\n"
    plain = parse("a.md", text)
    windows = parse("a.md", "\ufeff" + text.replace("\n", "\r\n"))
    assert windows.id == plain.id and windows.meta == plain.meta


def test_names_that_differ_only_by_case_are_found() -> None:
    from nexo.kernel.workspace.git import case_collisions

    assert case_collisions(["a/Pos.yaml", "a/pos.yaml", "b/x.yaml"]) == [["a/Pos.yaml", "a/pos.yaml"]]
    assert case_collisions(["a.yaml", "b.yaml"]) == []
