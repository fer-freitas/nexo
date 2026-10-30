"""Dados da apresentação inicial — somente fontes locais e rápidas (< 1 s)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from nexo import __version__
from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.runtime.system import which
from nexo.modules.setup.app.project import project_summary

AGENT_TOOLS = ("claude", "copilot", "devin")


@dataclass
class BannerInfo:
    version: str
    brain_version: str | None
    project: dict[str, object] | None
    agents: dict[str, bool] = field(default_factory=dict)
    budget: dict[str, object] = field(default_factory=dict)
    running: dict[str, int] = field(default_factory=dict)


def brain_version(brain: Path) -> str | None:
    from nexo.kernel.brain import cached_status

    cached = (cached_status(brain) or {}).get("version")  # a versão assinada (ex.: 2026.10.07-2) vem primeiro
    if cached:
        return str(cached)
    version_file = brain / "VERSION"
    if version_file.is_file():
        return version_file.read_text(encoding="utf-8").strip() or None
    head = brain / ".git" / "HEAD"
    if head.is_file():
        ref = head.read_text(encoding="utf-8").strip()
        if ref.startswith("ref:"):
            ref_file = brain / ".git" / ref.split(" ", 1)[1]
            if ref_file.is_file():
                return ref_file.read_text(encoding="utf-8").strip()[:7]
            return None
        return ref[:7]
    return None


ACTIVE_STATES = ("EmDev", "EmQA", "EmReview", "AguardandoHumano", "Bloqueado", "Escalado")


def _budget(ctx: AppContext) -> dict[str, object]:
    from nexo.modules.finops.app import costs

    try:
        status = costs.budget_status(ctx)
    except (NexoError, ValueError, sqlite3.Error):  # o banner nunca falha por causa do orçamento
        return {}
    return {"spent_brl": status.spent_brl, "limit_brl": status.limit_brl, "ratio": status.ratio, "units": status.units}


def _running(ctx: AppContext) -> dict[str, int]:
    if ctx.project_root is None:
        return {}
    from nexo.store.runs import project_key

    rows = ctx.conn.execute(
        "SELECT state, COUNT(*) AS n FROM work_items WHERE project = ? AND state IN (?, ?, ?, ?, ?, ?) GROUP BY state",
        (project_key(ctx.project_root), *ACTIVE_STATES),
    ).fetchall()
    return {str(r["state"]): int(r["n"]) for r in rows}


def collect(ctx: AppContext) -> BannerInfo:
    return BannerInfo(
        version=__version__,
        brain_version=brain_version(ctx.brain_dir),
        project=project_summary(ctx),
        agents={tool: which(tool) is not None for tool in AGENT_TOOLS},
        budget=_budget(ctx),
        running=_running(ctx),
    )
