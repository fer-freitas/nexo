"""Detectores de plataforma: mudança do cérebro, atraso da telemetria e limiares configuráveis."""

from __future__ import annotations

import json
import time
from typing import Any

from nexo.central.insights.context import THRESHOLD_METRICS, EvalCtx, Finding, _finding, _rate
from nexo.central.insights.quality import _route_rows
from nexo.kernel.finops.insights import Detector, mean, two_proportion_p


def brain_change(c: EvalCtx, det: Detector) -> list[Finding]:
    alpha = float(det.params.get("p", 0.1))
    cost_pct = float(det.params.get("cost_pct", 20)) / 100
    rows = [r for r in _route_rows(c, int(det.params.get("days", 60))) if r["brain_version"]]
    first: dict[str, str] = {}
    for r in sorted(rows, key=lambda x: str(x["started_at"])):
        first.setdefault(str(r["brain_version"]), str(r["started_at"]))
    versions = sorted(first, key=lambda v: first[v])
    if len(versions) < 2:
        return []
    before, after = versions[-2], versions[-1]
    out = []
    for role, stack in sorted({(str(r["role"]), str(r["stack"])) for r in rows}):

        def pick(v: str, role: str = role, stack: str = stack) -> list[dict[str, Any]]:
            return [r for r in rows if r["brain_version"] == v and str(r["role"]) == role and str(r["stack"]) == stack]

        b, a = pick(before), pick(after)
        if len(b) < det.min_sample or len(a) < det.min_sample:
            continue
        bok = sum(1 for r in b if r["outcome"] == "success")
        aok = sum(1 for r in a if r["outcome"] == "success")
        p = two_proportion_p(bok, len(b), aok, len(a))
        bcost = mean([float(r["usage_brl"] or 0) for r in b])
        acost = mean([float(r["usage_brl"] or 0) for r in a])
        reasons = []
        if _rate(aok, len(a)) < _rate(bok, len(b)) and p < alpha:
            reasons.append(f"sucesso {_rate(bok, len(b)):.0%}→{_rate(aok, len(a)):.0%} (p={p:.3f})")
        if bcost and acost > bcost * (1 + cost_pct):
            reasons.append(f"R$/passo {bcost:.3f}→{acost:.3f}")
        if reasons:
            out.append(
                _finding(
                    "org",
                    f"{before}->{after}|{role}|{stack}",
                    after,
                    {
                        "before": before,
                        "after": after,
                        "role": role,
                        "stack": stack,
                        "n_before": len(b),
                        "n_after": len(a),
                        "success_before": round(_rate(bok, len(b)), 3),
                        "success_after": round(_rate(aok, len(a)), 3),
                        "p": round(p, 4),
                        "cost_before": round(bcost, 4),
                        "cost_after": round(acost, 4),
                    },
                    f"cérebro {before}→{after} ({role}/{stack or '—'}): " + "; ".join(reasons),
                )
            )
    return out


def telemetry_delay(c: EvalCtx, det: Detector) -> list[Finding]:
    hours = float(det.params.get("hours", 72))
    since = time.time() - 7 * 86400
    out = [
        _finding(
            "device",
            str(r["device_id"]),
            c.week,
            {"lag_hours": r["lag_hours"], "max_lag_hours": r["max_lag_hours"]},
            f"dispositivo {str(r['device_id'])[:8]}: pendência de {float(r['lag_hours']):.0f} h",
        )
        for r in c.d.query("SELECT * FROM device_lag WHERE lag_hours > ? AND last_seen >= ?", (hours, since))
    ]
    for a in c.d.query("SELECT detail FROM audit WHERE action = 'anchor.divergent' AND at >= ?", (since,)):
        detail = json.loads(a["detail"] or "{}")
        out.append(
            _finding(
                "device",
                f"ancora:{detail.get('device_id')}",
                c.week,
                detail,
                f"âncora divergente no dispositivo {str(detail.get('device_id'))[:8]} (log reescrito?)",
            )
        )
    return out


THRESHOLD_GROUPS = {
    "step": ("squad", "project", "stack", "tool", "model"),
    "story": ("squad", "project", "stack"),
    "handoff": ("squad", "project", "tool"),
    "blocked": ("squad", "project", "kind"),
}


def threshold(c: EvalCtx, det: Detector) -> list[Finding]:
    """Detector genérico do cérebro: métrica de um catálogo fechado, agrupamento permitido, comparação e limiar."""
    metric = str(det.params.get("metric", ""))
    if metric not in THRESHOLD_METRICS:
        raise ValueError(f"{det.id}: métrica fora do catálogo: {metric}")
    source, agg = THRESHOLD_METRICS[metric]
    group = str(det.params.get("group_by", "squad"))
    if group not in THRESHOLD_GROUPS[source]:
        raise ValueError(f"{det.id}: agrupamento não permitido para {metric}: {group}")
    since = c.since(int(det.params.get("days", 30)))
    if source == "step":
        rows = c.d.query("SELECT * FROM fact_step WHERE started_at >= ?", (since,))
    elif source == "story":
        rows = c.d.query("SELECT * FROM fact_story WHERE done_at >= ?", (since,))
    else:
        rows = c.d.query(
            "SELECT * FROM fact_event WHERE type = ? AND at >= ?",
            ("run.handoff" if source == "handoff" else "run.blocked", since),
        )
    op, value = str(det.params.get("op", ">")), float(det.params.get("value", 0))
    out = []
    for key in sorted({str(r.get(group) or "") for r in rows}):
        mine = [r for r in rows if str(r.get(group) or "") == key]
        if len(mine) < det.min_sample:
            continue
        if agg == "sum_brl":
            v = sum(float(r["usage_brl"] or 0) for r in mine)
        elif agg == "success_pct":
            v = 100 * sum(1 for r in mine if r["outcome"] == "success") / len(mine)
        elif agg == "fpy_pct":
            v = 100 * sum(1 for r in mine if not r["bugs"]) / len(mine)
        elif agg == "brl_per_point":
            pts = sum(float(r["points"] or 0) for r in mine)
            v = sum(float(r["cost_brl"] or 0) for r in mine) / pts if pts else 0.0
        elif agg == "avg_wait":
            v = mean([float(r["waiting_human_hours"] or 0) for r in mine])
        else:
            v = float(len(mine))
        if (op == ">" and v > value) or (op == "<" and v < value):
            out.append(
                _finding(
                    group,
                    key,
                    c.month,
                    {"metric": metric, "value": round(v, 3), "limit": value, "n": len(mine)},
                    f"{metric} de {group} {key or '—'} = {v:.2f} ({op} {value:g})",
                    squad=key if group == "squad" else None,
                )
            )
    return out
