"""Página FinOps: custo de uso e alocado, chargeback, uso fora do Nexo, cache e orçamento por pessoa."""

from __future__ import annotations

import os
from typing import Any

from nexo.central.common import names, pid_of
from nexo.central.db import CollectorDB
from nexo.central.metrics.scope import Scope, _day_fraction, devs_in
from nexo.central.metrics.widgets import _drill, _pct, _r, kpi, month_name, page, table
from nexo.kernel.finops.insights import linear_projection


def _chargeback(d: CollectorDB, scope: Scope, month: str) -> dict[str, Any]:
    """custo do mês por centro de custo (o do squad ou, se vazio, o da diretoria)."""
    from nexo.central import chargeback

    return table(
        "chargeback",
        f"Custo por centro de custo ({month})",
        ["centro de custo", "squads", "uso pelo Nexo R$", "uso fora do Nexo R$", "licenças R$", "total R$"],
        [
            [
                r["cost_center"],
                ", ".join(r["squads"]) or "—",
                r["nexo_brl"],
                r["outside_brl"],
                r["licenses_brl"],
                r["total_brl"],
            ]
            for r in chargeback.compute(d, month, scope.squads)
        ],
    )


def _outside_nexo(d: CollectorDB, scope: Scope, month: str, nexo_brl: dict[str, float]) -> dict[str, Any]:
    """IA usada direto nas ferramentas, fora do Nexo, e a parte do consumo total que passou pelo Nexo."""
    rows: list[dict[str, Any]] = []
    if not scope.filters.project:  # uso fora do Nexo não pertence a um projeto do Nexo
        where, params = scope.where(months=(month, month), stack=False)
        rows = d.query(
            "SELECT tool, COALESCE(SUM(sessions), 0) AS sessions, COALESCE(SUM(usage_brl), 0) AS brl,"
            " COALESCE(SUM(tokens_in), 0) + COALESCE(SUM(tokens_out), 0) AS tokens,"
            " COALESCE(SUM(premium_request), 0) AS premium, COALESCE(SUM(ai_credits), 0) AS credits,"
            " COALESCE(SUM(acu), 0) AS acu"
            f" FROM fact_external_usage WHERE {where} GROUP BY tool ORDER BY tool",
            tuple(params),
        )
    return table(
        "outside_nexo",
        f"IA usada fora do Nexo ({month})",
        ["ferramenta", "sessões", "uso R$", "tokens", "premium requests", "AI Credits", "ACU", "% pelo Nexo"],
        [
            [
                r["tool"],
                int(r["sessions"]),
                _r(r["brl"]),
                int(r["tokens"]),
                _r(r["premium"]),
                _r(r["credits"]),
                _r(r["acu"]),
                _pct(nexo_brl.get(str(r["tool"]), 0.0), nexo_brl.get(str(r["tool"]), 0.0) + float(r["brl"])),
            ]
            for r in rows
        ],
    )


