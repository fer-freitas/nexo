"""Página do cérebro: versão em uso e adoção das skills."""

from __future__ import annotations

from typing import Any

from nexo.central.db import CollectorDB
from nexo.central.metrics.scope import Scope
from nexo.central.metrics.widgets import _pct, _r, kpi, page, table


def brain(d: CollectorDB, scope: Scope) -> dict[str, Any]:
    where, params = scope.where(months=scope.period, steps=True)
    rows = d.query(
        "SELECT COALESCE(brain_version, '') AS v, COUNT(*) AS n, COUNT(DISTINCT dev_pid) AS devs,"
        " SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) AS ok, COALESCE(SUM(usage_brl), 0) AS brl,"
        f" MIN(started_at) AS first FROM fact_step WHERE {where} GROUP BY COALESCE(brain_version, '')"
        " ORDER BY MIN(started_at)",
        tuple(params),
    )
    latest = rows[-1]["v"] if rows else ""
    last_by_dev: dict[str, str] = {}
    for r in d.query(f"SELECT dev_pid, brain_version FROM fact_step WHERE {where} ORDER BY started_at", tuple(params)):
        last_by_dev[str(r["dev_pid"])] = str(r["brain_version"] or "")
    on_latest = sum(1 for v in last_by_dev.values() if v == latest)
    return page(
        "brain",
        "Conhecimento",
        scope,
        kpis=[
            kpi("versions", "Versões em uso no período", len(rows), "int"),
            kpi("latest", "Versão mais recente", latest or "—", "text"),
            kpi("adoption", "% de devs na versão mais recente", _pct(on_latest, len(last_by_dev)), "pct"),
        ],
        tables=[
            table(
                "versions",
                "Efeito de cada versão do cérebro (mesmo período e filtros)",
                ["versão", "primeiro uso", "passos", "devs", "sucesso %", "R$/passo"],
                [
                    [
                        r["v"] or "—",
                        str(r["first"] or "")[:10],
                        int(r["n"]),
                        int(r["devs"]),
                        _pct(int(r["ok"]), int(r["n"])),
                        _r(float(r["brl"]) / int(r["n"]), 4),
                    ]
                    for r in rows
                ],
            )
        ],
        notes=["lições propostas × aprovadas e issues abertas: ver os PRs e issues do nexo-brain"],
    )
