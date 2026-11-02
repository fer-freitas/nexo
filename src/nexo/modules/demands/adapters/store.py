"""Armazenamento das demandas: um arquivo por evento, no repositório do time ou numa pasta local.

Sem conflito de merge entre pessoas, porque ninguém edita arquivo de outro: cada evento é um arquivo novo, com ID
ordenável. Com repositório do time, cada gravação faz commit e push, e refaz sobre a versão nova em caso de disputa;
sem rede, fica na fila local e segue no próximo comando.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexo.kernel.errors import NexoError

PUSH_RETRIES = 5
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


_LAST: list[tuple[int, int]] = []  # (ms, sufixo) do último id deste processo


def new_event_id(now: float | None = None) -> str:
    """Identificador ordenável por tempo (formato ULID), sem dependência externa.

    No mesmo milissegundo, o sufixo cresce em vez de sortear de novo: dois eventos gravados em sequência
    (criar e aprovar, por exemplo) são lidos na mesma ordem em que aconteceram.
    """
    ms = int((time.time() if now is None else now) * 1000)
    suffix = random.getrandbits(79)
    if now is None and _LAST and ms <= _LAST[0][0]:
        ms, suffix = _LAST[0][0], _LAST[0][1] + 1
    if now is None:
        _LAST[:] = [(ms, suffix)]
    head = []
    for _ in range(10):
        ms, rem = divmod(ms, 32)
        head.append(ALPHABET[rem])
    tail = []
    for _ in range(16):
        suffix, rem = divmod(suffix, 32)
        tail.append(ALPHABET[rem])
    return "".join(reversed(head)) + "".join(reversed(tail))


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}  # nunca pede senha em segundo plano
    done = subprocess.run(
        ["git", "-c", "core.longpaths=true", "-c", "core.autocrlf=false", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    if check and done.returncode != 0:
        raise NexoError(f"git {args[0]} falhou: {done.stderr.strip()[:300]}")
    return done


class Store:
    """Pasta com os eventos de um projeto. `shared` indica um clone do repositório do time."""

    def __init__(self, base: Path, project: str, shared: bool = False) -> None:
        self.base = base
        self.project = project
        self.shared = shared
        self.root = base / "projects" / project

    # ----------------------------------------------------------- eventos
    def events_dir(self) -> Path:
        return self.root / "demands" / "events"

    def append(self, type_: str, demand: str, actor: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        now = datetime.now(UTC)
        event = {
            "id": new_event_id(),
            "at": now.isoformat(timespec="seconds"),
            "actor": actor,
            "type": type_,
            "demand": demand,
            "data": data or {},
        }
        folder = self.events_dir() / now.strftime("%Y-%m")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{event['id']}.json"
        path.write_text(json.dumps(event, ensure_ascii=False, sort_keys=True, indent=1) + "\n", "utf-8", newline="\n")
        return event

    def events(self) -> list[dict[str, Any]]:
        folder = self.events_dir()
        if not folder.is_dir():
            return []
        out = []
        for path in sorted(folder.rglob("*.json")):
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue  # arquivo corrompido não derruba o board; `demand verify` aponta
        return out

    # ----------------------------------------------------------- documentos do projeto
    def path(self, *parts: str) -> Path:
        target = self.root.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    # ----------------------------------------------------------- sincronização
    def pull(self) -> bool:
        """Traz o que os outros gravaram. Falha de rede não interrompe: vale o que já está aqui."""
        if not self.shared:
            return True
        if _git(self.base, "pull", "--rebase", "--autostash", "--quiet", check=False).returncode == 0:
            return True
        _git(self.base, "rebase", "--abort", check=False)
        return False

    def push(self, message: str) -> dict[str, Any]:
        """Grava e envia. Disputa no push refaz sobre a versão nova; sem rede, fica pendente na fila local."""
        if not self.shared:
            return {"shared": False, "sent": False, "pending": False}
        _git(self.base, "add", "-A")
        if _git(self.base, "diff", "--cached", "--quiet", check=False).returncode != 0:
            _git(self.base, "commit", "-q", "-m", message)
        ahead = _git(self.base, "rev-list", "--count", "@{u}..HEAD", check=False)
        if ahead.returncode == 0 and ahead.stdout.strip() == "0":
            return {"shared": True, "sent": True, "pending": False}
        for _ in range(PUSH_RETRIES):
            if _git(self.base, "push", "--quiet", check=False).returncode == 0:
                return {"shared": True, "sent": True, "pending": False}
            if not self.pull():
                break
        return {"shared": True, "sent": False, "pending": True}

    def pending(self) -> int:
        if not self.shared:
            return 0
        ahead = _git(self.base, "rev-list", "--count", "@{u}..HEAD", check=False)
        return int(ahead.stdout.strip() or 0) if ahead.returncode == 0 else 0
