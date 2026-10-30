"""Painel HTML e login por SSO: páginas, drill, insights, flags, login, retorno do SSO e saída."""

from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from nexo.central import auth, dashboard, flags, metrics
from nexo.central import insights as insight_engine
from nexo.central.app.admin import FlagIn, put_flag
from nexo.central.app.api import api_drill
from nexo.central.app.core import _dash_principal, _dashboard_on, _filters, _form, _html, app
from nexo.central.common import db
from nexo.central.visibility import PAGES, require_page


@app.get("/dash", response_model=None)
def dash_home(request: Request) -> Response:
    principal = _dash_principal(request)
    if isinstance(principal, RedirectResponse):
        return principal
    first = next(p for p in PAGES if principal.can_page(p))
    return RedirectResponse(f"/dash/{first}", status_code=303)


@app.get("/dash/drill", response_model=None)
def dash_drill(request: Request, source: str) -> Response:
    principal = _dash_principal(request)
    if isinstance(principal, RedirectResponse):
        return principal
    return _html(dashboard.render_drill(api_drill(source, request), principal))


@app.get("/dash/insights", response_model=None)
def dash_insights(request: Request, state: str = "aberto") -> Response:
    principal = _dash_principal(request)
    if isinstance(principal, RedirectResponse):
        return principal
    session = auth.session_of(request)
    items = insight_engine.list_for(db(), principal, state or None)
    return _html(dashboard.render_insights(items, principal, str(session["csrf"]) if session else None, state))


@app.post("/dash/insights/{insight_id}", response_model=None)
async def dash_insight_state(insight_id: str, request: Request) -> Response:
    principal = _dash_principal(request)
    if isinstance(principal, RedirectResponse):
        return principal
    form = await _form(request)
    auth.check_csrf(request, form.get("csrf", ""))
    insight_engine.set_state(db(), principal, insight_id, form.get("state", ""), form.get("reason", ""))
    return RedirectResponse("/dash/insights", status_code=303)


@app.get("/dash/{name}", response_model=None)
def dash_page(name: str, request: Request) -> Response:
    principal = _dash_principal(request)
    if isinstance(principal, RedirectResponse):
        return principal
    require_page(principal, name)
    _dashboard_on(name)
    if name == "flags":
        session = auth.session_of(request)
        return _html(
            dashboard.render_flags(
                flags.snapshot(db()),
                flags.history(db(), 20),
                principal,
                str(session["csrf"]) if session else None,
                area=request.query_params.get("area", ""),
                sort=request.query_params.get("sort", ""),
                desc=request.query_params.get("dir") == "desc",
            )
        )
    scope = metrics.make_scope(db(), principal, _filters(request))
    data = metrics.build(db(), name, scope, principal.dev)
    names = metrics.project_names(db(), principal)
    return _html(dashboard.render_page(data, principal, metrics.options(db(), principal), names))


@app.post("/dash/flags", response_model=None)
async def dash_flags_submit(request: Request) -> Response:
    principal = _dash_principal(request)
    if isinstance(principal, RedirectResponse):
        return principal
    form = await _form(request)
    auth.check_csrf(request, form.get("csrf", ""))
    value = {"on": True, "off": False, "inherit": None}.get(form.get("value", ""))
    put_flag(
        form.get("key", ""),
        FlagIn(
            value=value,
            squad=form.get("squad") or None,
            project=form.get("project") or None,
            reason=form.get("reason", ""),
        ),
        request,
    )
    return RedirectResponse("/dash/flags", status_code=303)


@app.get("/login", response_model=None)
def login_page(next: str = "/dash") -> Response:
    if auth.oidc_enabled():
        return RedirectResponse(auth.oidc_start(next), status_code=303)
    return _html(dashboard.render_login(token_login=auth.token_login_enabled(), next_url=next))


def _start_session(dev: str, role: str | None, via: str, nxt: str) -> Response:
    sid, _ = auth.create_session(dev, role, via)
    target = nxt if nxt.startswith("/") and not nxt.startswith("//") else "/dash"
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(
        auth.SESSION_COOKIE,
        sid,
        max_age=auth.SESSION_TTL,
        httponly=True,
        secure=auth.secure_cookie(),
        samesite="lax",
        path="/",
    )
    return response


@app.post("/login", response_model=None)
async def login_submit(request: Request) -> Response:
    if not auth.token_login_enabled():
        raise HTTPException(404, "login por token desligado: use o SSO")
    form = await _form(request)
    key = request.client.host if request.client else "local"
    if auth.login_blocked(key):
        return _html(dashboard.render_login("muitas tentativas: aguarde", next_url=form.get("next", "/dash")), 429)
    token = form.get("token", "").strip()
    ident = auth.from_invite(token) or auth.from_bearer(token)
    if ident is None or ident["device_id"]:
        auth.login_failed(key)
        return _html(dashboard.render_login("token inválido", next_url=form.get("next", "/dash")), 401)
    auth.login_ok(key)
    return _start_session(ident["dev"], ident["role"] or None, "token", form.get("next", "/dash"))


@app.get("/auth/callback", response_model=None)
def auth_callback(code: str, state: str) -> Response:
    dev, nxt = auth.oidc_finish(code, state)
    return _start_session(dev, None, "sso", nxt)


@app.get("/logout", response_model=None)
def logout(request: Request) -> Response:
    auth.end_session(request)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return response
