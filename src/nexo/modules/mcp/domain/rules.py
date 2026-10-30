"""O que torna um MCP aprovável: identidade pelo destino, versão fixa e ferramentas listadas.

O nome é só um rótulo, então a aprovação casa pela URL ou pelo comando com versão. `npx -y pacote` sem versão pode
mudar de código sem ninguém saber, por isso é recusado.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

STATES = ("listed", "configured", "connected", "used")
RUNNERS = {"npx", "uvx", "pipx", "bunx", "pnpx"}
VERSIONED = re.compile(r"^(@?[^@\s]+)@(\d+\.\d+[\w.\-+]*)$")
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class Identity:
    transport: str
    target: str  # URL, ou comando + argumentos

    @property
    def key(self) -> str:
        return f"{self.transport}:{self.target}"


def identity(entry: dict[str, Any], values: dict[str, str] | None = None) -> Identity:
    values = values or {}

    def fill(text: str) -> str:
        for k, v in values.items():
            text = text.replace("{" + k + "}", v)
        return text

    if entry.get("transport") == "http":
        return Identity("http", fill(str(entry.get("url") or "")))
    parts = [str(entry.get("command") or ""), *[fill(str(a)) for a in entry.get("args") or []]]
    return Identity("stdio", " ".join(parts))


def pin_problem(entry: dict[str, Any], values: dict[str, str] | None = None) -> str:
    """Vazio se a versão está fixada. Só importa para stdio: em HTTP, quem responde é o fornecedor."""
    if entry.get("transport") == "http":
        return ""
    command = str(entry.get("command") or "").rsplit("/", 1)[-1].removesuffix(".cmd")
    args = identity(entry, values).target.split()[1:]
    if command == "docker":
        return "" if any(DIGEST.search(a) for a in args) else "imagem sem digest (@sha256:...)"
    if command in RUNNERS:
        packages = [a for a in args if not a.startswith("-")]
        if not packages:
            return f"{command} sem pacote"
        if not VERSIONED.match(packages[0]):
            return f"{packages[0]} sem versão fixa: use pacote@1.2.3"
    return ""


def tool_allowed(allowed: list[str], tool: str) -> bool:
    return tool in allowed
