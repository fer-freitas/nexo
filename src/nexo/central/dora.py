"""DORA, ROI e incidentes.

- Implantações chegam do pipeline (`POST /v2/deployments`, exemplo em deploy/ci/dora-deploy.yml) com os commits
  entregues e a marca de quais vieram do Nexo (trailer `Nexo-Run`).
- Incidentes chegam do ServiceNow (`POST /v2/incidents`) com o commit causador quando conhecido; o coletor liga o
  incidente à implantação e sabe se a mudança foi do Nexo.
- ROI: pontos entregues pelo Nexo × horas por ponto (linha de base ou parâmetro) × custo-hora − custo do Nexo.
  As premissas aparecem junto do número.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from nexo.central.db import CollectorDB
from nexo.central.metrics import Scope, kpi, table
from nexo.central.metrics import page as build_page
from nexo.central.metrics.widgets import _drill, _pct, _r
from nexo.kernel.finops.insights import percentile

PRODUCTION = ("production", "prod", "producao", "produção")


def _ts(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _hours(a: Any, b: Any) -> float | None:
    start, end = _ts(a), _ts(b)
    if start is None or end is None or end < start:
        return None
    return (end - start).total_seconds() / 3600


def record_deployment(d: CollectorDB, body: dict[str, Any]) -> dict[str, Any]:
    commits = [dict(c) for c in body.get("commits") or []]
    authored = [c.get("authored_at") for c in commits if c.get("authored_at")]
    first = min(authored, key=lambda v: _ts(v) or datetime.max) if authored else None
    lead = _hours(first, body["finished_at"]) if first else None
    row = {
        "id": str(body["id"]),
        "service": str(body["service"]),
        "env": str(body.get("env") or "production").lower(),
        "sha": str(body.get("sha") or ""),
        "status": str(body.get("status") or "success"),
        "started_at": body.get("started_at"),
        "finished_at": str(body["finished_at"]),
        "squad": str(body.get("squad") or ""),
        "commits": json.dumps([{"sha": str(c.get("sha", ""))[:40], "nexo": bool(c.get("nexo"))} for c in commits]),
        "nexo_commits": sum(1 for c in commits if c.get("nexo")),
        "lead_time_hours": round(lead, 2) if lead is not None else None,
        "month": str(body["finished_at"])[:7],
    }
    d.execute("DELETE FROM deployments WHERE id = ?", (row["id"],))
    d.execute(  # nosemgrep -- colunas fixas
        f"INSERT INTO deployments ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values())
    )
    return row


def record_incident(d: CollectorDB, body: dict[str, Any]) -> dict[str, Any]:
    cause = str(body.get("cause_sha") or "")
    deployment = None
    nexo_caused = 0
    if cause:
        for dep in d.query(
            "SELECT id, commits FROM deployments WHERE service = ? ORDER BY finished_at DESC", (str(body["service"]),)
        ):
            match = next((c for c in json.loads(dep["commits"] or "[]") if str(c["sha"]).startswith(cause[:12])), None)
            if match is not None:
                deployment, nexo_caused = str(dep["id"]), int(bool(match["nexo"]))
                break
    row = {
        "id": str(body["id"]),
        "service": str(body["service"]),
        "opened_at": str(body["opened_at"]),
        "resolved_at": body.get("resolved_at"),
        "severity": str(body.get("severity") or ""),
        "cause_sha": cause[:40] or None,
        "cause_deployment": deployment,
        "nexo_caused": nexo_caused,
        "squad": str(body.get("squad") or ""),
        "month": str(body["opened_at"])[:7],
    }
    d.execute("DELETE FROM incidents WHERE id = ?", (row["id"],))
    d.execute(  # nosemgrep -- colunas fixas
        f"INSERT INTO incidents ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values())
    )
    if nexo_caused and row["squad"]:  # incidente causado por mudança do Nexo rebaixa a autonomia do squad
        from nexo.central import autonomy

        squad = str(row["squad"])
        autonomy.auto_demote(
            d, squad, autonomy.areas_of(d, squad), f"incidente {row['id']} causado por mudança do Nexo"
        )
    return row


def _in_scope(scope: Scope, squad: str | None) -> bool:
    return scope.squads is None or (squad or "") in scope.squads


def page(d: CollectorDB, scope: Scope) -> dict[str, Any]:
    months = scope.period
    deps = [
        r
        for r in d.query("SELECT * FROM deployments WHERE month BETWEEN ? AND ?", months)
        if r["env"] in PRODUCTION and _in_scope(scope, r["squad"])
    ]
    incs = [
        r
        for r in d.query("SELECT * FROM incidents WHERE month BETWEEN ? AND ?", months)
        if _in_scope(scope, r["squad"])
    ]
    start, end = _ts(f"{months[0]}-01T00:00:00+00:00"), scope.now
    weeks = max(1.0, ((end - start).days if start else 30) / 7)
    ok = [r for r in deps if r["status"] == "success"]
    leads = [float(r["lead_time_hours"]) for r in ok if r["lead_time_hours"] is not None]
    failed = {r["id"] for r in deps if r["status"] != "success"} | {
        r["cause_deployment"] for r in incs if r["cause_deployment"]
    }
    mttr = [h for h in (_hours(r["opened_at"], r["resolved_at"]) for r in incs if r["resolved_at"]) if h is not None]
    by_service: dict[str, list[dict[str, Any]]] = {}
    for r in deps:
        by_service.setdefault(str(r["service"]), []).append(r)
    service_rows = []
    for service, rows in sorted(by_service.items()):
        ok_s = [r for r in rows if r["status"] == "success"]
        lead_s = [float(r["lead_time_hours"]) for r in ok_s if r["lead_time_hours"] is not None]
        failed_s = sum(1 for r in rows if r["id"] in failed)
        service_rows.append(
            [
                service,
                len(ok_s),
                round(len(ok_s) / weeks, 2),
                _r(percentile(lead_s, 50)) if lead_s else None,
                _pct(failed_s, len(rows)),
                sum(int(r["nexo_commits"] or 0) for r in rows),
            ]
        )
    roi = _roi(d, scope)
    nexo_incidents = [r for r in incs if r["nexo_caused"]]
    return build_page(
        "dora",
        "DORA e ROI",
        scope,
        kpis=[
            kpi("deploy_freq", "Implantações em produção por semana", round(len(ok) / weeks, 2), "num"),
            kpi(
                "lead_time_change", "Lead time de mudança p50 (h)", _r(percentile(leads, 50)) if leads else None, "num"
            ),
            kpi(
                "change_failure",
                "Taxa de falha de mudança",
                _pct(len(failed & {r["id"] for r in deps}), len(deps)),
                "pct",
            ),
            kpi("mttr", "Tempo de restauração p50 (h)", _r(percentile(mttr, 50)) if mttr else None, "num"),
            kpi("incidents", "Incidentes no período", len(incs), "int"),
            kpi(
                "nexo_incidents",
                "Incidentes causados por mudanças do Nexo",
                len(nexo_incidents),
                "int",
                _drill("story", done="1"),
            ),
            kpi("roi_brl", "ROI estimado no período", roi["roi_brl"], "brl"),
            kpi("hours_saved", "Horas estimadas economizadas", roi["hours_saved"], "num"),
        ],
        tables=[
            table(
                "services",
                "DORA por serviço (produção)",
                [
                    "serviço",
                    "implantações",
                    "por semana",
                    "lead time p50 (h)",
                    "falha de mudança %",
                    "commits do Nexo",
                ],
                service_rows,
            ),
            table("roi", "Premissas do ROI", ["premissa", "valor"], roi["assumptions"]),
            table(
                "incidents",
                "Incidentes ligados a implantações",
                ["incidente", "serviço", "severidade", "implantação", "mudança do Nexo", "restauração (h)"],
                [
                    [
                        r["id"],
                        r["service"],
                        r["severity"],
                        r["cause_deployment"] or "—",
                        "sim" if r["nexo_caused"] else "não",
                        _r(_hours(r["opened_at"], r["resolved_at"])),
                    ]
                    for r in sorted(incs, key=lambda x: str(x["opened_at"]))
                ],
            ),
        ],
        notes=[
            "DORA vem dos pipelines (POST /v2/deployments) e do ServiceNow (POST /v2/incidents); "
            "ROI é estimativa — as premissas estão na tabela"
        ],
    )


def _roi(d: CollectorDB, scope: Scope) -> dict[str, Any]:
    """I-18: horas economizadas = pontos entregues pelo Nexo × horas por ponto; ROI = horas × custo-hora − custo."""
    where, params = scope.where(months=scope.period)
    story = d.one(
        f"SELECT COALESCE(SUM(points), 0) AS pts, COUNT(*) AS n FROM fact_story WHERE done_at IS NOT NULL AND {where}",
        tuple(params),
    ) or {"pts": 0, "n": 0}
    swhere, sparams = scope.where(months=scope.period, steps=True)
    usage = d.one(f"SELECT COALESCE(SUM(usage_brl), 0) AS v FROM fact_step WHERE {swhere}", tuple(sparams)) or {"v": 0}
    hours_per_point = float(os.environ.get("NX_COLLECTOR_ROI_HOURS_PER_POINT", "6"))
    hourly = float(os.environ.get("NX_COLLECTOR_ROI_HOURLY_BRL", "120"))
    points = float(story["pts"] or 0)
    hours = points * hours_per_point
    cost = float(usage["v"] or 0)
    return {
        "hours_saved": round(hours, 1),
        "roi_brl": round(hours * hourly - cost, 2),
        "assumptions": [
            ["pontos entregues pelo Nexo", points],
            ["horas por ponto (manual, sem o Nexo)", hours_per_point],
            ["custo-hora (R$)", hourly],
            ["custo de uso do Nexo no período (R$)", round(cost, 2)],
            ["fonte das horas por ponto", "NX_COLLECTOR_ROI_HOURS_PER_POINT (calibrar com a linha de base I-16)"],
        ],
    }
