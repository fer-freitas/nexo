"""Validação local do Nexo: o projeto não conta com CI, então este é o portão de cada versão.

Uso: python scripts/validate.py [--fast]

`--fast` roda lint, formatação, arquitetura e testes de unidade. Sem ele, roda também tipos e a suíte inteira.
A lista de termos proibidos vem de `NX_FORBIDDEN_TERMS` (separados por vírgula) e nunca fica no repositório.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
TEXT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".toml", ".json", ".txt", ".css", ".tf"}


def run(name: str, cmd: list[str]) -> dict[str, object]:
    start = time.monotonic()
    done = subprocess.run(
        cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )
    ok = done.returncode == 0
    print(f"[{'ok' if ok else 'FALHOU'}] {name} ({time.monotonic() - start:.0f}s)")
    if not ok:
        print((done.stdout + done.stderr)[-4000:])
    return {"step": name, "ok": ok, "seconds": round(time.monotonic() - start, 1)}


def forbidden_terms() -> dict[str, object]:
    terms = [t.strip().lower() for t in os.environ.get("NX_FORBIDDEN_TERMS", "").split(",") if t.strip()]
    hits: list[str] = []
    if terms:
        listed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        ).stdout.splitlines()
        for rel in listed:
            path = ROOT / rel
            if path.suffix in TEXT_SUFFIXES and path.is_file():
                text = path.read_text(encoding="utf-8", errors="replace").lower()
                hits += [rel for term in terms if term in text or term in rel.lower()]
    print(f"[{'ok' if not hits else 'FALHOU'}] termos proibidos ({len(terms)} termos)")
    if hits:
        print("\n".join(sorted(set(hits))))
    return {"step": "termos proibidos", "ok": not hits, "seconds": 0, "checked": bool(terms)}


def version() -> str:
    for line in (ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        if line.startswith("version"):
            return line.split('"')[1]
    return "local"


def install_hook() -> int:
    """`pre-push` opcional, só neste repositório, com a validação rápida."""
    hook = ROOT / ".git" / "hooks" / "pre-push"
    hook.write_text(
        f'#!/bin/sh\nexec "{Path(PY).as_posix()}" scripts/validate.py --fast\n', encoding="utf-8", newline="\n"
    )
    hook.chmod(0o755)
    print(f"hook instalado: {hook}")
    return 0


def optional_tools() -> list[dict[str, object]]:
    """Segredos, dependências e SAST. Ferramenta ausente fica registrada como não verificada, nunca como aprovada."""
    tools = {
        "gitleaks": ["gitleaks", "detect", "--no-banner", "--redact"],
        "pip-audit": ["pip-audit", "--progress-spinner", "off"],
        "semgrep": ["semgrep", "--config", "auto", "--error", "--quiet", "src"],
    }
    steps: list[dict[str, object]] = []
    for name, cmd in tools.items():
        if shutil.which(cmd[0]) or Path(PY).with_name(cmd[0]).is_file():
            steps.append(
                run(
                    name,
                    [str(Path(PY).with_name(cmd[0])) if Path(PY).with_name(cmd[0]).is_file() else cmd[0], *cmd[1:]],
                )
            )
        else:
            print(f"[não verificado] {name}: não instalado")
            steps.append({"step": name, "ok": True, "seconds": 0, "checked": False})
    return steps


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fast", action="store_true")
    parser.add_argument("--install-hook", action="store_true", help="Instala o pre-push opcional")
    args = parser.parse_args()
    if args.install_hook:
        return install_hook()
    steps = [
        forbidden_terms(),
        run("lint", [PY, "-m", "ruff", "check", "src", "tests", "scripts"]),
        run("formatação", [PY, "-m", "ruff", "format", "--check", "src", "tests", "scripts"]),
        run("camadas", [str(Path(PY).with_name("lint-imports")), "--no-cache"]),
        run("arquitetura", [PY, "-m", "pytest", "tests/architecture", "-q", "-p", "no:cacheprovider"]),
    ]
    if args.fast:
        steps.append(run("testes de unidade", [PY, "-m", "pytest", "tests/unit", "-q", "-n", "auto"]))
    else:
        steps.append(run("tipos", [PY, "-m", "mypy"]))
        steps.append(run("testes", [PY, "-m", "pytest", "tests", "-q", "-n", "auto", "--timeout=120"]))
        steps += optional_tools()
    ok = all(s["ok"] for s in steps)
    out = ROOT / "evidence" / version() / f"{platform.system().lower()}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"ok": ok, "python": platform.python_version(), "steps": steps}, indent=2), "utf-8")
    print("VALIDAÇÃO OK" if ok else "VALIDAÇÃO FALHOU")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
