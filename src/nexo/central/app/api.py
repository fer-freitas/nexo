"""API de leitura: resumo, páginas e drill do painel, insights e export para BI."""

from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from nexo.central import auth, flags, metrics
from nexo.central import insights as insight_engine
from nexo.central.app.core import _dashboard_on, _filters, _require, app
from nexo.central.auth import identity
from nexo.central.common import audit, db
from nexo.central.visibility import Principal, require_export, require_page


@app.get("/v1/summary")
def summary(ident: dict[str, str] = Depends(identity)) -> dict[str, Any]:  # noqa: B008
    _require(ident, "manager", "admin")
    rows = db().query("SELECT type, COUNT(*) AS n FROM raw_events GROUP BY type")
    return {"events": {r["type"]: r["n"] for r in rows}}


@app.get("/v2/pages/{name}")
def api_page(name: str, request: Request) -> dict[str, Any]:
    principal = auth.principal(request)
    require_page(principal, name)
    _dashboard_on(name)
    if name == "insights":
        return {"page": "insights", "items": insight_engine.list_for(db(), principal)}
    scope = metrics.make_scope(db(), principal, _filters(request))
    return metrics.build(db(), name, scope, principal.dev)


@app.get("/v2/drill")
def api_drill(source: str, request: Request) -> dict[str, Any]:
    principal = auth.principal(request)
    if not any(principal.can_page(p) for p in ("executive", "finops", "squad", "quality", "routing", "brain")):
        raise HTTPException(403, "seu perfil não acessa o drill-down")
    scope = metrics.make_scope(db(), principal, _filters(request))
    return metrics.drill(db(), scope, source, dict(request.query_params))


@app.get("/v2/insights")
def api_insights(request: Request, state: str = "") -> dict[str, Any]:
    principal = auth.principal(request)
    return {"items": insight_engine.list_for(db(), principal, state or None)}


class StateIn(BaseModel):
    state: str
    reason: str = ""


@app.post("/v2/insights/{insight_id}/state")
def api_insight_state(insight_id: str, body: StateIn, request: Request) -> dict[str, Any]:
    principal = auth.principal(request)
    if principal.via == "session":
        auth.check_csrf(request, request.headers.get("x-csrf-token", ""))
    return insight_engine.set_state(db(), principal, insight_id, body.state, body.reason)


EXPORTS = {
    "fact_step": ("bi_fact_step", "month"),
    "fact_story": ("bi_fact_story", "month"),
    "fact_gate": ("bi_fact_gate", "month"),
    "fact_quality": ("bi_fact_quality", "month"),
    "dim_squad": ("bi_dim_squad", None),
}


def _export_rows(principal: Principal, fact: str, request: Request) -> list[dict[str, Any]]:
    if fact not in EXPORTS:
        raise HTTPException(404, f"fato desconhecido: {fact}")
    view, month_col = EXPORTS[fact]
    scope = metrics.make_scope(db(), principal, _filters(request))
    if month_col is None:
        rows = db().query(f"SELECT * FROM {view} ORDER BY squad")
        return [r for r in rows if principal.sees_squad(r["squad"])]
    where, params = scope.where(months=scope.period, steps=fact == "fact_step")
    return db().query(f"SELECT * FROM {view} WHERE {where}", tuple(params))


FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: Any) -> Any:
    """Texto que começa como fórmula (=, +, -, @) ganha apóstrofo: a planilha mostra o texto em vez de executar."""
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


@app.get("/v2/export/{fact}")
def export(fact: str, request: Request, format: str = "csv") -> Response:
    """Export para BI: mesmas regras de visibilidade; nunca inclui identificação de pessoa."""
    principal = auth.principal(request)
    require_export(principal)
    if not flags.enabled(db(), "bi_export"):
        raise HTTPException(503, "export para BI desligado por feature flag (bi_export)")
    rows = _export_rows(principal, fact, request)
    audit(principal.dev, "export", {"fact": fact, "format": format, "rows": len(rows)})
    columns = list(rows[0]) if rows else []
    if format == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        # células neutralizadas contra injeção de fórmula na planilha/BI (valores vêm da telemetria dos devs)
        writer.writerow(
            [_cell(c) for c in columns]
        )  # nosemgrep: python.django.security.injection.csv-writer-injection.csv-writer-injection
        writer.writerows(
            [[_cell(r[c]) for c in columns] for r in rows]
        )  # nosemgrep: python.django.security.injection.csv-writer-injection.csv-writer-injection
        return Response(
            buf.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{fact}.csv"'},
        )
    if format == "parquet":
        try:
            import pyarrow as pa  # extra `collector`
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise HTTPException(415, "Parquet requer pyarrow (extra nexo[collector])") from exc
        table = pa.Table.from_pylist(rows) if rows else pa.table({})
        sink = io.BytesIO()
        pq.write_table(table, sink)
        return Response(
            sink.getvalue(),
            media_type="application/vnd.apache.parquet",
            headers={"Content-Disposition": f'attachment; filename="{fact}.parquet"'},
        )
    raise HTTPException(400, "formato: csv ou parquet")
