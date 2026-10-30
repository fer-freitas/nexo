"""Documentos da feature e atualização as-built."""

from __future__ import annotations

from pathlib import Path

from nexo.kernel.context import AppContext
from nexo.kernel.spec.model import Spec


def target_kind(ctx: AppContext) -> str:
    """Os documentos ficam no projeto; a publicação externa saiu do escopo."""
    return "none"


def publish(ctx: AppContext, folder: Path, spec: Spec, root: Path | None = None) -> list[str]:
    return []


def as_built(ctx: AppContext, root: Path, story_id: str) -> bool:
    """Quando todas as histórias da feature estão concluídas, regrava os documentos como as-built e publica."""
    from nexo.kernel.workspace import specs
    from nexo.modules.planning.app import templates
    from nexo.modules.planning.app.clarify import feature_for_story
    from nexo.store.runs import RunStore, project_key

    folder = feature_for_story(root, story_id)
    backlog = specs.load_backlog(folder) if folder else None
    if folder is None or backlog is None:
        return False
    store = RunStore(ctx.conn, project_key(root))
    states = {s.id: store.item_state(s.id) or "Pronto" for s in backlog.stories}
    if any(v != "Concluido" for v in states.values()):
        return False
    states[backlog.feature] = "Concluido"
    spec = specs.load_spec(folder)
    qs = specs.load_questions(folder)
    templates.write_feature_docs(ctx, folder, spec, qs, 100, backlog, states, as_built=True, root=root)
    ctx.audit("docs.as_built", {"feature": spec.id}, ref=spec.id)
    publish(ctx, folder, spec, root)
    return True
