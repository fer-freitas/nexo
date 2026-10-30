"""Opções, relatório, esquema do classificador e utilitários da execução de tarefa usados por mais de um módulo."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.runtime.resources import load_yaml
from nexo.kernel.workflow.engine import Workflow
from nexo.kernel.workspace import git

UI = Callable[[str, dict[str, Any]], None]


GIT_LOCK = threading.Lock()


CLASSIFIER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["backend", "frontend", "test", "migration", "docs", "infra", "contract"]},
        "size": {"type": "string", "enum": ["S", "M", "L"]},
        "risk_tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["kind", "size"],
    "additionalProperties": False,
}


@dataclass
class RunOptions:
    profile: str | None = None
    tool: str | None = None
    model: str | None = None
    dry_run: bool = False
    simulate_rate_limit: frozenset[str] = frozenset()
    wait: bool = False
    role: str = "developer"
    profile_policy: str = "code-writer"  # perfil de permissão
    base_ref: str | None = None  # branch/commit base (ex.: branch da história)
    locked: frozenset[str] = frozenset()  # arquivos travados para o agente
    exclude_tools: frozenset[str] = frozenset()  # escada de escalonamento
    stop_after_qa: bool = False  # `nexo qa write`: só os testes, bloqueados, sem implementar
    extra_prompt: str = ""  # avaliação de skills: conteúdo da variante injetado no prompt


@dataclass
class RunReport:
    task_id: str
    state: str
    reason: str = ""
    run_id: str | None = None
    commit: str | None = None
    branch: str | None = None
    worktree: str | None = None
    routes: list[str] = field(default_factory=list)
    handoffs: int = 0
    blocked_until: float | None = None
    decision: dict[str, Any] | None = None
    classification: dict[str, Any] | None = None
    usage: list[dict[str, Any]] = field(default_factory=list)
    model_vendors: list[str] = field(default_factory=list)


def _noop(_: str, __: dict[str, Any]) -> None:
    return None


def task_workflow(ctx: AppContext) -> Workflow:
    brain = ctx.brain_dir if ctx.brain_dir.is_dir() else None
    return Workflow.from_mapping(load_yaml("workflows/task.yaml", brain))


def responsible(ctx: AppContext) -> str:
    """Humano responsável pela entrega."""
    from nexo.kernel.platform import identity

    try:
        return identity.current(ctx).id
    except NexoError:
        return f"os:{ctx.actor}"


def configure_git(ctx: AppContext, cfg: dict[str, Any]) -> None:
    """S-09: `git.signing.key` (chave SSH da identidade de serviço) e `git.identity` (nome/e-mail do serviço)."""
    gcfg = dict(cfg.get("git") or {})
    signing = dict(gcfg.get("signing") or {})
    ident = dict(gcfg.get("identity") or {})
    key = str(signing.get("key") or "")
    if key and not Path(key).expanduser().is_file():
        raise NexoError(f"git.signing.key não encontrada: {key}")
    git.configure(
        str(ident.get("name") or ""), str(ident.get("email") or ""), str(Path(key).expanduser()) if key else ""
    )


def _now() -> str:
    from nexo.store.runs import now_iso

    return now_iso()


def _fmt(epoch: float | None) -> str:
    if not epoch:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch))
