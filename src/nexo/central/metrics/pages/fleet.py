"""Página da frota: versões do Nexo e do cérebro nas máquinas."""

from __future__ import annotations

import json
from typing import Any

from nexo.central.db import CollectorDB
from nexo.central.metrics.scope import Scope
from nexo.central.metrics.widgets import _pct, kpi, page, table


def _vtuple(value: str) -> tuple[int, ...]:
    import re

    return tuple(int(x) for x in re.findall(r"\d+", value or "")) or (0,)


def fleet(d: CollectorDB, scope: Scope) -> dict[str, Any]:
    """Frota: versões de Nexo, cérebro e CLIs por dispositivo, deriva e pendências — sem nomes."""
    now = scope.now.timestamp()
    rows = [
        r
        for r in d.query("SELECT * FROM fleet ORDER BY device_id")
        if scope.principal.sees_squad(r["squad"]) and scope.has_squad(r["squad"])
    ]
    lags = {
        str(r["device_id"]): float(r["lag_hours"] or 0) for r in d.query("SELECT device_id, lag_hours FROM device_lag")
    }
    active = [r for r in rows if now - float(r["last_seen"] or 0) <= 30 * 86400]
    latest_nexo = max((str(r["nexo"]) for r in active), key=_vtuple, default="")
    latest_brain = max((str(r["brain"]) for r in active if r["brain"]), key=_vtuple, default="")

    def count(key: str) -> list[list[Any]]:
        out: dict[str, int] = {}
        for r in active:
            out[str(r[key] or "—")] = out.get(str(r[key] or "—"), 0) + 1
        return [[k, v] for k, v in sorted(out.items(), key=lambda kv: _vtuple(kv[0]), reverse=True)]

    agents: dict[tuple[str, str], int] = {}
    for r in active:
        for tool, version in dict(json.loads(r["agents"] or "{}")).items():
            agents[(tool, str(version))] = agents.get((tool, str(version)), 0) + 1
    return page(
        "fleet",
        "Frota de agentes",
        scope,
        kpis=[
            kpi("devices", "Dispositivos ativos (30 dias)", len(active), "int"),
            kpi("latest_nexo", "Nexo mais recente", latest_nexo or "—", "text"),
            kpi(
                "on_latest_nexo",
                "% no Nexo mais recente",
                _pct(sum(1 for r in active if r["nexo"] == latest_nexo), len(active)),
                "pct",
            ),
            kpi(
                "on_latest_brain",
                "% no cérebro mais recente",
                _pct(sum(1 for r in active if r["brain"] == latest_brain), len(active)),
                "pct",
            ),
            kpi(
                "lagging",
                "Com telemetria atrasada (> 72 h)",
                sum(1 for r in active if lags.get(str(r["device_id"]), 0) > 72),
                "int",
            ),
            kpi(
                "stale",
                "Sem contato há mais de 7 dias",
                sum(1 for r in active if now - float(r["last_seen"] or 0) > 7 * 86400),
                "int",
            ),
        ],
        tables=[
            table("nexo_versions", "Versões do Nexo", ["versão", "dispositivos"], count("nexo")),
            table("brain_versions", "Versões do cérebro", ["versão", "dispositivos"], count("brain")),
            table(
                "agent_versions",
                "Versões das CLIs dos agentes",
                ["ferramenta", "versão", "dispositivos"],
                [[t, v, n] for (t, v), n in sorted(agents.items())],
            ),
            table(
                "devices",
                "Dispositivos (deriva e pendências)",
                ["dispositivo", "squad", "Nexo", "cérebro", "SO", "último contato (dias)", "atraso (h)"],
                [
                    [
                        str(r["device_id"])[:8],
                        r["squad"] or "—",
                        r["nexo"],
                        r["brain"] or "—",
                        r["os"],
                        round((now - float(r["last_seen"] or 0)) / 86400, 1),
                        lags.get(str(r["device_id"]), 0.0),
                    ]
                    for r in active
                    if r["nexo"] != latest_nexo
                    or (latest_brain and r["brain"] != latest_brain)
                    or lags.get(str(r["device_id"]), 0) > 72
                ],
            ),
        ],
    )
