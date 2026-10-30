"""Página do squad: fluxo das histórias, pessoas e gargalos."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from nexo.central.common import names, pid_of
from nexo.central.db import CollectorDB
from nexo.central.metrics.pages.finops import _budget_people
from nexo.central.metrics.queries import _story_kpis
from nexo.central.metrics.scope import DONE, WAITING, Scope, devs_in
from nexo.central.metrics.widgets import _drill, _pct, _r, kpi, page, series, table


def squad(d: CollectorDB, scope: Scope, actor: str) -> dict[str, Any]:
    where, params = scope.where()
    period_where = f"({where}) AND (done_at IS NULL OR month BETWEEN ? AND ?)"
    pparams = (*params, *scope.period)
    states = d.query(
        f"SELECT state, COUNT(*) AS n FROM fact_story WHERE {period_where} GROUP BY state ORDER BY state",
        pparams,
    )
    active = d.query(
        "SELECT project, story, feature, state, cost_brl, cap_brl, attempts, bugs FROM fact_story"
        f" WHERE {where} AND state <> ? ORDER BY project, story",
        (*params, DONE),
    )
    stories = _story_kpis(d, scope, scope.period)
    ewhere, eparams = scope.where(months=scope.period)
    blocks = d.query(
        f"SELECT COALESCE(kind, '-') AS kind, COUNT(*) AS n FROM fact_event WHERE type = 'run.blocked' AND {ewhere}"
        " GROUP BY COALESCE(kind, '-') ORDER BY 1",
        tuple(eparams),
    )
    rework = (
        d.one(
            "SELECT COALESCE(SUM(bugs), 0) AS bugs, AVG(attempts) AS attempts, COALESCE(SUM(spec_returns), 0) AS sr,"
            f" SUM(CASE WHEN escalated = 1 THEN 1 ELSE 0 END) AS esc FROM fact_story WHERE {period_where}",
            pparams,
        )
        or {}
    )
    features = d.query(
        "SELECT project, feature, COUNT(*) AS n, COALESCE(SUM(estimate_brl), 0) AS est,"
        f" COALESCE(SUM(cost_brl), 0) AS real FROM fact_story WHERE done_at IS NOT NULL AND {where}"
        " AND month BETWEEN ? AND ? GROUP BY project, feature ORDER BY project, feature",
        pparams,
    )
    weekly: dict[str, int] = {}
    for r in d.query(
        f"SELECT done_at FROM fact_story WHERE done_at IS NOT NULL AND {where} AND month BETWEEN ? AND ?",
        pparams,
    ):
        try:
            iso = datetime.fromisoformat(str(r["done_at"])).isocalendar()
        except ValueError:
            continue
        key = f"{iso[0]}-S{iso[1]:02d}"
        weekly[key] = weekly.get(key, 0) + 1
    tables = [
        table("states", "Fluxo por estado", ["estado", "histórias"], [[r["state"], int(r["n"])] for r in states]),
        table(
            "active",
            "Histórias em andamento: custo ao vivo e teto",
            ["projeto", "história", "feature", "estado", "custo R$", "teto R$", "% do teto", "tentativas", "bugs"],
            [
                [
                    r["project"],
                    r["story"],
                    r["feature"],
                    r["state"],
                    _r(r["cost_brl"]),
                    _r(r["cap_brl"]),
                    _pct(float(r["cost_brl"] or 0), float(r["cap_brl"] or 0)),
                    r["attempts"],
                    r["bugs"],
                ]
                for r in active
            ],
        ),
        table("blocks", "Bloqueios por motivo", ["motivo", "ocorrências"], [[r["kind"], int(r["n"])] for r in blocks]),
        table(
            "estimates",
            "Estimado × real por feature",
            ["projeto", "feature", "histórias", "estimado R$", "real R$", "erro %"],
            [
                [
                    r["project"],
                    r["feature"],
                    int(r["n"]),
                    _r(r["est"]),
                    _r(r["real"]),
                    _pct(float(r["real"]) - float(r["est"]), float(r["est"])),
                ]
                for r in features
            ],
        ),
    ]
    if scope.squads is not None and len(scope.squads) == 1:  # autonomia conquistada do squad
        from nexo.central.autonomy import summary

        (only_squad,) = tuple(scope.squads)
        tables.append(
            table(
                "autonomy",
                f"Autonomia do squad {only_squad} por área",
                ["área", "nível", "próximo", "elegível", "histórias", "FPY", "escapes (30 d)", "pendências"],
                [
                    [
                        a["area"],
                        a["level"],
                        a["next_level"] or "—",
                        "sim" if a["eligible"] else "não",
                        a["stories"],
                        _pct(a["fpy"] or 0, 1) if a["fpy"] is not None else None,
                        a["escapes"],
                        "; ".join(a["reasons"]),
                    ]
                    for a in summary(d, only_squad)
                ],
            )
        )
    tables += _squad_people(d, scope, actor)
    from nexo.central.metrics.pages import answers

    capped = [(str(r["story"]), float(r["cost_brl"] or 0), float(r["cap_brl"] or 0)) for r in active]
    top = max(blocks, key=lambda r: int(r["n"]), default=None)
    questions, todo = answers.squad(
        stories=stories,
        in_progress=len(active),
        estimates=[(float(r["est"]), float(r["real"])) for r in features],
        over_cap=[s for s, cost, cap in capped if cap and cost > cap],
        near_cap=[s for s, cost, cap in capped if cap and 0.8 * cap <= cost <= cap],
        escalated=int(rework.get("esc") or 0),
        top_block=(str(top["kind"]), int(top["n"])) if top else None,
    )
    return page(
        "squad",
        "Squads",
        scope,
        headline=questions,
        attention=todo,
        kpis=[
            kpi("done", "Concluídas no período", stories["done"], "int", _drill("story", done="1")),
            kpi("in_progress", "Em andamento", len(active), "int", _drill("story", done="0")),
            kpi(
                "waiting",
                "Espera média por humano (h)",
                stories["waiting_hours"],
                "num",
                _drill("story", state=WAITING),
            ),
            kpi("fpy", "FPY (sem bug)", stories["fpy"], "pct", _drill("story", done="1", bugs="0")),
            kpi("bugs", "Bugs (retrabalho)", int(rework.get("bugs") or 0), "int", _drill("event", type="bug.created")),
            kpi("attempts", "Tentativas médias", _r(rework.get("attempts")), "num"),
            kpi("spec_returns", "Retornos de spec", int(rework.get("sr") or 0), "int"),
            kpi("escalated", "Escaladas", int(rework.get("esc") or 0), "int", _drill("story", escalated="1")),
        ],
        tables=tables,
        charts=[series("weekly_done", "Histórias concluídas por semana", sorted(weekly.items()), "int")],
    )


def _squad_people(d: CollectorDB, scope: Scope, actor: str) -> list[dict[str, Any]]:
    """Tech lead: orçamento, bloqueios e portões pendentes por pessoa do squad (ordem alfabética, sem ranking)."""
    out = _budget_people(d, scope, actor, "squad")
    allowed = scope.principal.individual_squads("blocks")
    if not allowed:
        return out
    visible = allowed if scope.squads is None else allowed & scope.squads
    devs = sorted(devs_in(d, visible) or set())
    if not devs:
        return out
    pids = {pid_of(dev): dev for dev in devs}
    marks = ", ".join("?" * len(pids))
    blocks = {
        str(r["dev_pid"]): int(r["n"])
        for r in d.query(
            f"SELECT dev_pid, COUNT(*) AS n FROM fact_event WHERE type = 'run.blocked' AND month BETWEEN ? AND ?"
            f" AND dev_pid IN ({marks}) GROUP BY dev_pid",
            (*scope.period, *pids),
        )
    }
    pending = {
        str(r["dev_pid"]): int(r["n"])
        for r in d.query(
            f"SELECT dev_pid, COUNT(*) AS n FROM fact_story WHERE state = ? AND dev_pid IN ({marks}) GROUP BY dev_pid",
            (WAITING, *pids),
        )
    }
    who = names(actor, "blocks", list(pids))
    rows = [
        [who.get(pid, dev), blocks.get(pid, 0), pending.get(pid, 0)]
        for pid, dev in sorted(pids.items(), key=lambda x: x[1])
    ]
    out.append(
        table(
            "people_blocks",
            "Bloqueios e portões pendentes por pessoa (ordem alfabética)",
            ["pessoa", "bloqueios", "portões pendentes"],
            rows,
            individual="blocks",
        )
    )
    return out