def finops(d: CollectorDB, scope: Scope, actor: str) -> dict[str, Any]:
    cur = scope.current
    where, params = scope.where(months=(cur, cur), steps=True)
    by_tool = d.query(
        "SELECT tool, COUNT(*) AS steps, COALESCE(SUM(usage_brl), 0) AS brl, COALESCE(SUM(acu), 0) AS acu,"
        " COALESCE(SUM(premium_request), 0) AS premium, COALESCE(SUM(api_equiv_usd), 0) AS usd,"
        " COALESCE(SUM(tokens_in), 0) AS tokens_in, COALESCE(SUM(cache_read), 0) AS cache_read,"
        " COALESCE(SUM(cache_write), 0) AS cache_write, COALESCE(SUM(estimated), 0) AS estimated"
        f" FROM fact_step WHERE {where} GROUP BY tool ORDER BY tool",
        tuple(params),
    )
    by_squad = d.query(
        f"SELECT COALESCE(squad, '') AS squad, COALESCE(SUM(usage_brl), 0) AS brl FROM fact_step WHERE {where}"
        " GROUP BY COALESCE(squad, '') ORDER BY 1",
        tuple(params),
    )
    by_project = d.query(
        f"SELECT project, COALESCE(SUM(usage_brl), 0) AS brl FROM fact_step WHERE {where}"
        " GROUP BY project ORDER BY project",
        tuple(params),
    )
    day, days, is_current = _day_fraction(scope)
    devs = devs_in(d, scope.squads)
    licenses = [r for r in d.query("SELECT * FROM licenses ORDER BY tool, dev") if devs is None or r["dev"] in devs]
    alloc: dict[str, float] = {}
    acu_included = 0.0
    acu_brl = _acu_brl()
    for lic in licenses:
        alloc[lic["tool"]] = alloc.get(lic["tool"], 0.0) + float(lic["monthly_brl"] or 0)
        if lic["tool"] == "devin":
            included = lic["acu_included"]
            acu_included += float(included) if included is not None else float(lic["monthly_brl"] or 0) / acu_brl
    usage = {str(r["tool"]): float(r["brl"]) for r in by_tool}
    acu_used = sum(float(r["acu"]) for r in by_tool)
    usd = sum(float(r["usd"]) for r in by_tool)
    brl_equiv = sum(float(r["brl"]) for r in by_tool if float(r["usd"]) > 0)
    invoices = d.query("SELECT * FROM invoices WHERE month = ? ORDER BY tool", (cur,))
    tables = [
        table(
            "by_tool",
            f"Custo de uso × alocado por ferramenta ({cur})",
            ["ferramenta", "passos", "uso R$", "alocado R$", "utilização %", "ACU", "premium requests"],
            [
                [
                    r["tool"],
                    int(r["steps"]),
                    _r(r["brl"]),
                    _r(alloc.get(r["tool"], 0.0)),
                    _pct(float(r["brl"]), alloc.get(r["tool"], 0.0)),
                    _r(r["acu"]),
                    _r(r["premium"]),
                ]
                for r in by_tool
            ]
            + [[t, 0, 0.0, _r(v), 0.0, 0.0, 0.0] for t, v in sorted(alloc.items()) if t not in usage],
        ),
        table(
            "by_squad",
            "Uso e previsão por squad",
            ["squad", "uso R$", "previsão fim do mês R$"],
            [
                [
                    r["squad"] or "—",
                    _r(r["brl"]),
                    _r(linear_projection(float(r["brl"]), day, days)) if is_current else _r(r["brl"]),
                ]
                for r in by_squad
            ],
        ),
        table(
            "by_project",
            "Uso por projeto (hash)",
            ["projeto", "uso R$"],
            [[r["project"], _r(r["brl"])] for r in by_project],
        ),
        table(
            "invoices",
            "Conciliação com fatura",
            ["ferramenta", "fatura R$", "medido R$", "diferença %"],
            [[r["tool"], _r(r["amount_brl"]), _r(r["measured_brl"]), _r(r["diff_pct"], 1)] for r in invoices],
        ),
    ]
    outside = _outside_nexo(d, scope, cur, usage)
    tables.append(outside)
    if not scope.filters.project:  # licença e uso fora do Nexo não pertencem a um projeto
        tables.append(_chargeback(d, scope, cur))
    tables += _budget_people(d, scope, actor, "finops")
    if scope.principal.individual_squads("license") is None and licenses:
        who = names(actor, "license", [pid_of(str(lic["dev"])) for lic in licenses])
        used = _usage_by_dev_tool(d, cur)
        rows = []
        for lic in sorted(licenses, key=lambda x: (str(x["dev"]), str(x["tool"]))):
            pid = pid_of(str(lic["dev"]))
            spent = used.get((pid, str(lic["tool"])), 0.0)
            rows.append(
                [
                    who.get(pid, lic["dev"]),
                    lic["tool"],
                    _r(lic["monthly_brl"]),
                    _r(spent),
                    _pct(spent, float(lic["monthly_brl"] or 0)),
                ]
            )
        tables.append(
            table(
                "licenses",
                "Licença por pessoa (I-05, ordem alfabética)",
                ["pessoa", "ferramenta", "assinatura R$", "uso R$", "utilização %"],
                rows,
                individual="license",
            )
        )
    from nexo.central.metrics.pages import answers
    from nexo.central.metrics.pages.executive import _budget

    total = sum(usage.values())
    questions, todo = answers.finops(
        usage=_r(total) or 0.0,
        projection=_r(linear_projection(total, day, days)) if is_current else _r(total),
        budget=_budget(d, cur, devs),
        allocated=sum(alloc.values()),
        outside=sum(float(row[2] or 0) for row in outside["rows"]),
        cache_hit=_cache_hit(by_tool),
        acu_excess=max(0.0, acu_used - acu_included),
        invoices=invoices,
        month=month_name(cur),
    )
    return page(
        "finops",
        "Custos e orçamento",
        scope,
        headline=questions,
        attention=todo,
        kpis=[
            kpi("usage", f"Custo de uso {cur}", _r(sum(usage.values())), "brl", _drill("step", month=cur)),
            kpi("allocated", "Alocado (assinaturas)", _r(sum(alloc.values())), "brl"),
            kpi("acu_used", "ACU do Devin usados", _r(acu_used), "num", _drill("step", month=cur, tool="devin")),
            kpi("acu_included", "ACU inclusos", _r(acu_included), "num"),
            kpi("acu_excess", "ACU excedentes", _r(max(0.0, acu_used - acu_included)), "num"),
            kpi("fx", "Câmbio aplicado (R$/US$)", _r(brl_equiv / usd, 4) if usd else None, "num"),
            kpi("cache_hit", "Acerto do cache dos modelos", _cache_hit(by_tool), "pct"),
            kpi(
                "estimated_steps",
                "Passos com custo estimado (preço de tabela ou consumo estimado)",
                _r(sum(float(r["estimated"]) for r in by_tool)),
                "num",
            ),
        ],
        tables=tables,
    )


