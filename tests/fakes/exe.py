"""Executável falso portável para os testes: script Python com shebang no Unix; no Windows, um atalho `.cmd` no
formato do npm que chama um script Node, que executa o script Python (o mesmo caminho das CLIs reais instaladas pelo
npm, que o Nexo resolve para `node <script>`)."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path


def fake_exe(bin_dir: Path, name: str, body: str) -> Path:
    """Cria `name` em `bin_dir` rodando `body` (código Python) e devolve o caminho a chamar."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        script = bin_dir / name
        script.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        return script
    impl = bin_dir / f"{name}-impl.py"
    impl.write_text(body, encoding="utf-8")
    (bin_dir / f"{name}.js").write_text(
        "const { spawnSync } = require('child_process');\n"
        f"const r = spawnSync({json.dumps(sys.executable)}, [{json.dumps(str(impl))}, ...process.argv.slice(2)],"
        " { stdio: 'inherit' });\n"
        "process.exit(r.status === null ? 1 : r.status);\n",
        encoding="utf-8",
    )
    shim = bin_dir / f"{name}.cmd"
    shim.write_text(f'@ECHO off\r\nSETLOCAL\r\nSET dp0=%~dp0\r\n"node"  "%dp0%\\{name}.js" %*\r\n', encoding="utf-8")
    return shim
