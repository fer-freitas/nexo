"""Windows: CLI instalada pelo npm (`.cmd`) é chamada como `node <script>` para o prompt multilinha chegar inteiro."""

from __future__ import annotations

from pathlib import Path

import pytest

from nexo.kernel.runtime import system

MODERN = (
    "@ECHO off\r\nGOTO start\r\n:find_dp0\r\nSET dp0=%~dp0\r\nEXIT /b\r\n:start\r\nSETLOCAL\r\nCALL :find_dp0\r\n"
    'endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  '
    '"%dp0%\\node_modules\\@google\\gemini-cli\\dist\\index.js" %*\r\n'
)
LEGACY = '@IF EXIST "%~dp0\\node.exe" (\r\n  "%~dp0\\node.exe"  "%~dp0\\node_modules\\aider-js\\cli.js" %*\r\n)'


def test_npm_shim_target_formats() -> None:
    assert system.npm_shim_target(MODERN) == "node_modules/@google/gemini-cli/dist/index.js"
    assert system.npm_shim_target(LEGACY) == "node_modules/aider-js/cli.js"
    assert system.npm_shim_target("@echo off\r\npython tool.py %*\r\n") is None


def test_windows_command_calls_node_directly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shim = tmp_path / "gemini.cmd"
    shim.write_text(MODERN, encoding="utf-8")
    (tmp_path / "node.exe").write_text(
        "", encoding="utf-8"
    )  # node ao lado do atalho tem prioridade, como no próprio atalho
    prompt = "linha 1\nlinha 2 & echo injetado"
    monkeypatch.setattr(system, "_windows", lambda: False)
    assert system.windows_command([str(shim), "-p", prompt]) == [str(shim), "-p", prompt]  # fora do Windows: igual
    monkeypatch.setattr(system, "_windows", lambda: True)
    cmd = system.windows_command([str(shim), "-p", prompt])
    assert cmd[0] == str(tmp_path / "node.exe")
    assert Path(cmd[1]) == tmp_path / "node_modules" / "@google" / "gemini-cli" / "dist" / "index.js"
    assert cmd[2:] == ["-p", prompt]  # o prompt segue como argumento, sem passar pelo cmd.exe
    plain = tmp_path / "tool.bat"
    plain.write_text("@echo off\r\npython tool.py %*\r\n", encoding="utf-8")
    assert system.windows_command([str(plain), "x"]) == [str(plain), "x"]  # .bat que não é do npm: sem mudança
