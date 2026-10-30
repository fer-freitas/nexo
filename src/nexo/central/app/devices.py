"""Rotas de dispositivo: código de pareamento, aprovação, token, renovação, revogação e página de verificação."""

from __future__ import annotations

import os
import secrets
import string
import time
import uuid
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel

from nexo.central import auth, dashboard
from nexo.central.app.core import ACCESS_TTL, CODE_TTL, REFRESH_TTL, _form, _html, _issue, _require, app
from nexo.central.auth import _hash, identity
from nexo.central.common import audit, db, pseudonym


class CodeIn(BaseModel):
    device_id: str


class ApproveIn(BaseModel):
    user_code: str


class DeviceTokenIn(BaseModel):
    device_code: str


class RefreshIn(BaseModel):
    refresh_token: str


@app.post("/v2/device/code")
def device_code(body: CodeIn) -> dict[str, Any]:
    code = uuid.uuid4().hex
    user = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    db().execute(
        "INSERT INTO device_codes (device_code, user_code, device_id, status, dev, expires_at) VALUES (?,?,?,?,?,?)",
        (code, user, body.device_id, "pending", None, time.time() + CODE_TTL),
    )
    base = os.environ.get("NX_COLLECTOR_PUBLIC_URL", "https://coletor")
    return {
        "device_code": code,
        "user_code": f"{user[:4]}-{user[4:]}",
        "verification_uri": f"{base}/v2/device/verify",
        "interval": 5,
        "expires_in": CODE_TTL,
    }


def _approve_code(dev: str, user_code: str) -> None:
    user = user_code.replace("-", "").strip().upper()
    row = db().one("SELECT * FROM device_codes WHERE user_code = ?", (user,))
    if row is None or row["status"] != "pending" or float(row["expires_at"]) < time.time():
        raise HTTPException(404, "código inválido ou expirado")
    db().execute("UPDATE device_codes SET status = 'approved', dev = ? WHERE user_code = ?", (dev, user))
    audit(dev, "device.approved", {"device_id": row["device_id"]})


@app.post("/v2/device/approve")
def device_approve(body: ApproveIn, ident: dict[str, str] = Depends(identity)) -> dict[str, str]:  # noqa: B008
    """Aprovação por API com a credencial da pessoa; a página `/v2/device/verify` usa a sessão do SSO."""
    if not ident["dev"] or ident["device_id"]:
        raise HTTPException(403, "aprovação exige a sessão da pessoa (SSO), não o token de um dispositivo")
    _approve_code(ident["dev"], body.user_code)
    return {"status": "aprovado"}


@app.post("/v2/device/token")
def device_token(body: DeviceTokenIn) -> Any:
    row = db().one("SELECT * FROM device_codes WHERE device_code = ?", (body.device_code,))
    if row is None or float(row["expires_at"]) < time.time():
        raise HTTPException(404, "código inválido ou expirado")
    if row["status"] == "pending":
        return JSONResponse({"status": "authorization_pending"}, status_code=428)
    if row["status"] != "approved":
        raise HTTPException(409, "código já usado")
    pid = pseudonym(str(row["dev"]))
    device = str(row["device_id"])
    existing = db().one("SELECT status FROM devices WHERE device_id = ?", (device,))
    if existing is None:
        db().execute(
            "INSERT INTO devices (device_id, dev_pid, status, created_at) VALUES (?, ?, 'active', ?)",
            (device, pid, time.time()),
        )
    elif existing["status"] != "active":
        raise HTTPException(403, "dispositivo revogado: peça a reativação ao time do Nexo")
    db().execute("UPDATE device_codes SET status = 'used' WHERE device_code = ?", (body.device_code,))
    return {
        "access_token": _issue(device, "access", ACCESS_TTL),
        "refresh_token": _issue(device, "refresh", REFRESH_TTL),
        "expires_in": ACCESS_TTL,
    }


@app.post("/v2/device/refresh")
def device_refresh(body: RefreshIn) -> dict[str, Any]:
    row = db().one(
        "SELECT t.device_id, t.expires_at, t.revoked, d.status FROM tokens t JOIN devices d ON d.device_id ="
        " t.device_id WHERE t.token_hash = ? AND t.kind = 'refresh'",
        (_hash(body.refresh_token),),
    )
    if row is None or row["revoked"] or float(row["expires_at"]) < time.time() or row["status"] != "active":
        raise HTTPException(401, "renovação recusada: rode `nexo login central`")
    return {"access_token": _issue(str(row["device_id"]), "access", ACCESS_TTL), "expires_in": ACCESS_TTL}


@app.post("/v2/devices/{device_id}/revoke")
def revoke(device_id: str, ident: dict[str, str] = Depends(identity)) -> dict[str, str]:  # noqa: B008
    _require(ident, "admin")
    d = db()
    d.execute("UPDATE devices SET status = 'revoked', revoked_at = ? WHERE device_id = ?", (time.time(), device_id))
    d.execute("UPDATE tokens SET revoked = 1 WHERE device_id = ?", (device_id,))
    audit(ident["dev"], "device.revoked", {"device_id": device_id})
    return {"status": "revogado"}


@app.get("/v2/device/verify", response_model=None)
def verify_page(request: Request) -> Response:
    session = auth.session_of(request)
    if session is None:
        return RedirectResponse("/login?next=/v2/device/verify", status_code=303)
    principal = auth.principal_for(str(session["dev"]), session["role"] or None, "session")
    return _html(dashboard.render_verify(principal, str(session["csrf"])))


@app.post("/v2/device/verify", response_model=None)
async def verify_submit(request: Request) -> Response:
    session = auth.session_of(request)
    if session is None:
        raise HTTPException(401, "sessão ausente")
    form = await _form(request)
    auth.check_csrf(request, form.get("csrf", ""))
    principal = auth.principal_for(str(session["dev"]), session["role"] or None, "session")
    _approve_code(principal.dev, form.get("user_code", ""))
    return _html(dashboard.render_verify(principal, str(session["csrf"]), "dispositivo autorizado; volte ao terminal"))