def _acu_brl() -> float:
    """R$ por ACU do contrato (`NX_COLLECTOR_ACU_BRL`); sem configurar, a tabela pública do Devin."""
    return float(os.environ.get("NX_COLLECTOR_ACU_BRL", "10.8"))


def _cache_hit(rows: list[dict[str, Any]]) -> float | None:
    """parte da entrada servida pelo cache do modelo (prompt com a parte fixa na frente acerta mais)."""
    from nexo.kernel.finops import tokens

    hit = tokens.cache_hit({k: sum(float(r[k]) for r in rows) for k in ("tokens_in", "cache_read", "cache_write")})
    return round(100 * hit, 1) if hit is not None else None


def _usage_by_dev_tool(d: CollectorDB, month: str) -> dict[tuple[str, str], float]:
    rows = d.query(
        "SELECT dev_pid, tool, COALESCE(SUM(usage_brl), 0) AS brl FROM fact_step WHERE month = ?"
        " GROUP BY dev_pid, tool",
        (month,),
    )
    return {(str(r["dev_pid"]), str(r["tool"])): float(r["brl"]) for r in rows}


def _budget_people(d: CollectorDB, scope: Scope, actor: str, page_name: str) -> list[dict[str, Any]]:
    """Orçamento por pessoa — só para quem a matriz permite (gestor/tech lead dos squads), ordem alfabética."""
    allowed = scope.principal.individual_squads("budget")
    if allowed is None or not allowed:
        return []
    visible = allowed if scope.squads is None else allowed & scope.squads
    devs = sorted(devs_in(d, visible) or set())
    if not devs:
        return []
    cur = scope.current
    day, days, is_current = _day_fraction(scope)
    limits = {
        str(r["dev"]): float(r["limit_brl"] or 0)
        for r in d.query("SELECT dev, limit_brl FROM budgets WHERE month = ? AND tool = ''", (cur,))
    }
    used = _usage_by_dev_tool(d, cur)
    who = names(actor, "budget", [pid_of(dev) for dev in devs])
    rows = []
    for dev in devs:
        pid = pid_of(dev)
        spent = sum(v for (p, _), v in used.items() if p == pid)
        rows.append(
            [
                who.get(pid, dev),
                _r(limits.get(dev)),
                _r(spent),
                _r(linear_projection(spent, day, days)) if is_current else _r(spent),
            ]
        )
    return [
        table(
            f"{page_name}_budget_people",
            "Orçamento por pessoa (ordem alfabética)",
            ["pessoa", "limite R$", "uso R$", "projeção R$"],
            rows,
            individual="budget",
        )
    ]
