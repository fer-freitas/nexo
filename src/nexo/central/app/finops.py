"""Rotas de FinOps: orçamento por pessoa, medição, chargeback, faturas e relatório."""

from __future__ import annotations

import csv
import io
import re
import time
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from nexo.central import auth, distribution
from nexo.central.app.api import _cell
from nexo.central.app.core import _require, app
from nexo.central.auth import identity
from nexo.central.common import audit, db, pseudonym
from nexo.central.visibility import require_page


class BudgetIn(BaseModel):
    month: str
    limit_brl: float
    tool: str = ""


@app.get("/v2/budgets/me")
def my_budget(month: str, ident: dict[str, str] = Depends(identity)) -> dict[str, Any]:  # noqa: B008
    """Limite do próprio dev pela identidade do token (conta corporativa aprovada no login do dispositivo)."""
    rows = db().query("SELECT tool, limit_brl FROM budgets WHERE dev = ? AND month = ?", (ident["dev"], month))
    return {"month": month, "limits": {r["tool"] or "*": r["limit_brl"] for r in rows}}


@app.get("/v1/budgets/{dev}")
def get_budget(dev: str, month: str, ident: dict[str, str] = Depends(identity)) -> dict[str, Any]:  # noqa: B008
    if ident["role"] not in ("manager", "admin") and ident["dev"] != dev:
        raise HTTPException(403, "sem acesso ao orçamento de outro dev")
    rows = db().query("SELECT tool, limit_brl FROM budgets WHERE dev = ? AND month = ?", (dev, month))
    return {"dev": dev, "month": month, "limits": {r["tool"] or "*": r["limit_brl"] for r in rows}}


@app.put("/v1/budgets/{dev}")
def put_budget(dev: str, body: BudgetIn, ident: dict[str, str] = Depends(identity)) -> dict[str, str]:  # noqa: B008
    _require(ident, "manager", "admin")
    d = db()
    d.execute("DELETE FROM budgets WHERE dev = ? AND month = ? AND tool = ?", (dev, body.month, body.tool))
    d.execute(
        "INSERT INTO budgets (dev, month, tool, limit_brl, set_by) VALUES (?,?,?,?,?)",
        (dev, body.month, body.tool, body.limit_brl, ident["dev"]),
    )
    audit(ident["dev"], "budget.set", {"dev": pseudonym(dev), "month": body.month, "limit_brl": body.limit_brl})
    return {"status": "ok"}


class InvoiceIn(BaseModel):
    tool: str
    month: str
    amount_brl: float


def _measured(month: str) -> dict[str, float]:
    """Consumo medido do mês por ferramenta: passos do Nexo + uso direto das ferramentas, para conciliar."""
    out: dict[str, float] = {}
    for table in ("fact_step", "fact_external_usage"):
        rows = db().query(f"SELECT tool, SUM(usage_brl) AS total FROM {table} WHERE month = ? GROUP BY tool", (month,))
        for r in rows:
            out[str(r["tool"])] = round(out.get(str(r["tool"]), 0.0) + float(r["total"] or 0), 2)
    return out


@app.get("/v2/finops/measured")
def measured(month: str, ident: dict[str, str] = Depends(identity)) -> dict[str, Any]:  # noqa: B008
    _require(ident, "manager", "finops", "admin")
    return {"month": month, "usage_brl": _measured(month)}


@app.get("/v2/finops/chargeback")
def chargeback_report(request: Request, month: str, format: str = "json") -> Any:
    """custo do mês por centro de custo (uso pelo Nexo, uso fora dele e licenças rateadas por squad)."""
    from nexo.central import chargeback

    principal = auth.principal(request)
    require_page(principal, "finops")
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(400, "mês no formato AAAA-MM")
    rows = chargeback.compute(db(), month, principal.squads)
    audit(principal.dev, "finops.chargeback", {"month": month, "format": format, "rows": len(rows)})
    if format == "json":
        return {"month": month, "rows": rows, "total_brl": round(sum(r["total_brl"] for r in rows), 2)}
    if format != "csv":
        raise HTTPException(400, "formato: json ou csv")
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(chargeback.CSV_COLUMNS)
    # centro de custo e squad vêm do diretório; neutralizados contra fórmula na planilha mesmo assim
    writer.writerows(
        [[_cell(c) for c in r] for r in chargeback.csv_rows(rows, month)]
    )  # nosemgrep: python.django.security.injection.csv-writer-injection.csv-writer-injection
    return Response(
        buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="chargeback-{month}.csv"'},
    )


@app.post("/v2/invoices")
def invoice(body: InvoiceIn, ident: dict[str, str] = Depends(identity)) -> dict[str, Any]:  # noqa: B008
    _require(ident, "finops", "admin", "manager")
    value = _measured(body.month).get(body.tool, 0.0)
    diff = round(100 * (value - body.amount_brl) / body.amount_brl, 1) if body.amount_brl else 0.0
    d = db()
    d.execute("DELETE FROM invoices WHERE tool = ? AND month = ?", (body.tool, body.month))
    d.execute(
        "INSERT INTO invoices (tool, month, amount_brl, measured_brl, diff_pct, at) VALUES (?,?,?,?,?,?)",
        (body.tool, body.month, body.amount_brl, value, diff, time.time()),
    )
    return {
        "tool": body.tool,
        "month": body.month,
        "amount_brl": body.amount_brl,
        "measured_brl": value,
        "diff_pct": diff,
        "divergent": abs(diff) > 5,
    }


@app.get("/v2/reports/finops")
def finops_report(request: Request, month: str = "") -> dict[str, Any]:
    """Relatório mensal de FinOps (só agregado)."""
    principal = auth.principal(request)
    require_page(principal, "finops")
    return distribution.monthly_finops(db(), month or None, deliver=False)
