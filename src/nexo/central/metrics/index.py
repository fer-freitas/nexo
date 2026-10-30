"""Índice das páginas: montar uma página pelo nome, as opções dos filtros e o drill."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from nexo.central.db import CollectorDB
from nexo.central.metrics.pages.brain import brain
from nexo.central.metrics.pages.executive import executive
from nexo.central.metrics.pages.finops import finops
from nexo.central.metrics.pages.fleet import fleet
from nexo.central.metrics.pages.me import me
from nexo.central.metrics.pages.quality import quality
from nexo.central.metrics.pages.routing import routing
from nexo.central.metrics.pages.squad import squad
from nexo.central.metrics.scope import DONE, DRILL, Scope
from nexo.central.metrics.widgets import page, table
from nexo.central.visibility import Principal


def drill(d: CollectorDB, scope: Scope, source: str, params: dict[str, str], limit: int = 500) -> dict[str, Any]:
    if source not in DRILL:
        raise HTTPException(404, "origem desconhecida")
    table_name, columns, keys = DRILL[source]
    if source == "story":
        where, sql_params = scope.where()
        done = params.get("done")
        if done == "1":
            where += " AND done_at IS NOT NULL AND month BETWEEN ? AND ?"
            sql_params += list(scope.period)
        elif done == "0":
            where += " AND state <> ?"
            sql_params.append(DONE)
    else:
        where, sql_params = scope.where(months=scope.period, steps=source == "step")
    for key, column in keys.items():
        if params.get(key):
            where += f" AND {column} = ?"
            sql_params.append(params[key])
    rows = d.query(
        f"SELECT {', '.join(columns)} FROM {table_name} WHERE {where} ORDER BY {columns[0]}, {columns[1]}"
        f" LIMIT {int(limit)}",
        tuple(sql_params),
    )
    return {"source": source, "columns": list(columns), "rows": [[r[c] for c in columns] for r in rows]}


def build(d: CollectorDB, name: str, scope: Scope, actor: str) -> dict[str, Any]:
    if name == "executive":
        return executive(d, scope)
    if name == "finops":
        return finops(d, scope, actor)
    if name == "squad":
        return squad(d, scope, actor)
    if name == "quality":
        return quality(d, scope, actor)
    if name == "routing":
        return routing(d, scope)
    if name == "brain":
        return brain(d, scope)
    if name == "me":
        return me(d, scope)
    if name == "fleet":
        return fleet(d, scope)
    if name == "board":
        from nexo.central.metrics.pages.board import board

        return board(d, scope)
    if name == "flags":
        from nexo.central.flags import snapshot
        from nexo.kernel.config.flags import CATALOG

        snap = snapshot(d)
        rows = [
            [
                f.key,
                f.title,
                f.area,
                {True: "ligada", False: "desligada"}.get(snap["global"].get(f.key), "padrão"),
                ", ".join(f"{s}={'on' if v[f.key] else 'off'}" for s, v in sorted(snap["squads"].items()) if f.key in v)
                or "—",
            ]
            for f in CATALOG
        ]
        return page(
            "flags",
            "Feature flags",
            scope,
            tables=[table("flags", "Funcionalidades", ["flag", "funcionalidade", "área", "empresa", "squads"], rows)],
        )
    if name == "dora":
        from nexo.central.dora import page as dora_page

        return dora_page(d, scope)
    raise HTTPException(404, "página desconhecida")


def options(d: CollectorDB, principal: Principal) -> dict[str, list[str]]:
    """Valores dos filtros — só o que o perfil pode ver."""
    allowed = principal.squads
    squads = sorted(
        {str(r["squad"]) for r in d.query("SELECT DISTINCT squad FROM fact_step WHERE squad IS NOT NULL")}
        | {str(r["squad"]) for r in d.query("SELECT squad FROM dim_squad")}
    )
    squads = [s for s in squads if s and (allowed is None or s in allowed)]
    directorates = (
        sorted(
            {
                str(r["directorate"])
                for r in d.query("SELECT squad, directorate FROM dim_squad")
                if r["directorate"] and (allowed is None or r["squad"] in allowed)
            }
        )
        if squads
        else []
    )
    return {
        "squad": squads,
        "directorate": directorates,
        "stack": sorted({str(r["stack"]) for r in d.query("SELECT DISTINCT stack FROM fact_step") if r["stack"]}),
        "tool": sorted({str(r["tool"]) for r in d.query("SELECT DISTINCT tool FROM fact_step") if r["tool"]}),
        "model": sorted({str(r["model"]) for r in d.query("SELECT DISTINCT model FROM fact_step") if r["model"]}),
    }


def project_names(d: CollectorDB, principal: Principal | None = None) -> dict[str, str]:
    """Hash do projeto → nome enviado pela CLI, só dos squads que a pessoa vê; sem nome fica o código curto."""
    allowed = principal.squads if principal is not None else None
    return {
        str(r["project"]): str(r["name"])
        for r in d.query("SELECT project, name, squad FROM dim_project")
        if allowed is None or (r["squad"] or "") in allowed
    }
