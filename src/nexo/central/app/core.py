"""Aplicação FastAPI do coletor: cabeçalhos de segurança, autorização das rotas e utilitários das páginas HTML."""

from __future__ import annotations

import secrets
import time
import urllib.parse
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from nexo.central import auth, flags, metrics, tracing
from nexo.central.auth import _hash
from nexo.central.common import db
from nexo.central.visibility import Principal

ACCESS_TTL = 24 * 3600


REFRESH_TTL = 30 * 24 * 3600


CODE_TTL = 600


INSIGHTS_EVERY = 300  # RF-120: reavaliação após consolidação, no máximo a cada 5 min


app = FastAPI(title="nexo-collector", version="2")


@app.middleware("http")
async def security_headers(request: Request, call_next: Any) -> Response:
    started = time.time_ns()
    response: Response = await call_next(request)
    route = request.scope.get("route")
    tracing.record(request.method, str(getattr(route, "path", "desconhecida")), response.status_code, started)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'"
    )
    return response


def _require(ident: dict[str, str], *roles: str) -> None:
    if ident["role"] not in roles:
        raise HTTPException(403, f"exige papel: {', '.join(roles)}")


def _issue(device_id: str, kind: str, ttl: int) -> str:
    token = secrets.token_urlsafe(32)
    db().execute(
        "INSERT INTO tokens (token_hash, device_id, kind, expires_at, revoked) VALUES (?, ?, ?, ?, 0)",
        (_hash(token), device_id, kind, time.time() + ttl),
    )
    return token


def _filters(request: Request) -> metrics.Filters:
    params = dict(request.query_params)
    squads = request.query_params.getlist("squad")  # caixas de seleção repetem o nome
    if squads:
        params["squad"] = ",".join(squads)
    return metrics.Filters.parse(params)


def _dashboard_on(name: str) -> None:
    if name != "flags" and not flags.enabled(db(), "dashboard"):
        raise HTTPException(503, "dashboard desligado por feature flag (dashboard)")


def _html(body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(body, status_code=status)


def _dash_principal(request: Request) -> Principal | RedirectResponse:
    try:
        return auth.principal(request)
    except HTTPException:
        nxt = urllib.parse.quote(str(request.url.path) + ("?" + request.url.query if request.url.query else ""))
        return RedirectResponse(f"/login?next={nxt}", status_code=303)


async def _form(request: Request) -> dict[str, str]:
    raw = (await request.body()).decode("utf-8", errors="replace")
    return {k: v[0] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}
