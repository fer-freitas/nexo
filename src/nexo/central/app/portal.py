"""Telas do portal além do painel: pessoas e acessos, kill switch e configuração de insights."""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from nexo.central import auth, dashboard
from nexo.central.app.core import _dash_principal, _form, _html, app
from nexo.central.common import db
from nexo.central.visibility import ROLES

e = dashboard.e


@app.get("/health", response_model=None)
def health() -> dict[str, Any]:
    """Para o balanceador: responde se o banco responde."""
    db().one("SELECT 1 AS ok")
    return {"ok": True}


def _guard(request: Request, page: str) -> Any:
    principal = _dash_principal(request)
    if isinstance(principal, RedirectResponse):
        return principal
    if not principal.can_page(page):
        raise HTTPException(403, "sem acesso a esta página")
    return principal


def _csrf(request: Request) -> str:
    session = auth.session_of(request)
    return str(session["csrf"]) if session else ""


@app.get("/dash/people", response_model=None)
def people_page(request: Request, token: str = "") -> Response:
    principal = _guard(request, "people")
    if isinstance(principal, RedirectResponse):
        return principal
    rows = db().query("SELECT dev, role FROM org_people ORDER BY dev")
    invites = {str(r["dev"]): float(r["expires_at"]) for r in db().query("SELECT dev, expires_at FROM portal_invites")}
    options = "".join(f"<option>{e(r)}</option>" for r in ROLES if r != "ci")
    table = "".join(
        f"<tr><td>{e(r['dev'])}</td><td>{e(r['role'])}</td><td>{'convite ativo' if str(r['dev']) in invites else '—'}</td>"
        f'<td><form method=post action="/dash/people/revoke"><input type=hidden name=csrf value="{e(_csrf(request))}">'
        f'<input type=hidden name=dev value="{e(r["dev"])}"><button class=btn>Revogar</button></form></td></tr>'
        for r in rows
    )
    notice = (
        f"<section class=card><div class=card-head><h2>Convite criado</h2></div><p>Envie por um canal seguro. Ele não será "
        f"mostrado de novo.</p><code class=id>{e(token)}</code></section>"
        if token
        else ""
    )
    sso = (
        "SSO ligado: novos acessos vêm do provedor." if auth.oidc_enabled() else "Login provisório, até conectar o SSO."
    )
    body = (
        f"<div class=top><div><h1>Pessoas e acessos</h1><p>{e(sso)}</p></div></div>{notice}"
        "<section class=card><div class=card-head><h2>Convidar</h2></div>"
        f'<form method=post action="/dash/people" class=inline><input type=hidden name=csrf value="{e(_csrf(request))}">'
        f"<label>pessoa<input name=dev required></label><label>perfil<select name=role>{options}</select></label>"
        "<label>validade (dias)<input name=days value=30 size=4></label><button class='btn primary'>Convidar</button></form></section>"
        "<section class=card><div class=card-head><h2>Quem tem acesso</h2></div><div class=wrap><table><thead><tr>"
        f"<th>pessoa</th><th>perfil</th><th>login</th><th></th></tr></thead><tbody>{table}</tbody></table></div></section>"
    )
    return _html(dashboard.layout("Pessoas e acessos", principal, "people", body))


@app.post("/dash/people", response_model=None)
async def people_invite(request: Request) -> Response:
    principal = _guard(request, "people")
    if isinstance(principal, RedirectResponse):
        return principal
    form = await _form(request)
    auth.check_csrf(request, form.get("csrf", ""))
    role = form.get("role", "dev")
    if role not in ROLES or role == "ci":
        raise HTTPException(400, "perfil inválido")
    dev = form.get("dev", "").strip()
    if not dev:
        raise HTTPException(400, "informe a pessoa")
    days = max(1, min(90, int(form.get("days") or 30)))
    token = auth.create_invite(dev, role, principal.dev, days)
    return people_page(request, token)


@app.post("/dash/people/revoke", response_model=None)
async def people_revoke(request: Request) -> Response:
    principal = _guard(request, "people")
    if isinstance(principal, RedirectResponse):
        return principal
    form = await _form(request)
    auth.check_csrf(request, form.get("csrf", ""))
    auth.revoke_invites(form.get("dev", ""), principal.dev)
    return RedirectResponse("/dash/people", status_code=303)


KILL_KINDS = (
    ("tools", "ferramentas"),
    ("models", "modelos"),
    ("nexo_versions", "versões do Nexo"),
    ("brain_versions", "versões do cérebro"),
)


