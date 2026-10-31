"""Página de demandas: o que está aberto em todos os times, com quem está e o que está parado."""

from __future__ import annotations

from typing import Any

from nexo.central.db import CollectorDB
from nexo.central.metrics.scope import Scope
from nexo.central.metrics.widgets import kpi, page, table

CLOSED = ("done", "cancelled")


def board(d: CollectorDB, scope: Scope) -> dict[str, Any]:
    rows = [
        r
        for r in d.query("SELECT * FROM fact_demand ORDER BY project, demand")
        if scope.principal.sees_squad(str(r["squad"] or ""))
        and scope.has_squad(str(r["squad"] or ""))
        and scope.has_project(str(r["project"] or ""))
        and str(r["stage"]) not in CLOSED
    ]
    by_stage: dict[str, int] = {}
    for r in rows:
        by_stage[str(r["stage"])] = by_stage.get(str(r["stage"]), 0) + 1
    return page(
        "board",
        "Demandas",
        scope,
        kpis=[
            kpi("open", "Abertas", len(rows), "int"),
            kpi("blocked", "Com decisão pendente", sum(1 for r in rows if int(r["blocked"] or 0)), "int"),
            kpi("triage", "Em triagem", by_stage.get("triage", 0), "int"),
        ],
        tables=[
            table(
                "demands",
                "Demandas abertas",
                ["projeto", "demanda", "tipo", "título", "etapa", "com", "origem", "decisões"],
                [
                    [
                        r["project"],
                        r["demand"],
                        r["demand_type"] or "",
                        r["title"] or "",
                        r["stage"] or "",
                        r["holders"] or "",
                        r["origin"] or "",
                        r["blocked"] or 0,
                    ]
                    for r in rows
                ],
            ),
            table("stages", "Por etapa", ["etapa", "demandas"], [[k, v] for k, v in sorted(by_stage.items())]),
        ],
    )
