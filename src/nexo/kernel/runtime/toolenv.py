"""Ferramentas em ambiente próprio.

Ferramentas com dependências muito travadas (semgrep, pip-audit) não cabem no ambiente do Nexo nem no do projeto.
Cada uma ganha um `venv` em `<NX_HOME>/tools/<nome>/`, criado e instalado com pip na primeira vez que uma etapa
precisa dela (versão travada abaixo; a org pode trocar em `quality.tools`), e reaproveitado depois.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

PINNED = {"semgrep": "semgrep==1.179.0", "pip-audit": "pip-audit==2.10.1"}


def home() -> Path:
    env = os.environ.get("NX_HOME")
    if env:
        return Path(env).expanduser().resolve()
    from nexo.kernel.context import nexo_home

    return nexo_home()


def _scripts(venv: Path) -> Path:
    return venv / ("Scripts" if sys.platform == "win32" else "bin")


def path_for(name: str) -> Path:
    exe = f"{name}.exe" if sys.platform == "win32" else name
    return _scripts(home() / "tools" / name) / exe


def base_python() -> str:
    """Python que cria os ambientes: o do Nexo; no binário empacotado, o do sistema."""
    if not getattr(sys, "frozen", False):
        return sys.executable
    return shutil.which("python3") or shutil.which("python") or shutil.which("py") or "python"


def ensure(name: str, requirement: str = "") -> tuple[str, str]:
    """(caminho da ferramenta, erro). Já instalada no ambiente próprio, ao lado do Nexo ou no PATH: usa."""
    target = path_for(name)
    if target.is_file():
        return str(target), ""
    beside = Path(sys.executable).parent / (f"{name}.exe" if sys.platform == "win32" else name)
    if beside.is_file():
        return str(beside), ""
    found = shutil.which(name)
    if found:
        return found, ""
    venv = home() / "tools" / name
    spec = requirement or PINNED.get(name, name)
    try:
        subprocess.run([base_python(), "-m", "venv", str(venv)], check=True, capture_output=True, timeout=300)
        python = _scripts(venv) / ("python.exe" if sys.platform == "win32" else "python")
        proc = subprocess.run(
            [str(python), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", spec],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "", f"não consegui criar o ambiente de {name}: {exc}"
    if proc.returncode != 0 or not target.is_file():
        return "", f"instalação de {spec} falhou: {(proc.stderr or proc.stdout).strip()[-300:]}"
    return str(target), ""