@app.get("/dash/killswitch", response_model=None)
def killswitch_page(request: Request) -> Response:
    principal = _guard(request, "killswitch")
    if isinstance(principal, RedirectResponse):
        return principal
    row = db().one("SELECT data FROM kill_switch WHERE id = 'org'")
    data = dict(json.loads(row["data"])) if row else {}
    fields = "".join(
        f"<label>{e(label)} (separados por vírgula)<input name={key} size=40 "
        f'value="{e(", ".join(data.get(key) or []))}"></label>'
        for key, label in KILL_KINDS
    )
    body = (
        "<div class=top><div><h1>Kill switch</h1><p>Bloqueia durante um incidente. Vale em até 2 minutos para quem está "
        "online; offline, vale a última resposta.</p></div></div><section class=card>"
        f'<form method=post action="/dash/killswitch" class=inline><input type=hidden name=csrf value="{e(_csrf(request))}">'
        f"{fields}<label>motivo (obrigatório)<input name=reason size=40 required></label>"
        "<button class='btn primary'>Aplicar</button></form></section>"
    )
    return _html(dashboard.layout("Kill switch", principal, "killswitch", body))


@app.post("/dash/killswitch", response_model=None)
async def killswitch_submit(request: Request) -> Response:
    from nexo.central.common import audit
    from nexo.kernel.security.killswitch import parse

    principal = _guard(request, "killswitch")
    if isinstance(principal, RedirectResponse):
        return principal
    form = await _form(request)
    auth.check_csrf(request, form.get("csrf", ""))
    reason = form.get("reason", "").strip()
    if len(reason) < 10:
        raise HTTPException(400, "o kill switch exige motivo")
    data: dict[str, Any] = {k: [v.strip() for v in form.get(k, "").split(",") if v.strip()] for k, _ in KILL_KINDS}
    data["reason"] = reason
    try:
        parse(data, "central")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    d = db()
    d.execute("DELETE FROM kill_switch WHERE id = 'org'")
    d.execute(
        "INSERT INTO kill_switch (id, data, updated_at, updated_by) VALUES ('org', ?, ?, ?)",
        (json.dumps(data), time.time(), principal.dev),
    )
    audit(principal.dev, "killswitch.set", {"reason": reason})
    return RedirectResponse("/dash/killswitch", status_code=303)


@app.get("/dash/insights-config", response_model=None)
def insights_config_page(request: Request) -> Response:
    from nexo.central.insights import engine

    principal = _dash_principal(request)
    if isinstance(principal, RedirectResponse):
        return principal
    if not principal.can_page("flags"):
        raise HTTPException(403, "sem acesso a esta página")
    settings = {
        str(r["detector"]): bool(r["enabled"]) for r in db().query("SELECT detector, enabled FROM insight_settings")
    }
    rows = ""
    for det in engine.load_detectors():
        on = settings.get(det.id, det.enabled)
        rows += (
            f"<tr><td><code class=id>{e(det.id)}</code></td><td>{e(det.title)}</td><td>{'ligado' if on else 'desligado'}</td>"
            f'<td><form method=post action="/dash/insights-config"><input type=hidden name=csrf value="{e(_csrf(request))}">'
            f'<input type=hidden name=detector value="{e(det.id)}"><input type=hidden name=enabled value="{0 if on else 1}">'
            f"<button class=btn>{'Desligar' if on else 'Ligar'}</button></form></td></tr>"
        )
    body = (
        "<div class=top><div><h1>Configuração de insights</h1><p>Ligue um a um, depois de conferir com dados reais.</p></div></div>"
        "<section class=card><div class=wrap><table><thead><tr><th>detector</th><th>o que aponta</th><th>estado</th><th></th>"
        f"</tr></thead><tbody>{rows}</tbody></table></div></section>"
    )
    return _html(dashboard.layout("Configuração de insights", principal, "insights", body))


@app.post("/dash/insights-config", response_model=None)
async def insights_config_submit(request: Request) -> Response:
    principal = _dash_principal(request)
    if isinstance(principal, RedirectResponse):
        return principal
    if not principal.can_page("flags"):
        raise HTTPException(403, "sem acesso a esta página")
    form = await _form(request)
    auth.check_csrf(request, form.get("csrf", ""))
    detector = form.get("detector", "")
    d = db()
    d.execute("DELETE FROM insight_settings WHERE detector = ?", (detector,))
    d.execute(
        "INSERT INTO insight_settings (detector, enabled, updated_by, updated_at) VALUES (?,?,?,?)",
        (detector, int(form.get("enabled") == "1"), principal.dev, time.time()),
    )
    return RedirectResponse("/dash/insights-config", status_code=303)
