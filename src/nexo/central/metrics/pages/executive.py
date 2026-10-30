"""Página executiva: entregas, custo, prazo e comparação com a linha de base."""

from __future__ import annotations

import json
from typing import Any

from nexo.central.db import CollectorDB
from nexo.central.metrics.queries import _cost, _story_kpis
from nexo.central.metrics.scope import Scope, _day_fraction, _month_add, devs_in
from nexo.central.metrics.widgets import _drill, _pct, _r, attention, headline, kpi, month_name, page, series
from nexo.kernel.finops.insights import linear_projection
from nexo.kernel.finops.money import brl

FPY_TARGET = 85.0  # meta de referência de "entregue sem bug" (%), a mesma dos insights de qualidade


def _budget(d: CollectorDB, month: str, devs: set[str] | None) -> float:
    rows = d.query("SELECT dev, limit_brl FROM budgets WHERE month = ? AND tool = ''", (month,))
    return round(sum(float(r["limit_brl"] or 0) for r in rows if devs is None or r["dev"] in devs), 2)


def executive(d: CollectorDB, scope: Scope) -> dict[str, Any]:
    cur = scope.current
    cost = _cost(d, scope, cur)
    day, days, is_current = _day_fraction(scope)
    projection = _r(linear_projection(cost, day, days)) if is_current else cost
    devs = devs_in(d, scope.squads)
    budget = _budget(d, cur, devs)
    stories = _story_kpis(d, scope, scope.period)
    where, params = scope.where(months=(cur, cur), steps=True)
    active = d.one(f"SELECT COUNT(DISTINCT dev_pid) AS n FROM fact_step WHERE {where}", tuple(params))
    swhere, sparams = scope.where()
    started = d.one(
        f"SELECT COUNT(*) AS n FROM fact_story WHERE SUBSTR(started_at, 1, 7) = ? AND {swhere}",
        (cur, *sparams),
    )
    tracker = d.query("SELECT squad, stories FROM tracker_totals WHERE month = ?", (cur,))
    total_tracker = sum(int(r["stories"] or 0) for r in tracker if scope.squads is None or r["squad"] in scope.squads)
    cost_trend, fpy_trend = _trend(d, scope)
    before = _baseline(d, scope)
    lead_now = _lead_p50(d, scope)
    org: list[dict[str, Any]] = []
    if scope.squads is not None:
        all_cost = d.one("SELECT COALESCE(SUM(usage_brl), 0) AS v FROM fact_step WHERE month = ?", (cur,))
        org_stories = _story_kpis(d, scope, scope.period, all_squads=True)
        org = [
            kpi("org_cost", "Custo da org no mês", _r(float(all_cost["v"] if all_cost else 0)), "brl"),
            kpi("org_fpy", "FPY da org", org_stories["fpy"], "pct"),
            kpi("org_brl_per_point", "R$/ponto da org", org_stories["brl_per_point"], "brl"),
        ]
    lead_gain = _pct(before["lead_p50"] - lead_now, before["lead_p50"]) if before.get("lead_p50") and lead_now else None
    month = month_name(cur)
    questions = _questions(cost, projection, budget, stories, lead_gain, before, month, cur)
    todo = _todo(budget, projection, before, total_tracker, month)
    return page(
        "executive",
        "Visão geral",
        scope,
        headline=questions,
        attention=todo,
        kpis=[
            kpi("cost_month", f"Custo de uso {cur}", cost, "brl", _drill("step", month=cur)),
            kpi("budget_month", "Orçamento do mês", budget, "brl"),
            kpi("projection", "Projeção de fim de mês", projection, "brl"),
            kpi("budget_pct", "Projeção × orçamento", _pct(projection or 0, budget), "pct"),
            kpi("brl_per_point", "R$ por ponto", stories["brl_per_point"], "brl", _drill("story", done="1")),
            kpi("e2e", "% entregue sem humano", stories["e2e"], "pct", _drill("story", done="1", human_gate="0")),
            kpi("fpy", "FPY (sem bug)", stories["fpy"], "pct", _drill("story", done="1", bugs="0")),
            kpi("lead_hours", "Lead time médio (h)", stories["lead_hours"], "num"),
            kpi("done", "Histórias concluídas", stories["done"], "int", _drill("story", done="1")),
            kpi("active_devs", "Devs ativos no mês", int(active["n"] if active else 0), "int"),
            kpi("lead_before", "Lead time p50 antes do Nexo (h)", before.get("lead_p50"), "num"),
            kpi("lead_now", "Lead time p50 com o Nexo (h)", lead_now, "num", _drill("story", done="1")),
            kpi("lead_gain", "Ganho de lead time vs linha de base", lead_gain, "pct"),
            kpi("bugs_before", "Bugs por história antes do Nexo", before.get("bug_ratio"), "num"),
            kpi(
                "adoption",
                "% das histórias do tracker pelo Nexo",
                _pct(int(started["n"] if started else 0), total_tracker),
                "pct",
            ),
        ],
        charts=[
            series("cost_trend", "Custo de uso por mês (6 meses)", cost_trend, "brl"),
            series("fpy_trend", "FPY por mês (6 meses)", fpy_trend, "pct"),
        ],
        org=org,
        notes=[],
    )


