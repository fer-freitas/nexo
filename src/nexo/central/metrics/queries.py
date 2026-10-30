"""Consultas usadas por mais de uma página: custo do mês e indicadores das histórias."""

from __future__ import annotations

from typing import Any

from nexo.central.db import CollectorDB
from nexo.central.metrics.scope import Scope
from nexo.central.metrics.widgets import _pct, _r


def _cost(d: CollectorDB, scope: Scope, month: str) -> float:
    where, params = scope.where(months=(month, month), steps=True)
    row = d.one(f"SELECT COALESCE(SUM(usage_brl), 0) AS v FROM fact_step WHERE {where}", tuple(params))
    return round(float(row["v"] if row else 0), 2)


def _story_kpis(d: CollectorDB, scope: Scope, months: tuple[str, str], all_squads: bool = False) -> dict[str, Any]:
    if all_squads:
        where, params = "month BETWEEN ? AND ?", [*months]
    else:
        where, params = scope.where(months=months)
    row = d.one(
        "SELECT COUNT(*) AS done, COALESCE(SUM(cost_brl), 0) AS cost, COALESCE(SUM(points), 0) AS pts,"
        " SUM(CASE WHEN human_gate = 0 AND escalated = 0 THEN 1 ELSE 0 END) AS e2e,"
        " SUM(CASE WHEN bugs = 0 THEN 1 ELSE 0 END) AS fpy, AVG(lead_hours) AS lead,"
        " AVG(cycle_hours) AS cycle, AVG(waiting_human_hours) AS waiting"
        f" FROM fact_story WHERE done_at IS NOT NULL AND {where}",
        tuple(params),
    ) or {"done": 0}
    done = int(row["done"] or 0)
    return {
        "done": done,
        "brl_per_point": _r(float(row["cost"]) / float(row["pts"])) if done and row["pts"] else None,
        "e2e": _pct(float(row["e2e"] or 0), done),
        "fpy": _pct(float(row["fpy"] or 0), done),
        "lead_hours": _r(row.get("lead")),
        "cycle_hours": _r(row.get("cycle")),
        "waiting_hours": _r(row.get("waiting")),
    }
