"""Detectores de custo.

tendência de eficiência, estouro de orçamento, licença ociosa, fatura divergente, tokens e cota.
"""

from __future__ import annotations

from typing import Any

from nexo.central.common import pid_of
from nexo.central.insights.context import EvalCtx, Finding, _finding
from nexo.central.metrics.scope import _month_add, squad_of_pid
from nexo.kernel.finops.insights import Detector, linear_projection, mean, percentile
from nexo.kernel.finops.money import brl


def efficiency_trend(c: EvalCtx, det: Detector) -> list[Finding]:
    worse = float(det.params.get("worse_pct", 20)) / 100
    back = int(det.params.get("months", 3))
    prev = [_month_add(c.month, -i) for i in range(1, back + 1)]
    rows = c.d.query(
        "SELECT squad, month, cost_brl, points, bugs FROM fact_story WHERE done_at IS NOT NULL"
        " AND month BETWEEN ? AND ?",
        (prev[-1], c.month),
    )
    out = []
    for squad in sorted({str(r["squad"] or "") for r in rows}):
        mine = [r for r in rows if str(r["squad"] or "") == squad]
        cur = [r for r in mine if r["month"] == c.month]
        old = [r for r in mine if r["month"] in prev]
        if len(cur) < det.min_sample or len(old) < det.min_sample:
            continue

        def rpp(rs: list[dict[str, Any]]) -> float | None:
            pts = sum(float(r["points"] or 0) for r in rs)
            return sum(float(r["cost_brl"] or 0) for r in rs) / pts if pts else None

        def fpy(rs: list[dict[str, Any]]) -> float:
            return sum(1 for r in rs if not r["bugs"]) / len(rs)

        months = [[r for r in old if r["month"] == m] for m in prev]
        months = [m for m in months if m]
        rpp_prev = mean([v for v in (rpp(m) for m in months) if v is not None])
        fpy_prev = mean([fpy(m) for m in months])
        rpp_cur, fpy_cur = rpp(cur), fpy(cur)
        reasons = []
        if rpp_cur is not None and rpp_prev and rpp_cur > rpp_prev * (1 + worse):
            reasons.append(f"R$/ponto {rpp_cur:.2f} vs {rpp_prev:.2f}")
        if fpy_prev and fpy_cur < fpy_prev * (1 - worse):
            reasons.append(f"FPY {fpy_cur:.0%} vs {fpy_prev:.0%}")
        if reasons:
            out.append(
                _finding(
                    "squad",
                    squad,
                    c.month,
                    {
                        "brl_per_point": round(rpp_cur or 0, 2),
                        "brl_per_point_prev": round(rpp_prev, 2),
                        "fpy": round(fpy_cur, 3),
                        "fpy_prev": round(fpy_prev, 3),
                        "n": len(cur),
                        "n_prev": len(old),
                    },
                    "; ".join(reasons),
                    squad=squad,
                )
            )
    return out


def budget_overrun(c: EvalCtx, det: Detector) -> list[Finding]:
    day = c.now.day
    if day >= int(det.params.get("until_day", 20)) or day < int(det.params.get("min_days", 3)):
        return []
    import calendar

    days = calendar.monthrange(c.now.year, c.now.month)[1]
    usage = {
        str(r["dev_pid"]): float(r["brl"])
        for r in c.d.query(
            "SELECT dev_pid, COALESCE(SUM(usage_brl), 0) AS brl FROM fact_step WHERE month = ? GROUP BY dev_pid",
            (c.month,),
        )
    }
    squads = squad_of_pid(c.d)
    out = []
    per_squad: dict[str, list[float]] = {}
    for b in c.d.query("SELECT dev, limit_brl FROM budgets WHERE month = ? AND tool = ''", (c.month,)):
        pid = pid_of(str(b["dev"]))
        limit = float(b["limit_brl"] or 0)
        used = usage.get(pid, 0.0)
        projected = linear_projection(used, day, days)
        squad = squads.get(pid, "")
        slot = per_squad.setdefault(squad, [0.0, 0.0])
        slot[0] += limit
        slot[1] += projected
        if limit and projected > limit:
            out.append(
                _finding(
                    "dev",
                    pid,
                    c.month,
                    {
                        "limit_brl": round(limit, 2),
                        "used_brl": round(used, 2),
                        "projection_brl": round(projected, 2),
                        "day": day,
                    },
                    f"projeção {brl(projected)} > limite {brl(limit)}",
                    squad=squad,
                    dev_pid=pid,
                    purpose="budget",
                )
            )
    for squad, (limit, projected) in sorted(per_squad.items()):
        if limit and projected > limit:
            out.append(
                _finding(
                    "squad",
                    squad,
                    c.month,
                    {"limit_brl": round(limit, 2), "projection_brl": round(projected, 2), "day": day},
                    f"squad {squad or '—'}: projeção {brl(projected)} > {brl(limit)}",
                    squad=squad,
                )
            )
    return out


