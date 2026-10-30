"""Gera o binário `onedir`, sem exigir Python na máquina de quem usa. Precisa ser rodado no sistema de destino."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = "from nexo.cli.main import run\n\nrun()\n"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="nexo-build-", ignore_cleanup_errors=True) as tmp:
        entry = Path(tmp) / "nexo_entry.py"
        entry.write_text(ENTRY, encoding="utf-8")
        cmd = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--onedir",
            "--console",
            "--name",
            "nexo",
            "--distpath",
            str(ROOT / "dist"),
            "--workpath",
            str(Path(tmp) / "build"),
            "--specpath",
            tmp,
            "--paths",
            str(ROOT / "src"),
            # os módulos entram por `module.yaml`, e não por import direto
            "--collect-submodules",
            "nexo",
            "--collect-data",
            "nexo",
            "--collect-submodules",
            "keyring.backends",
            "--collect-submodules",
            "uvicorn",
            # `pyarrow` só serve à exportação do central na AWS
            "--exclude-module",
            "pyarrow",
            str(entry),
        ]
        done = subprocess.run(cmd, cwd=ROOT, check=False)
    if done.returncode != 0:
        return done.returncode
    exe = ROOT / "dist" / "nexo" / ("nexo.exe" if sys.platform == "win32" else "nexo")
    check = subprocess.run([str(exe), "version"], capture_output=True, text=True, encoding="utf-8", check=False)
    print(check.stdout.strip() or check.stderr.strip())
    return check.returncode


if __name__ == "__main__":
    raise SystemExit(main())
