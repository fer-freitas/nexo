"""Página pessoal: o que é de cada pessoa (histórias, custo, orçamento)."""

from __future__ import annotations

from typing import Any

from nexo.central.common import pid_of
from nexo.central.db import CollectorDB
from nexo.central.metrics.pages.finops import _acu_brl
from nexo.central.metrics.scope import WAITING, Scope
from nexo.central.metrics.widgets import _r, attention, headline, kpi, month_name, page, table
from nexo.kernel.finops.money import brl


def me(d: CollectorDB, scope: Scope) -> dict[str, Any]:
    principal = scope.principal
    cur = scope.current
    pid = pid_of(principal.dev)
    limit = d.one("SELECT limit_brl FROM budgets WHERE dev = ? AND month = ? AND tool = ''", (principal.dev, cur))
    by_tool = d.query(
        "SELECT tool, COALESCE(SUM(usage_brl), 0) AS brl, COALESCE(SUM(acu), 0) AS acu FROM fact_step"
        " WHERE dev_pid = ? AND month = ? GROUP BY tool ORDER BY tool",
        (pid, cur),
    )
    acu_brl = _acu_brl()
    lic = d.one("SELECT monthly_brl, acu_included FROM licenses WHERE dev = ? AND tool = 'devin'", (principal.dev,))
    included = (
        float(lic["acu_included"])
        if lic and lic["acu_included"] is not None
        else (float(lic["monthly_brl"] or 0) / acu_brl if lic else None)
    )
    stories = d.query(
        "SELECT project, story, feature, state, cost_brl, cap_brl FROM fact_story WHERE dev_pid = ?"
        " AND (done_at IS NULL OR month BETWEEN ? AND ?) ORDER BY project, story",
        (pid, *scope.period),
    )
    blocks = d.query(
        "SELECT COALESCE(kind, '-') AS kind, COUNT(*) AS n FROM fact_event WHERE type = 'run.blocked'"
        " AND dev_pid = ? AND month BETWEEN ? AND ? GROUP BY COALESCE(kind, '-') ORDER BY 1",
        (pid, *scope.period),
    )
    used = sum(float(r["brl"]) for r in by_tool)
    acu = sum(float(r["acu"]) for r in by_tool)
    waiting = [s for s in stories if s["state"] == WAITING]
    stuck = [s for s in stories if s["state"] in ("Escalado", "Bloqueado")]
    cap = float(limit["limit_brl"]) if limit and limit["limit_brl"] else None
    ratio = 100 * used / cap if cap else None
    spend = (
        ("neutral", "sem teto definido")
        if ratio is None
        else ("good", f"{ratio:.0f}% do teto")
        if ratio < 70
        else ("warn", f"{ratio:.0f}% do teto")
        if ratio < 100
        else ("bad", "teto atingido")
    )
    questions = [
        headline(
            "used",
            f"Quanto usei em {month_name(cur)}?",
            _r(used),
            "brl",
            f"de um teto de {brl(cap)}" if cap else "medido pelo Nexo nas suas execuções",
            *spend,
            bar=ratio,
        ),
        headline(
            "waiting",
            "O que espera por mim?",
            len(waiting) + len(stuck),
            "int",
            "histórias aguardando sua decisão ou paradas",
            *(("warn", "decida no console") if waiting or stuck else ("good", "nada esperando")),
        ),
    ]
    if included:
        questions.append(
            headline(
                "acu",
                "Quanto do Devin já usei?",
                _r(acu),
                "num",
                f"de {_r(included)} ACUs inclusos na licença",
                bar=100 * acu / included,
            )
        )
    todo = [
        attention(
            f"{s['story']} aguarda sua aprovação",
            s["feature"] or "portão humano",
            f"nexo approve {s['story']}",
            "warn",
        )
        for s in waiting
    ] + [
        attention(f"{s['story']} parou ({s['state']})", s["feature"] or "", f"nexo resume {s['story']}", "bad")
        for s in stuck
    ]
    return page(
        "me",
        "Meu painel",
        scope,
        headline=questions,
        attention=todo,
        kpis=[
            kpi("budget", f"Meu orçamento {cur}", _r(limit["limit_brl"]) if limit else None, "brl"),
            kpi("used", "Meu uso no mês", _r(used), "brl"),
            kpi("acu_used", "Meus ACUs usados", _r(acu), "num"),
            kpi("acu_included", "ACUs inclusos", _r(included), "num"),
            kpi("pending", "Minhas aprovações pendentes", sum(1 for s in stories if s["state"] == WAITING), "int"),
        ],
        tables=[
            table(
                "tools",
                "Meu uso por ferramenta",
                ["ferramenta", "uso R$", "ACU"],
                [[r["tool"], _r(r["brl"]), _r(r["acu"])] for r in by_tool],
            ),
            table(
                "stories",
                "Minhas histórias e custos",
                ["projeto", "história", "feature", "estado", "custo R$", "teto R$"],
                [
                    [s["project"], s["story"], s["feature"], s["state"], _r(s["cost_brl"]), _r(s["cap_brl"])]
                    for s in stories
                ],
            ),
            table("blocks", "Meus bloqueios", ["motivo", "ocorrências"], [[b["kind"], int(b["n"])] for b in blocks]),
        ],
    )