def idle_license(c: EvalCtx, det: Detector) -> list[Finding]:
    prev = _month_add(c.month, -1)
    util = float(det.params.get("util_pct", 20)) / 100
    used = {
        (str(r["dev_pid"]), str(r["tool"])): float(r["brl"])
        for r in c.d.query(
            "SELECT dev_pid, tool, COALESCE(SUM(usage_brl), 0) AS brl FROM fact_step WHERE month = ?"
            " GROUP BY dev_pid, tool",
            (prev,),
        )
    }
    recent = {
        (str(r["dev_pid"]), str(r["tool"]))
        for r in c.d.query(
            "SELECT DISTINCT dev_pid, tool FROM fact_step WHERE started_at >= ?",
            (c.since(int(det.params.get("idle_days", 30))),),
        )
    }
    squads = squad_of_pid(c.d)
    out = []
    for lic in c.d.query("SELECT dev, tool, monthly_brl FROM licenses"):
        pid = pid_of(str(lic["dev"]))
        monthly = float(lic["monthly_brl"] or 0)
        spent = used.get((pid, str(lic["tool"])), 0.0)
        idle = (pid, str(lic["tool"])) not in recent
        if monthly and (spent < util * monthly or idle):
            detail = f"{lic['tool']}: uso {spent / monthly:.0%} da assinatura em {prev}" + (
                "; 0 passos em 30 dias" if idle else ""
            )
            out.append(
                _finding(
                    "license",
                    f"{pid}:{lic['tool']}",
                    prev,
                    {
                        "tool": lic["tool"],
                        "monthly_brl": monthly,
                        "used_brl": round(spent, 2),
                        "idle_30d": idle,
                    },
                    detail,
                    squad=squads.get(pid, ""),
                    dev_pid=pid,
                    purpose="license",
                )
            )
    return out


def tight_quota(c: EvalCtx, det: Detector) -> list[Finding]:
    counts: dict[tuple[str, str], int] = {}
    for r in c.d.query(
        "SELECT tool, dev_pid FROM fact_event WHERE type = 'run.handoff' AND kind = 'quota' AND at >= ?", (c.since(7),)
    ):
        key = (str(r["tool"] or "?"), str(r["dev_pid"]))
        counts[key] = counts.get(key, 0) + 1
    limit = int(det.params.get("handoffs", 3))
    out = []
    for tool in sorted({k[0] for k in counts}):
        hit = [n for (t, _), n in counts.items() if t == tool and n >= limit]
        if hit:
            out.append(
                _finding(
                    "tool",
                    tool,
                    c.week,
                    {"devs_affected": len(hit), "handoffs": sum(hit)},
                    f"{tool}: {len(hit)} dev(s) com ≥ {limit} handoffs por cota na semana",
                )
            )
    return out


def invoice_divergence(c: EvalCtx, det: Detector) -> list[Finding]:
    limit = float(det.params.get("pct", 5))
    return [
        _finding(
            "tool",
            str(r["tool"]),
            str(r["month"]),
            {"amount_brl": r["amount_brl"], "measured_brl": r["measured_brl"], "diff_pct": r["diff_pct"]},
            f"{r['tool']} {r['month']}: medido difere {r['diff_pct']}% da fatura",
        )
        for r in c.d.query("SELECT * FROM invoices")
        if abs(float(r["diff_pct"] or 0)) > limit
    ]


def token_hogs(c: EvalCtx, det: Detector) -> list[Finding]:
    """histórias que mais consomem tokens no squad (acima do percentil), com a causa provável."""
    from nexo.kernel.finops import tokens

    rows = c.d.query(
        "SELECT project, story, COALESCE(squad, '') AS squad, COUNT(*) AS steps,"
        " COALESCE(SUM(tokens_in), 0) AS tokens_in, COALESCE(SUM(tokens_out), 0) AS tokens_out,"
        " COALESCE(SUM(cache_read), 0) AS cache_read, COALESCE(SUM(cache_write), 0) AS cache_write"
        " FROM fact_step WHERE started_at >= ? AND story IS NOT NULL AND story <> ''"
        " GROUP BY project, story, COALESCE(squad, '')",
        (c.since(int(det.params.get("days", 30))),),
    )
    groups: dict[str, list[float]] = {}
    for r in rows:
        groups.setdefault(str(r["squad"]), []).append(tokens.total_tokens(r))
    pct = float(det.params.get("pct", 90))
    out = []
    for r in rows:
        sample = groups[str(r["squad"])]
        if len(sample) < det.min_sample:
            continue
        used, limit = tokens.total_tokens(r), percentile(sample, pct)
        if used <= limit:
            continue
        hit = tokens.cache_hit(r)
        causes = []
        if hit is not None and hit < float(det.params.get("min_cache_hit", 0.3)):
            causes.append(f"acerto do cache {100 * hit:.0f}%")
        if int(r["steps"]) >= int(det.params.get("steps", 6)):
            causes.append(f"{r['steps']} passos (tarefas grandes ou retrabalho)")
        out.append(
            _finding(
                "story",
                f"{r['project']}:{r['story']}",
                "",
                {
                    "project": r["project"],
                    "story": r["story"],
                    "tokens": int(used),
                    "p": int(limit),
                    "cache_hit": hit,
                    "steps": r["steps"],
                    "n_group": len(sample),
                },
                f"{r['story']}: {int(used)} tokens > p{pct:.0f} do squad ({int(limit)})"
                + (f"; {'; '.join(causes)}" if causes else ""),
                squad=r["squad"],
            )
        )
    return out
