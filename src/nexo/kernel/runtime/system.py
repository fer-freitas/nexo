"""Sondagem de executáveis instalados (sem shell)."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

_VERSION = re.compile(r"(\d+\.\d+(?:\.\d+)?)")


@dataclass(frozen=True)
class ToolProbe:
    name: str
    path: str | None
    version: str | None

    @property
    def installed(self) -> bool:
        return self.path is not None


# Atalho `.cmd` que o npm cria no Windows: `"%_prog%"  "%dp0%\node_modules\pacote\cli.js" %*` (ou `%~dp0`).
_NPM_SHIM = re.compile(r'"%~?dp0%?\\?([^"%]+\.[cm]?js)"\s*%\*', re.I)


def which(executable: str) -> str | None:
    return shutil.which(executable)


def npm_shim_target(text: str) -> str | None:
    """Script JavaScript que um atalho `.cmd` do npm executa (caminho relativo à pasta do atalho)."""
    match = _NPM_SHIM.search(text)
    return match.group(1).replace("\\", "/") if match else None


def _windows() -> bool:
    return os.name == "nt"


def windows_command(cmd: list[str]) -> list[str]:
    """No Windows, CLI instalada pelo npm é um `.cmd`, e o `cmd.exe` corta argumentos com quebra de linha (o prompt
    chegaria truncado). Como o cross-spawn, chama o `node` direto com o script do pacote; demais casos, sem mudança."""
    if not _windows() or not cmd:
        return cmd
    found = cmd[0] if Path(cmd[0]).is_absolute() else shutil.which(cmd[0])
    if not found or not found.lower().endswith((".cmd", ".bat")):
        return cmd
    shim = Path(found)
    try:
        target = npm_shim_target(shim.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return cmd
    if target is None:
        return cmd
    local_node = shim.parent / "node.exe"
    node = str(local_node) if local_node.is_file() else shutil.which("node")
    if node is None:
        return cmd
    return [node, str(shim.parent.joinpath(*target.split("/"))), *cmd[1:]]


def probe(executable: str, args: tuple[str, ...] = ("--version",), timeout: float = 5.0) -> ToolProbe:
    path = which(executable)
    if path is None:
        return ToolProbe(executable, None, None)
    try:
        proc = subprocess.run(
            windows_command([path, *args]),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            encoding="utf-8",
        )
    except (OSError, subprocess.TimeoutExpired):
        return ToolProbe(executable, path, None)
    match = _VERSION.search((proc.stdout or "") + (proc.stderr or ""))
    return ToolProbe(executable, path, match.group(1) if match else None)


def shell_join(parts: list[str]) -> str:
    if os.name == "nt":
        return " ".join(f'"{p}"' if " " in p else p for p in parts)
    return shlex.join(parts)
