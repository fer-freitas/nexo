"""Detectores de qualidade: rota ruim, escape, queda de qualidade e spec frágil."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from nexo.central.insights.context import EvalCtx, Finding, _finding, _rate, _ts
from nexo.kernel.finops.insights import Detector, mean, two_proportion_p


def _route_rows(c: EvalCtx, days: int) -> list[dict[str, Any]]:
    return c.d.query(
        "SELECT s.tool, s.model, COALESCE(t.kind, '-') AS kind, COALESCE(s.stack, '') AS stack, s.role,"
        " s.outcome, s.usage_brl, s.brain_version, s.started_at FROM fact_step s LEFT JOIN fact_task t"
        " ON t.project = s.project AND t.task = s.target WHERE s.started_at >= ?",
        (c.since(days),),
    )


def bad_route(c: EvalCtx, det: Detector) -> list[Finding]:
    alpha = float(det.params.get("p", 0.1))
    groups: dict[tuple[str, str, str, str], list[int]] = {}
    for r in _route_rows(c, int(det.params.get("days", 30))):
        slot = groups.setdefault((str(r["tool"]), str(r["model"]), str(r["kind"]), str(r["stack"])), [0, 0])
        slot[0] += 1 if r["outcome"] == "success" else 0
        slot[1] += 1
    out = []
    for (tool, model, kind, stack), (ok, n) in sorted(groups.items()):
        if n < det.min_sample:
            continue
        rivals = [
            (k, v)
            for k, v in groups.items()
            if k[2:] == (kind, stack) and k[:2] != (tool, model) and v[1] >= det.min_sample and _rate(*v) > _rate(ok, n)
        ]
        if not rivals:
            continue
        best_key, (bok, bn) = max(rivals, key=lambda kv: (_rate(*kv[1]), kv[0]))
        p = two_proportion_p(bok, bn, ok, n)
        if p < alpha:
            out.append(
                _finding(
                    "route",
                    f"{tool}/{model}|{kind}|{stack}",
                    c.month,
                    {
                        "route": f"{tool}/{model}",
                        "kind": kind,
                        "stack": stack,
                        "success": round(_rate(ok, n), 3),
                        "n": n,
                        "best": f"{best_key[0]}/{best_key[1]}",
                        "best_success": round(_rate(bok, bn), 3),
                        "best_n": bn,
                        "p": round(p, 4),
                    },
                    f"{tool}/{model} em {kind}/{stack or '—'}: {_rate(ok, n):.0%} vs {best_key[0]}/{best_key[1]} "
                    f"{_rate(bok, bn):.0%} (p={p:.3f})",
                )
            )
    return out


def escape(c: EvalCtx, det: Detector) -> list[Finding]:
    days = int(det.params.get("days", 30))
    done = {
        (str(r["project"]), str(r["story"])): (r["done_at"], r["squad"])
        for r in c.d.query("SELECT project, story, done_at, squad FROM fact_story WHERE done_at IS NOT NULL")
    }
    out = []
    for b in c.d.query("SELECT project, story, bug, source, at FROM fact_event WHERE type = 'bug.created'"):
        info = done.get((str(b["project"]), str(b["story"])))
        if not info:
            continue
        done_at, bug_at = _ts(info[0]), _ts(b["at"])
        if done_at and bug_at and done_at < bug_at <= done_at + timedelta(days=days):
            out.append(
                _finding(
                    "story",
                    f"{b['project']}:{b['bug']}",
                    "",
                    {
                        "project": b["project"],
                        "story": b["story"],
                        "bug": b["bug"],
                        "source": b["source"],
                        "days_after_done": round((bug_at - done_at).total_seconds() / 86400, 1),
                    },
                    f"bug {b['bug']} em {b['story']} após concluída",
                    squad=info[1] or "",
                )
            )
    return out


def quality_drop(c: EvalCtx, det: Detector) -> list[Finding]:
    now, week, prev = c.now.isoformat(), c.since(7), c.since(14)
    cov_min = float(det.params.get("coverage_min", 70))
    rows = c.d.query(
        "SELECT squad, at, findings_high, findings_critical, coverage FROM fact_quality WHERE at >= ?", (prev,)
    )
    flaky = c.d.query("SELECT squad, at FROM fact_event WHERE type = 'quality.flaky' AND at >= ?", (prev,))
    out = []
    for squad in sorted({str(r["squad"] or "") for r in rows}):
        this = [r for r in rows if str(r["squad"] or "") == squad and week <= str(r["at"]) <= now]
        last = [r for r in rows if str(r["squad"] or "") == squad and str(r["at"]) < week]
        if len(this) < det.min_sample:
            continue
        high = sum(int(r["findings_high"] or 0) + int(r["findings_critical"] or 0) for r in this)
        high_prev = sum(int(r["findings_high"] or 0) + int(r["findings_critical"] or 0) for r in last)
        fl = sum(1 for f in flaky if str(f["squad"] or "") == squad and str(f["at"]) >= week)
        fl_prev = sum(1 for f in flaky if str(f["squad"] or "") == squad and str(f["at"]) < week)
        covs = [float(r["coverage"]) for r in this if r["coverage"] is not None]
        cov = mean(covs) if covs else None
        reasons = []
        if high > high_prev and high > 0:
            reasons.append(f"achados altos {high_prev}→{high}")
        if fl > fl_prev and fl > 0:
            reasons.append(f"testes instáveis {fl_prev}→{fl}")
        if cov is not None and cov < cov_min:
            reasons.append(f"cobertura {cov:.0f}% < {cov_min:.0f}%")
        if reasons:
            out.append(
                _finding(
                    "squad",
                    squad,
                    c.week,
                    {
                        "high": high,
                        "high_prev": high_prev,
                        "flaky": fl,
                        "flaky_prev": fl_prev,
                        "coverage": cov,
                        "runs": len(this),
                    },
                    f"squad {squad or '—'}: " + "; ".join(reasons),
                    squad=squad,
                )
            )
    return out


def fragile_spec(c: EvalCtx, det: Detector) -> list[Finding]:
    limit = int(det.params.get("per_story", 3))
    rows = c.d.query(
        "SELECT project, feature, squad, story, clarifications, spec_returns FROM fact_story WHERE started_at >= ?",
        (c.since(int(det.params.get("days", 90))),),
    )
    out = []
    for project, feature in sorted({(str(r["project"]), str(r["feature"])) for r in rows}):
        mine = [r for r in rows if (str(r["project"]), str(r["feature"])) == (project, feature)]
        fragile = [r for r in mine if int(r["clarifications"] or 0) + int(r["spec_returns"] or 0) >= limit]
        if len(mine) >= det.min_sample and fragile:
            out.append(
                _finding(
                    "feature",
                    f"{project}:{feature}",
                    "",
                    {
                        "project": project,
                        "feature": feature,
                        "stories": len(mine),
                        "fragile_stories": len(fragile),
                        "clarifications": sum(int(r["clarifications"] or 0) for r in mine),
                        "spec_returns": sum(int(r["spec_returns"] or 0) for r in mine),
                    },
                    f"{feature}: {len(fragile)} história(s) com ≥ {limit} esclarecimentos/retornos",
                    squad=str(mine[0]["squad"] or ""),
                )
            )
    return out
