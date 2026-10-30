"""Preparo do ambiente no worktree do agente.

O `uv run` criava o ambiente Python sozinho em qualquer pasta; com o pip, o `.venv` de cada worktree precisa ser
criado e instalado antes de o agente rodar os testes. Usa as etapas `setup: true` da receita da stack (as mesmas do
pipeline); falha não interrompe: o pipeline mostra o erro de ambiente depois.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nexo.kernel.context import AppContext


def prepare_worktree(ctx: AppContext, cfg: dict[str, Any], worktree: Path, brain_dir: Path) -> None:
    import os

    from nexo.modules.quality.adapters.runner import prepare_env

    stacks = [str(s) for s in cfg.get("stacks") or []]
    enabled = (cfg.get("quality") or {}).get("prepare_worktree", True)
    if not stacks or enabled is False or os.environ.get("NX_PREPARE_WORKTREE") == "0":
        return
    out = worktree.parent.parent / "runs" / "_preparo" / worktree.name
    try:
        results = prepare_env(stacks[0], worktree, out, brain_dir if brain_dir.is_dir() else None)
    except Exception as exc:  # preparo é ajuda ao agente; o pipeline confere o ambiente de verdade
        ctx.audit("worktree.prepare", {"worktree": worktree.name, "ok": False, "error": str(exc)[:200]})
        return
    if results:
        ok = all(r.status in ("passed", "skipped") for r in results)
        ctx.audit("worktree.prepare", {"worktree": worktree.name, "ok": ok, "stages": [r.id for r in results]})
