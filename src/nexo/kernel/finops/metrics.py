"""Métricas de eficácia, eficiência, planejamento e estimativa."""

from __future__ import annotations

import random
import statistics
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StoryFacts:
    story: str
    stack: str
    points: int
    cost_brl: float
    state: str
    human_gate: bool = False  # passou por aprovação humana
    escalated: bool = False
    bugs: int = 0
    attempts: int = 0
    spec_returns: int = 0
    clarifications: int = 0
    lead_hours: float | None = None
    cycle_hours: float | None = None
    waiting_human_hours: float = 0.0
    tokens: float = 0.0
    handoffs: int = 0
    feature: str = ""  # retrato enviado à telemetria central
    started_at: str | None = None
    done_at: str | None = None
    cap_brl: float | None = None
    estimate_brl: float | None = None


@dataclass
class MetricsReport:
    stories: int
    done: int
    e2e_without_human: float | None
    first_pass_yield: float | None
    avg_attempts: float | None
    brl_per_story: float | None
    brl_per_point: float | None
    tokens_per_point: float | None
    lead_time_h: float | None
    cycle_time_h: float | None
    waiting_human_h: float | None
    planning_accuracy: float | None
    escalation_rate: float | None
    by_stack: dict[str, dict[str, float]] = field(default_factory=dict)


def _ratio(n: int, d: int) -> float | None:
    return round(n / d, 4) if d else None


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 2) if values else None


def summarize(facts: list[StoryFacts]) -> MetricsReport:
    done = [f for f in facts if f.state == "Concluido"]
    points = sum(f.points for f in done)
    report = MetricsReport(
        stories=len(facts),
        done=len(done),
        e2e_without_human=_ratio(sum(1 for f in done if not f.human_gate and not f.escalated), len(done)),
        first_pass_yield=_ratio(
            sum(1 for f in done if f.bugs == 0 and f.spec_returns == 0 and f.clarifications == 0), len(done)
        ),
        avg_attempts=_mean([float(f.attempts) for f in done]),
        brl_per_story=_mean([f.cost_brl for f in done]),
        brl_per_point=round(sum(f.cost_brl for f in done) / points, 2) if points else None,
        tokens_per_point=round(sum(f.tokens for f in done) / points, 1) if points else None,
        lead_time_h=_mean([f.lead_hours for f in done if f.lead_hours is not None]),
        cycle_time_h=_mean([f.cycle_hours for f in done if f.cycle_hours is not None]),
        waiting_human_h=_mean([f.waiting_human_hours for f in done if f.human_gate]),
        planning_accuracy=_ratio(sum(1 for f in done if f.spec_returns == 0 and f.clarifications == 0), len(done)),
        escalation_rate=_ratio(sum(1 for f in facts if f.escalated), len(facts)),
    )
    for stack in sorted({f.stack for f in done}):
        items = [f for f in done if f.stack == stack]
        pts = sum(f.points for f in items)
        report.by_stack[stack] = {
            "stories": len(items),
            "brl_per_point": round(sum(f.cost_brl for f in items) / pts, 2) if pts else 0.0,
            "fpy": _ratio(sum(1 for f in items if f.bugs == 0), len(items)) or 0.0,
        }
    return report


def success_rate(successes: int, total: int, prior_alpha: float = 7.0, prior_beta: float = 3.0) -> float:
    """Média a posteriori Beta — histórico da rota/stack com prior de 0,7."""
    return round((prior_alpha + successes) / (prior_alpha + prior_beta + total), 4)


@dataclass(frozen=True)
class Estimate:
    points: int
    brl: float
    brl_low: float
    brl_high: float
    hours: float | None
    samples: int
    confidence: str  # alta | média | baixa (prior)


def estimate(
    points: int,
    samples_brl_per_point: list[float],
    samples_hours_per_point: list[float],
    prior_brl_per_point: float,
    iterations: int = 2000,
    seed: int = 7,
) -> Estimate:
    """Bootstrap do R$/ponto histórico → intervalo de 80%; sem histórico suficiente usa o prior."""
    if len(samples_brl_per_point) < 3:
        brl = points * prior_brl_per_point
        return Estimate(
            points, round(brl, 2), round(brl * 0.5, 2), round(brl * 2, 2), None, len(samples_brl_per_point), "baixa"
        )
    rng = random.Random(seed)  # noqa: S311 — reamostragem estatística, não criptografia
    means = sorted(
        statistics.fmean(rng.choices(samples_brl_per_point, k=len(samples_brl_per_point))) for _ in range(iterations)
    )
    low, high = means[int(0.1 * iterations)], means[int(0.9 * iterations)]
    mid = statistics.fmean(samples_brl_per_point)
    hours = statistics.fmean(samples_hours_per_point) * points if samples_hours_per_point else None
    confidence = "alta" if len(samples_brl_per_point) >= 20 else "média"
    return Estimate(
        points,
        round(mid * points, 2),
        round(low * points, 2),
        round(high * points, 2),
        round(hours, 1) if hours is not None else None,
        len(samples_brl_per_point),
        confidence,
    )


def baseline_summary(items: list[dict[str, Any]], since: str, until: str) -> dict[str, Any]:
    """Linha de base antes do Nexo: histórias e bugs fechados no período, sem os que passaram pelo Nexo.

    `items`: `kind` (story|bug|other), `created_at`, `closed_at`, `activated_at`, `points`, `nexo`.
    """
    from datetime import datetime

    from nexo.kernel.finops.insights import percentile

    def ts(value: Any) -> datetime | None:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    def stats(values: list[float]) -> dict[str, float] | None:
        if not values:
            return None
        return {
            "avg": round(sum(values) / len(values), 1),
            "p50": round(percentile(values, 50), 1),
            "p85": round(percentile(values, 85), 1),
        }

    manual = [i for i in items if not i.get("nexo")]
    stories = [i for i in manual if i.get("kind") == "story"]
    bugs = [i for i in manual if i.get("kind") == "bug"]
    lead: list[float] = []
    cycle: list[float] = []
    for i in stories:
        created, closed, active = ts(i.get("created_at")), ts(i.get("closed_at")), ts(i.get("activated_at"))
        if created and closed and closed >= created:
            lead.append((closed - created).total_seconds() / 3600)
        if active and closed and closed >= active:
            cycle.append((closed - active).total_seconds() / 3600)
    start, end = ts(since), ts(until)
    weeks = max(1.0, (end - start).days / 7) if start and end else 1.0
    return {
        "period": {"since": since, "until": until},
        "stories": len(stories),
        "bugs": len(bugs),
        "points": sum(float(i.get("points") or 0) for i in stories),
        "throughput_per_week": round(len(stories) / weeks, 2),
        "bug_ratio": round(len(bugs) / len(stories), 3) if stories else None,
        "lead_time_hours": stats(lead),
        "cycle_time_hours": stats(cycle),
        "excluded_nexo": len(items) - len(manual),
    }
