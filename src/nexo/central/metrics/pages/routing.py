"""Página de roteamento: rotas, ferramentas e modelos usados e o resultado de cada uma."""

from __future__ import annotations

from typing import Any

from nexo.central.db import CollectorDB
from nexo.central.metrics.scope import Scope
from nexo.central.metrics.widgets import _drill, _pct, _r, kpi, page, table


def routing(d: CollectorDB, scope: Scope) -> dict[str, Any]:
    where, params = scope.where("s", months=scope.period, steps=True)
    rows = d.query(
        "SELECT s.tool, s.model, COALESCE(t.kind, '-') AS kind, COALESCE(s.stack, '') AS stack, COUNT(*) AS n,"
        " SUM(CASE WHEN s.outcome = 'success' THEN 1 ELSE 0 END) AS ok, COALESCE(SUM(s.usage_brl), 0) AS brl,"
        " AVG(s.seconds) AS secs FROM fact_step s LEFT JOIN fact_task t ON t.project = s.project AND t.task = s.target"
        f" WHERE {where} GROUP BY s.tool, s.model, COALESCE(t.kind, '-'), COALESCE(s.stack, '')"
        " ORDER BY 1, 2, 3, 4",
        tuple(params),
    )
    ewhere, eparams = scope.where(months=scope.period)
    handoffs = d.query(
        "SELECT COALESCE(tool, '-') AS tool, COALESCE(kind, '-') AS kind, COUNT(*) AS n FROM fact_event"
        f" WHERE type = 'run.handoff' AND {ewhere} GROUP BY COALESCE(tool, '-'), COALESCE(kind, '-') ORDER BY 1, 2",
        tuple(eparams),
    )
    quota = d.one(
        f"SELECT COUNT(*) AS n FROM fact_event WHERE type = 'run.blocked' AND kind = 'quota' AND {ewhere}",
        tuple(eparams),
    )
    total = sum(int(r["n"]) for r in rows)
    ok = sum(int(r["ok"]) for r in rows)
    return page(
        "routing",
        "Ferramentas e modelos",
        scope,
        kpis=[
            kpi("steps", "Passos de agente", total, "int", _drill("step")),
            kpi("success", "% de passos com sucesso", _pct(ok, total), "pct"),
            kpi("handoffs", "Handoffs", sum(int(r["n"]) for r in handoffs), "int", _drill("event", type="run.handoff")),
            kpi(
                "quota_blocks",
                "Bloqueios por cota",
                int(quota["n"] if quota else 0),
                "int",
                _drill("event", type="run.blocked", kind="quota"),
            ),
        ],
        tables=[
            table(
                "routes",
                "Resultado e custo por rota × tipo × stack",
                ["ferramenta", "modelo", "tipo", "stack", "passos", "sucesso %", "custo R$", "R$/passo", "s/passo"],
                [
                    [
                        r["tool"],
                        r["model"],
                        r["kind"],
                        r["stack"],
                        int(r["n"]),
                        _pct(int(r["ok"]), int(r["n"])),
                        _r(r["brl"]),
                        _r(float(r["brl"]) / int(r["n"]), 4),
                        _r(r["secs"], 1),
                    ]
                    for r in rows
                ],
            ),
            table(
                "handoffs",
                "Handoffs por ferramenta e motivo",
                ["ferramenta", "motivo", "handoffs"],
                [[r["tool"], r["kind"], int(r["n"])] for r in handoffs],
            ),
        ],
        notes=["resultados de `nexo eval` e propostas do roteador ficam no nexo-brain (PRs de roteamento)"],
    )
