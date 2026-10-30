"""Custo por centro de custo: o relatório mensal do FinOps dividido pelos centros de custo da empresa.

Centro de custo vem do diretório da org (`PUT /v2/admin/directory`): o do squad ou, se vazio, o da diretoria.
Para cada centro de custo, no mês: uso pelo Nexo (passos), uso de IA fora do Nexo e licenças (assinatura
de cada pessoa rateada igualmente entre os squads de que ela é membro). O que não tem centro de custo aparece
numa linha própria, para o FinOps corrigir o diretório. Tudo determinístico, a partir dos fatos do coletor.
"""

from __future__ import annotations

from typing import Any

from nexo.central.db import CollectorDB

NO_CC = "(sem centro de custo)"


def squad_centers(d: CollectorDB) -> dict[str, str]:
    rows = d.query(
        "SELECT s.squad, COALESCE(NULLIF(s.cost_center, ''), NULLIF(dr.cost_center, ''), '') AS cc"
        " FROM dim_squad s LEFT JOIN dim_directorate dr ON dr.directorate = s.directorate"
    )
    return {str(r["squad"]): str(r["cc"] or "") for r in rows}


def compute(d: CollectorDB, month: str, squads: set[str] | None = None) -> list[dict[str, Any]]:
    """Linhas por centro de custo no mês; `squads` limita ao que a pessoa pode ver (None = todos)."""
    centers = squad_centers(d)

    def visible(squad: str) -> bool:
        return squads is None or squad in squads

    def cc_of(squad: str | None) -> str:
        return centers.get(str(squad or ""), "") or NO_CC

    out: dict[str, dict[str, Any]] = {}

    def row(cc: str) -> dict[str, Any]:
        return out.setdefault(
            cc, {"cost_center": cc, "squads": set(), "nexo_brl": 0.0, "outside_brl": 0.0, "licenses_brl": 0.0}
        )

    for r in d.query(
        "SELECT COALESCE(squad, '') AS squad, COALESCE(SUM(usage_brl), 0) AS brl FROM fact_step WHERE month = ?"
        " GROUP BY COALESCE(squad, '')",
        (month,),
    ):
        if not visible(str(r["squad"])):
            continue
        entry = row(cc_of(r["squad"]))
        entry["nexo_brl"] += float(r["brl"])
        if r["squad"]:
            entry["squads"].add(str(r["squad"]))
    for r in d.query(
        "SELECT COALESCE(squad, '') AS squad, COALESCE(SUM(usage_brl), 0) AS brl FROM fact_external_usage"
        " WHERE month = ? GROUP BY COALESCE(squad, '')",
        (month,),
    ):
        if not visible(str(r["squad"])):
            continue
        entry = row(cc_of(r["squad"]))
        entry["outside_brl"] += float(r["brl"])
        if r["squad"]:
            entry["squads"].add(str(r["squad"]))
    memberships: dict[str, list[str]] = {}
    for m in d.query("SELECT dev, squad FROM org_members WHERE relation = 'member'"):
        memberships.setdefault(str(m["dev"]), []).append(str(m["squad"]))
    for lic in d.query("SELECT dev, monthly_brl FROM licenses"):
        owners = memberships.get(str(lic["dev"])) or [""]
        share = float(lic["monthly_brl"] or 0) / len(owners)
        for sq in owners:
            if not visible(sq):
                continue
            entry = row(cc_of(sq))
            entry["licenses_brl"] += share
            if sq:
                entry["squads"].add(sq)
    rows = []
    for entry in out.values():
        total = entry["nexo_brl"] + entry["outside_brl"] + entry["licenses_brl"]
        rows.append(
            {
                "cost_center": entry["cost_center"],
                "squads": sorted(entry["squads"]),
                "nexo_brl": round(entry["nexo_brl"], 2),
                "outside_brl": round(entry["outside_brl"], 2),
                "licenses_brl": round(entry["licenses_brl"], 2),
                "total_brl": round(total, 2),
            }
        )
    rows.sort(key=lambda r: (r["cost_center"] == NO_CC, -r["total_brl"], r["cost_center"]))
    return rows


CSV_COLUMNS = ("mes", "centro_de_custo", "squads", "uso_nexo_brl", "uso_fora_brl", "licencas_brl", "total_brl")


def csv_rows(rows: list[dict[str, Any]], month: str) -> list[list[Any]]:
    return [
        [
            month,
            r["cost_center"],
            ",".join(r["squads"]),
            r["nexo_brl"],
            r["outside_brl"],
            r["licenses_brl"],
            r["total_brl"],
        ]
        for r in rows
    ]
