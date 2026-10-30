"""Detectores de fluxo: história fora da curva, calibração da estimativa, gargalo humano e adoção."""

from __future__ import annotations

from nexo.central.insights.context import EvalCtx, Finding, _finding
from nexo.central.metrics.scope import _month_add
from nexo.kernel.finops.insights import Detector, mean, percentile


def outlier_story(c: EvalCtx, det: Detector) -> list[Finding]:
    pct = float(det.params.get("pct", 90))
    rows = c.d.query(
        "SELECT project, story, squad, stack, cost_brl, points, attempts, spec_returns, done_at FROM fact_story"
        " WHERE started_at >= ? OR done_at IS NULL",
        (c.since(int(det.params.get("days", 90))),),
    )
    groups: dict[tuple[str, str], list[float]] = {}
    for r in rows:
        if r["done_at"] and r["points"]:
            groups.setdefault((str(r["squad"] or ""), str(r["stack"] or "")), []).append(
                float(r["cost_brl"] or 0) / float(r["points"])
            )
    out = []
    for r in rows:
        reasons = []
        key = (str(r["squad"] or ""), str(r["stack"] or ""))
        sample = groups.get(key, [])
        cpp = float(r["cost_brl"] or 0) / float(r["points"]) if r["points"] else None
        p90 = percentile(sample, pct) if len(sample) >= det.min_sample else None
        if cpp is not None and p90 is not None and cpp > p90:
            reasons.append(f"R$/ponto {cpp:.2f} > p{pct:.0f} {p90:.2f}")
        if int(r["attempts"] or 0) >= int(det.params.get("attempts", 3)):
            reasons.append(f"{r['attempts']} tentativas")
        if int(r["spec_returns"] or 0) >= int(det.params.get("spec_returns", 2)):
            reasons.append(f"{r['spec_returns']} retornos de spec")
        if reasons:
            out.append(
                _finding(
                    "story",
                    f"{r['project']}:{r['story']}",
                    "",
                    {
                        "project": r["project"],
                        "story": r["story"],
                        "brl_per_point": round(cpp or 0, 2),
                        "p90": round(p90, 2) if p90 is not None else None,
                        "attempts": r["attempts"],
                        "spec_returns": r["spec_returns"],
                        "n_group": len(sample),
                    },
                    f"{r['story']}: " + "; ".join(reasons),
                    squad=r["squad"] or "",
                )
            )
    return out


def estimate_calibration(c: EvalCtx, det: Detector) -> list[Finding]:
    limit = float(det.params.get("error_pct", 30)) / 100
    rows = c.d.query(
        "SELECT project, feature, COALESCE(SUM(estimate_brl), 0) AS est, COALESCE(SUM(cost_brl), 0) AS real"
        " FROM fact_story WHERE done_at IS NOT NULL AND month = ? AND estimate_brl IS NOT NULL"
        " GROUP BY project, feature",
        (c.month,),
    )
    errors = [(float(r["real"]) - float(r["est"])) / float(r["est"]) for r in rows if float(r["est"]) > 0]
    if len(errors) < det.min_sample:
        return []
    mae = mean([abs(e) for e in errors])
    bias = all(e > 0 for e in errors) or all(e < 0 for e in errors)
    if mae <= limit and not bias:
        return []
    detail = f"erro médio {mae:.0%}" + (
        " com viés constante " + ("para cima" if errors[0] > 0 else "para baixo") if bias else ""
    )
    return [
        _finding(
            "org",
            "estimativa",
            c.month,
            {
                "features": len(errors),
                "mean_abs_error": round(mae, 3),
                "mean_error": round(mean(errors), 3),
                "bias": bias,
            },
            detail,
        )
    ]


def human_bottleneck(c: EvalCtx, det: Detector) -> list[Finding]:
    since = c.since(int(det.params.get("days", 30)))
    out = []
    rows = c.d.query(
        "SELECT squad, waiting_human_hours FROM fact_story WHERE human_gate = 1 AND done_at >= ?", (since,)
    )
    for squad in sorted({str(r["squad"] or "") for r in rows}):
        waits = [float(r["waiting_human_hours"] or 0) for r in rows if str(r["squad"] or "") == squad]
        avg = mean(waits)
        if len(waits) >= det.min_sample and avg > float(det.params.get("hours", 8)):
            out.append(
                _finding(
                    "squad",
                    f"espera:{squad}",
                    c.month,
                    {"avg_wait_hours": round(avg, 2), "n": len(waits)},
                    f"espera média por humano {avg:.1f} h no squad {squad or '—'}",
                    squad=squad,
                )
            )
    gates = c.d.query(
        "SELECT project, story, decision, score, threshold FROM fact_gate WHERE gate = 'code' AND at >= ?"
        " ORDER BY project, story, at",
        (since,),
    )
    margin = float(det.params.get("margin", 0.03))
    pending: dict[tuple[str, str], bool] = {}
    near = approved = 0
    for g in gates:
        key = (str(g["project"]), str(g["story"]))
        if g["decision"] == "human" and g["score"] is not None and g["threshold"] is not None:
            pending[key] = float(g["score"]) >= float(g["threshold"]) - margin
        elif g["decision"] in ("approved", "rejected") and key in pending and pending.pop(key):
            near += 1
            approved += 1 if g["decision"] == "approved" else 0
    share = float(det.params.get("approve_pct", 90)) / 100
    if near >= det.min_sample and approved / near >= share:
        out.append(
            _finding(
                "org",
                "limiar",
                c.month,
                {"near_threshold": near, "approved_unchanged": approved, "share": round(approved / near, 3)},
                f"{approved}/{near} portões humanos perto do limiar aprovados sem mudança",
            )
        )
    return out


def adoption(c: EvalCtx, det: Detector) -> list[Finding]:
    prev = _month_add(c.month, -1)
    limit = float(det.params.get("pct", 30)) / 100
    out = []
    for t in c.d.query("SELECT squad, stories FROM tracker_totals WHERE month = ?", (prev,)):
        total = int(t["stories"] or 0)
        if total < det.min_sample:
            continue
        row = c.d.one(
            "SELECT COUNT(*) AS n FROM fact_story WHERE squad = ? AND SUBSTR(started_at, 1, 7) = ?", (t["squad"], prev)
        )
        nexo = int(row["n"] if row else 0)
        if nexo / total < limit:
            out.append(
                _finding(
                    "squad",
                    str(t["squad"]),
                    prev,
                    {"tracker": total, "nexo": nexo, "pct": round(nexo / total, 3)},
                    f"squad {t['squad']}: {nexo}/{total} histórias pelo Nexo em {prev}",
                    squad=str(t["squad"]),
                )
            )
    return out
