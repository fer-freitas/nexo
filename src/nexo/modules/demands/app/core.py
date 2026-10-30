"""Acesso comum às demandas: abrir o armazenamento, gravar eventos, projetar e levar o estado à execução local."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nexo.kernel import team as team_mod
from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError, PolicyViolation
from nexo.kernel.platform import identity
from nexo.kernel.security.redaction import redact
from nexo.kernel.workspace.specs import project_settings
from nexo.modules.demands.adapters.store import Store
from nexo.modules.demands.domain import model as m


def root_of(ctx: AppContext) -> Path:
    if ctx.project_root is None:
        raise NexoError("nenhum projeto Nexo aqui; rode `nexo init` no repositório ou `nexo join <time>`")
    return ctx.project_root


def store(ctx: AppContext) -> Store:
    cached = ctx.__dict__.get("_demand_store")
    if cached is None:
        settings = project_settings(root_of(ctx))
        cached = Store(settings.team_dir(), settings.project, settings.shared)
        ctx.__dict__["_demand_store"] = cached
    return cached


def who(ctx: AppContext) -> str:
    return identity.current(ctx).id


def team(ctx: AppContext) -> team_mod.Team:
    return team_mod.load(ctx.project_root)


def load(ctx: AppContext, sync: bool = True) -> dict[str, m.Demand]:
    s = store(ctx)
    if sync and not ctx.__dict__.get("_demands_pulled"):
        s.pull()
        ctx.__dict__["_demands_pulled"] = True
    demands = m.project(s.events())
    if s.shared:
        _hydrate(ctx, demands)
    return demands


def _hydrate(ctx: AppContext, demands: dict[str, m.Demand]) -> None:
    """A execução guarda o estado no banco da máquina; o que outra pessoa fez chega por aqui."""
    from nexo.store.runs import RunStore, project_key

    runs = RunStore(ctx.conn, project_key(root_of(ctx)))
    kinds = {"feature": "feature", "story": "story", "task": "task", "bug": "bug"}
    for d in demands.values():
        if d.type in kinds and d.state and runs.item_state(d.id) != d.state:
            runs.upsert_item(d.id, kinds[d.type], d.title, d.project, "local", d.state)
            runs.set_item_state(d.id, d.state)


def get(ctx: AppContext, demand_id: str, sync: bool = True) -> tuple[m.Demand, dict[str, m.Demand]]:
    demands = load(ctx, sync)
    local = demand_id.split(":", 1)[-1]
    if local not in demands:
        raise NexoError(f"demanda desconhecida: {demand_id} (veja `nexo board`)")
    return demands[local], demands


def record(ctx: AppContext, type_: str, demand: str, data: dict[str, Any] | None = None, push: bool = True) -> None:
    s = store(ctx)
    s.append(type_, demand, _actor(ctx), data)
    _publish(ctx, type_, demand)
    if push:
        s.push(f"nexo: {type_} {demand}")


def _publish(ctx: AppContext, type_: str, demand: str) -> None:
    """Só o que o painel precisa, pela lista de permissão. O título é opcional e passa pela redação."""
    from nexo.kernel.platform import config, telemetry

    if not telemetry.enabled(ctx):
        return
    demands = m.project(store(ctx).events())
    d = demands.get(demand)
    if d is None:
        return
    payload: dict[str, Any] = {
        "demand": d.id,
        "demand_type": d.type,
        "stage": m.stage_of(d, demands),
        "origin": str(d.origin.get("kind", "")),
        "parent": d.parent,
        "holders": ",".join(sorted({h["role"] for h in m.holders_of(d, demands)})),
        "blocked": len(d.holding_proposals),
    }
    cfg = dict(config.resolved(ctx).values.get("telemetry") or {})
    sensitive = str(config.resolved(ctx).values.get("data_class", "internal")) in ("confidential", "restricted")
    if cfg.get("demand_titles", not sensitive) and d.title:
        payload["title"] = clean(d.title).replace("/", " ").replace("\\", " ")[:120]
    ctx.audit("demand.created" if type_ == "demand.created" else "demand.transitioned", payload, ref=d.id)


def _actor(ctx: AppContext) -> str:
    try:
        return who(ctx)
    except NexoError:  # eventos automáticos em máquina sem identidade: não param o fluxo
        return ctx.actor


def clean(text: str) -> str:
    """Tudo que vai para o repositório do time passa pela redação."""
    return redact(text)


def require_role(ctx: AppContext, role: str, action: str) -> str:
    """Só quem tem o papel age. Sem `team.yaml`, vale o papel de quem usa a ferramenta sozinho (modo solo implícito)."""
    actor = who(ctx)
    t = team(ctx)
    if t.declared and role not in t.roles_of(actor):
        raise PolicyViolation(f"{action} exige o papel {m.ROLE_LABEL.get(role, role)}; {actor} não o tem em team.yaml")
    return actor


def holder_inbox(ctx: AppContext, role: str | None = None, only_mine: bool = True) -> list[dict[str, Any]]:
    """O que está comigo, do que está parado há mais tempo para o mais recente."""
    demands = load(ctx)
    me = _actor(ctx)
    t = team(ctx)
    my_roles = set(t.roles_of(me)) if t.declared else set(m.ROLE_LABEL)
    rows = []
    for d in demands.values():
        for h in m.holders_of(d, demands):
            if role and h["role"] != role:
                continue
            mine = h["who"] == me or (not h["who"] and h["role"] in my_roles)
            if only_mine and not mine:
                continue
            rows.append({"demand": d.id, "title": d.title, "stage": m.stage_of(d, demands), **h})
    return sorted(rows, key=lambda r: str(r["since"]))
