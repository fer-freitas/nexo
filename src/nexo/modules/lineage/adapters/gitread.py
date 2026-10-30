"""Lê `origin/main` direto dos objetos do Git, sem checkout: a pasta de trabalho da pessoa não é tocada."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from nexo.kernel.errors import NexoError

RELEVANT = (".tf", ".cs", ".csproj", ".json", ".yaml", ".yml")
MAX_BYTES = 2_000_000


def _git(root: Path, *args: str, check: bool = True, timeout: float = 120) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}  # nunca pede senha em segundo plano
    done = subprocess.run(
        ["git", "-c", "core.longpaths=true", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=timeout,
        check=False,
    )
    if check and done.returncode != 0:
        raise NexoError(f"git {args[0]} falhou: {done.stderr.strip()[:200]}")
    return done


def default_ref(root: Path) -> str:
    done = _git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False)
    if done.returncode == 0 and done.stdout.strip():
        return done.stdout.strip()
    for ref in ("origin/main", "origin/master"):
        if _git(root, "rev-parse", "--verify", "-q", ref, check=False).returncode == 0:
            return ref
    raise NexoError(f"{root}: sem origin/main")


def remote_head(root: Path) -> str:
    """Compara sem baixar nada: se a `main` não mudou, termina sem custo."""
    done = _git(root, "ls-remote", "origin", "HEAD", check=False, timeout=30)
    return done.stdout.split()[0] if done.returncode == 0 and done.stdout.strip() else ""


def fetch(root: Path) -> None:
    _git(root, "fetch", "--quiet", "origin")


def head_of(root: Path, ref: str) -> str:
    return _git(root, "rev-parse", ref).stdout.strip()


def tree(root: Path, ref: str) -> dict[str, str]:
    """Arquivos relevantes do commit. Sem conversão de fim de linha: lê o objeto como está."""
    listed = _git(root, "ls-tree", "-r", "-l", "--full-tree", ref).stdout.splitlines()
    out: dict[str, str] = {}
    for line in listed:
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if not path.endswith(RELEVANT) or (parts[3].isdigit() and int(parts[3]) > MAX_BYTES):
            continue
        blob = subprocess.run(["git", "cat-file", "blob", parts[2]], cwd=root, capture_output=True, check=False)
        if blob.returncode == 0:
            out[path] = blob.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n")
    return out


def is_ancestor(root: Path, older: str, newer: str) -> bool:
    return _git(root, "merge-base", "--is-ancestor", older, newer, check=False).returncode == 0


def remote_url(root: Path) -> str:
    return _git(root, "remote", "get-url", "origin", check=False).stdout.strip()