def _questions(
    cost: float | None,
    projection: float | None,
    budget: float,
    stories: dict[str, Any],
    lead_gain: float | None,
    before: dict[str, float],
    month_name: str,
    cur: str,
) -> list[dict[str, Any]]:
    """As quatro perguntas da gestão, respondidas com contexto e estado."""
    if not budget:
        spend = ("warn", "sem orçamento definido")
    else:
        ratio = 100 * (projection or 0) / budget
        spend = (
            ("good", f"dentro do orçamento ({ratio:.0f}%)")
            if ratio <= 90
            else ("warn", f"perto do teto ({ratio:.0f}%)")
            if ratio <= 100
            else ("bad", f"projeção acima do orçamento ({ratio:.0f}%)")
        )
    done = int(stories["done"] or 0)
    fpy, e2e = stories["fpy"], stories["e2e"]
    clean = round((fpy or 0) * done / 100)
    alone = round((e2e or 0) * done / 100)
    if fpy is None:
        quality = ("neutral", "nenhuma história entregue no período")
    elif fpy >= FPY_TARGET:
        quality = ("good", f"acima da meta de {FPY_TARGET:.0f}%")
    else:
        quality = ("bad" if fpy < FPY_TARGET - 15 else "warn", f"abaixo da meta de {FPY_TARGET:.0f}%")
    if lead_gain is not None:
        worth = (
            "good" if lead_gain > 0 else "warn",
            f"lead time {abs(lead_gain):.0f}% {'menor' if lead_gain > 0 else 'maior'} que antes",
        )
    elif before:
        worth = ("neutral", "sem histórias concluídas para comparar com o antes")
    else:
        worth = ("neutral", "comparação com o antes: falta a linha de base")
    return [
        headline(
            "spend",
            "Quanto estamos gastando?",
            cost,
            "brl",
            f"{month_name} · projeção de {brl(projection)} no fim do mês",
            *spend,
            drill=_drill("step", month=cur),
        ),
        headline(
            "quality",
            "Estamos entregando com qualidade?",
            fpy,
            "pct",
            f"das histórias sem bug depois de entregues ({clean} de {done})" if done else "sem entregas no período",
            *quality,
            drill=_drill("story", done="1", bugs="0"),
        ),
        headline(
            "autonomy",
            "Quanto roda sem precisar de alguém?",
            e2e,
            "pct",
            f"{alone} de {done} histórias passaram direto pelo portão" if done else "sem entregas no período",
            bar=e2e,
            drill=_drill("story", done="1", human_gate="0"),
        ),
        headline(
            "worth",
            "Está valendo a pena?",
            stories["brl_per_point"],
            "brl",
            "por ponto de história entregue",
            *worth,
            drill=_drill("story", done="1"),
        ),
    ]


def _todo(
    budget: float, projection: float | None, before: dict[str, float], tracker_total: int, month_name: str
) -> list[dict[str, str]]:
    """O que falta configurar ou está fora do trilho, com o comando que resolve."""
    items = []
    if not budget:
        items.append(
            attention(
                f"Sem orçamento para {month_name}",
                f"A projeção é de {brl(projection)}; com um teto, o Nexo avisa antes de estourar.",
                "nexo cost budget set",
                "warn",
            )
        )
    elif projection and projection > budget:
        items.append(
            attention(
                "Projeção acima do orçamento",
                f"{brl(projection)} previstos para um teto de {brl(budget)}.",
                "ver custos",
                "bad",
                "/dash/finops",
            )
        )
    if not before:
        items.append(
            attention(
                "Linha de base não carregada",
                "Sem ela não dá para mostrar o ganho de lead time e de bugs em relação ao antes do Nexo.",
                "nexo metrics baseline",
            )
        )
    if not tracker_total:
        items.append(
            attention(
                "Adoção sem o total do tracker",
                "Carregue quantas histórias o squad fez no mês para medir quanto passa pelo Nexo.",
                "PUT /v2/admin/tracker-totals",
            )
        )
    return items


def _baseline(d: CollectorDB, scope: Scope) -> dict[str, float]:
    """Linha de base dos squads do recorte: média ponderada por histórias do p50 de lead time e bugs/história."""
    rows = [
        r
        for r in d.query("SELECT squad, data FROM baselines")
        if scope.squads is None or (r["squad"] or "") in scope.squads
    ]
    total = lead = bugs = 0.0
    for r in rows:
        data = json.loads(r["data"])
        n = float(data.get("stories") or 0)
        if not n:
            continue
        total += n
        lead += n * float((data.get("lead_time_hours") or {}).get("p50") or 0)
        bugs += float(data.get("bugs") or 0)
    if not total:
        return {}
    return {"lead_p50": round(lead / total, 1), "bug_ratio": round(bugs / total, 3), "stories": total}


def _lead_p50(d: CollectorDB, scope: Scope) -> float | None:
    where, params = scope.where(months=scope.period)
    values = [
        float(r["lead_hours"])
        for r in d.query(
            f"SELECT lead_hours FROM fact_story WHERE done_at IS NOT NULL AND lead_hours IS NOT NULL AND {where}",
            tuple(params),
        )
    ]
    if not values:
        return None
    from nexo.kernel.finops.insights import percentile

    return round(percentile(values, 50), 1)


def _trend(d: CollectorDB, scope: Scope, months: int = 6) -> tuple[list[tuple[str, float | None]], ...]:
    labels = [_month_add(scope.current, -i) for i in range(months - 1, -1, -1)]
    cost: list[tuple[str, float | None]] = [(m, _cost(d, scope, m)) for m in labels]
    fpy: list[tuple[str, float | None]] = [(m, _story_kpis(d, scope, (m, m))["fpy"]) for m in labels]
    return cost, fpy
