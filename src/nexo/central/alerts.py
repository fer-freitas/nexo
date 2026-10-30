"""Alertas em tempo real na ingestão: custo de um passo fora do padrão e loop de ferramentas.

Diferente dos insights (diários, com amostra), dispara no momento em que o fato chega: passo com custo acima de
N × a média do mesmo papel e ferramenta nos últimos 30 dias (amostra mínima), ou com chamadas de ferramenta acima do
limite. Vira insight `RT-01`/`RT-02` (escopo org, dono time do Nexo) e cartão imediato no canal `nexo` do Teams.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import Any

from nexo.central.db import CollectorDB
from nexo.kernel.finops.insights import Detector
from nexo.kernel.finops.money import brl

COST = Detector(
    "RT-01",
    "Custo de passo fora do padrão",
    "realtime",
    severity="alta",
    owner="time do Nexo",
    action="Verificar o passo no drill-down (loop, contexto grande, rota errada) e ajustar limites",
)
LOOP = Detector(
    "RT-02",
    "Loop de ferramentas",
    "realtime",
    severity="alta",
    owner="time do Nexo",
    action="Rever o prompt/skill do papel e o limite de turnos da rota",
)


def check_step(d: CollectorDB, fact: dict[str, Any]) -> list[str]:
    factor = float(os.environ.get("NX_COLLECTOR_RT_COST_FACTOR", "5"))
    min_brl = float(os.environ.get("NX_COLLECTOR_RT_MIN_BRL", "5"))
    max_calls = int(os.environ.get("NX_COLLECTOR_RT_MAX_TOOL_CALLS", "150"))
    fired: list[str] = []
    cost = float(fact.get("usage_brl") or 0)
    since = datetime.fromtimestamp(time.time() - 30 * 86400, UTC).isoformat()
    if cost >= min_brl:
        row = d.one(
            "SELECT COUNT(*) AS n, AVG(usage_brl) AS avg FROM fact_step WHERE role = ? AND tool = ?"
            " AND started_at >= ? AND event_id <> ?",
            (fact.get("role"), fact.get("tool"), since, fact.get("event_id")),
        )
        n, avg = int(row["n"] if row else 0), float((row or {}).get("avg") or 0)
        if n >= 20 and avg > 0 and cost > factor * avg:
            fired.append(
                _open(
                    d,
                    COST,
                    fact,
                    {"cost_brl": round(cost, 2), "avg_brl": round(avg, 4), "n": n, "factor": factor},
                    f"passo {str(fact.get('step'))[:8]} custou {brl(cost)} ({cost / avg:.0f}× a média "
                    f"de {fact.get('role')}/{fact.get('tool')})",
                )
            )
    calls = int(fact.get("tool_calls") or 0)
    if calls > max_calls:
        fired.append(
            _open(
                d,
                LOOP,
                fact,
                {"tool_calls": calls, "limit": max_calls},
                f"passo {str(fact.get('step'))[:8]} fez {calls} chamadas de ferramenta",
            )
        )
    return fired


def _open(d: CollectorDB, det: Detector, fact: dict[str, Any], evidence: dict[str, Any], detail: str) -> str:
    from nexo.central import distribution
    from nexo.central.insights.engine import _upsert

    finding = {
        "scope_kind": "org",
        "scope": f"step:{fact.get('step')}",
        "period": "",
        "detail": detail,
        "evidence": {
            **evidence,
            "run": fact.get("run"),
            "step": fact.get("step"),
            "role": fact.get("role"),
            "tool": fact.get("tool"),
            "model": fact.get("model"),
        },
        "squad": None,
        "dev_pid": None,
        "purpose": None,
    }
    status = _upsert(d, det, finding, datetime.now(UTC))
    url = distribution._webhooks().get("nexo")
    if status == "opened" and url:
        card = distribution.card(
            f"Nexo — alerta: {det.title}", [(k, str(v)) for k, v in evidence.items()], [detail, det.action], None
        )
        distribution._deliver(d, "teams", "realtime", "nexo", url, card)
    return status
