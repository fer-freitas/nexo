"""Autenticação do coletor: token (integrações, dispositivos), sessão do dashboard e SSO (OIDC).

SSO: fluxo de código com PKCE contra o provedor corporativo (qualquer OIDC), configurado por
`NX_COLLECTOR_OIDC_ISSUER`, `_CLIENT_ID`, `_CLIENT_SECRET` e `_CLAIM` (padrão `preferred_username`). A identidade
vem do `userinfo` do provedor (chamada TLS autenticada pelo token recém-emitido), sem aceitar token do navegador.
Sem OIDC configurado, a tela de login aceita um token pessoal (desenvolvimento/demo; desligável com
`NX_COLLECTOR_TOKEN_LOGIN=0`).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any, Protocol

from fastapi import Header, HTTPException, Request

from nexo.central.common import audit, db
from nexo.central.visibility import Principal

SESSION_COOKIE = "nexo_session"
SESSION_TTL = 8 * 3600
STATE_TTL = 600


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def static_tokens() -> dict[str, dict[str, Any]]:
    return dict(json.loads(os.environ.get("NX_COLLECTOR_TOKENS", "{}")))


def principal_for(dev: str, role: str | None, via: str) -> Principal:
    """Papel: token de integração define o próprio; pessoa (SSO/dispositivo) usa o diretório da org (padrão dev)."""
    d = db()
    if role is None:
        row = d.one("SELECT role FROM org_people WHERE dev = ?", (dev,))
        role = str(row["role"]) if row else "dev"
    p = Principal(dev=dev, role=role, via=via)
    for r in d.query("SELECT squad, relation FROM org_members WHERE dev = ?", (dev,)):
        {"member": p.member, "lead": p.leads, "manager": p.manages}.get(str(r["relation"]), set()).add(str(r["squad"]))
    if not p.member and dev:
        from nexo.central.common import pid_of

        rows = d.query(
            "SELECT DISTINCT squad FROM raw_events WHERE dev_pid = ? AND squad IS NOT NULL AND squad <> ''",
            (pid_of(dev),),
        )
        p.member |= {str(r["squad"]) for r in rows}
    return p


def from_bearer(token: str) -> dict[str, str] | None:
    static = static_tokens().get(token)
    if static is not None:
        return {"role": str(static.get("role", "dev")), "dev": str(static.get("dev", "")), "device_id": ""}
    row = db().one(
        "SELECT t.device_id, t.expires_at, t.revoked, d.status, i.dev FROM tokens t JOIN devices d ON d.device_id ="
        " t.device_id JOIN identities i ON i.dev_pid = d.dev_pid WHERE t.token_hash = ? AND t.kind = 'access'",
        (_hash(token),),
    )
    if row is not None and row["status"] != "active":
        raise HTTPException(401, "dispositivo revogado: rode `nexo login central` depois da reativação")
    if row is None or row["revoked"] or float(row["expires_at"]) < time.time():
        return None
    return {"role": "", "dev": str(row["dev"]), "device_id": str(row["device_id"])}


def identity(authorization: str = Header(default="")) -> dict[str, str]:
    """Identidade das APIs de máquina (ingestão, orçamento, admin)."""
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "token ausente")
    ident = from_bearer(token)
    if ident is None:
        raise HTTPException(401, "token inválido ou expirado")
    if not ident["role"]:
        ident["role"] = "dev"
    return ident


def session_of(request: Request) -> dict[str, Any] | None:
    sid = request.cookies.get(SESSION_COOKIE)
    if not sid:
        return None
    row = db().one("SELECT * FROM sessions WHERE sid_hash = ?", (_hash(sid),))
    if row is None or float(row["expires_at"]) < time.time():
        return None
    return row


def principal(request: Request) -> Principal:
    """Pessoa das APIs de leitura e do dashboard: cabeçalho Bearer ou sessão (cookie)."""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        ident = from_bearer(auth.removeprefix("Bearer ").strip())
        if ident is None:
            raise HTTPException(401, "token inválido ou expirado")
        return principal_for(ident["dev"], ident["role"] or None, "device" if ident["device_id"] else "token")
    session = session_of(request)
    if session is None:
        raise HTTPException(401, "sessão ausente: entre em /login")
    return principal_for(str(session["dev"]), session["role"] or None, "session")


def check_csrf(request: Request, token: str) -> None:
    """Formulário do dashboard: token CSRF da sessão (cabeçalho Bearer dispensa — não há cookie envolvido)."""
    if request.headers.get("authorization", "").startswith("Bearer "):
        return
    session = session_of(request)
    if session is None or not secrets.compare_digest(str(session["csrf"]), token or ""):
        raise HTTPException(403, "token CSRF inválido")


def create_session(dev: str, role: str | None, via: str) -> tuple[str, str]:
    sid = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(16)
    db().execute(
        "INSERT INTO sessions (sid_hash, dev, role, csrf, expires_at, created_at) VALUES (?,?,?,?,?,?)",
        (_hash(sid), dev, role, csrf, time.time() + SESSION_TTL, time.time()),
    )
    audit(dev, "session.created", {"via": via})
    return sid, csrf


def end_session(request: Request) -> None:
    sid = request.cookies.get(SESSION_COOKIE)
    if sid:
        db().execute("DELETE FROM sessions WHERE sid_hash = ?", (_hash(sid),))


def secure_cookie() -> bool:
    public = os.environ.get("NX_COLLECTOR_PUBLIC_URL", "")
    return not public.startswith(("http://127.0.0.1", "http://localhost")) and public != ""


class AuthProvider(Protocol):
    """O portal só conhece a pessoa e o perfil. Conectar o SSO é configuração, não redesenho."""

    name: str

    def start(self, next_url: str) -> str | None:
        """Endereço para onde mandar a pessoa, ou None quando o login é na própria tela."""

    def accepts_token(self) -> bool: ...


class InviteProvider:
    name = "convite"

    def start(self, next_url: str) -> str | None:
        return None

    def accepts_token(self) -> bool:
        return token_login_enabled()


class OidcProvider:
    name = "oidc"

    def start(self, next_url: str) -> str | None:
        return oidc_start(next_url)

    def accepts_token(self) -> bool:
        return False


def provider() -> AuthProvider:
    return OidcProvider() if oidc_enabled() else InviteProvider()


def first_admin(dev: str) -> str:
    """Criado por comando na implantação, uma vez só. Depois, o acesso passa pela tela de Pessoas e acessos."""
    d = db()
    if d.one("SELECT dev FROM org_people WHERE role = 'admin'") or d.one(
        "SELECT dev FROM portal_invites WHERE role = 'admin'"
    ):
        raise PermissionError("já existe um admin: use a tela de Pessoas e acessos")
    return create_invite(dev, "admin", "implantação", days=1)


def token_login_enabled() -> bool:
    return not oidc_enabled() and os.environ.get("NX_COLLECTOR_TOKEN_LOGIN", "1") != "0"


# ---------------------------------------------------------------- OIDC


def oidc_enabled() -> bool:
    return bool(os.environ.get("NX_COLLECTOR_OIDC_ISSUER") and os.environ.get("NX_COLLECTOR_OIDC_CLIENT_ID"))


def _http_json(method: str, url: str, form: dict[str, str] | None = None, bearer: str | None = None) -> dict[str, Any]:
    if not url.startswith("https://") and not url.startswith(("http://127.0.0.1:", "http://localhost:")):
        raise HTTPException(502, "provedor de identidade precisa ser https")
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    headers = {"Accept": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    if form is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, method=method, data=data, headers=headers)  # noqa: S310 — esquema validado
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
        return dict(json.loads(resp.read() or b"{}"))


http_json: Callable[..., dict[str, Any]] = _http_json


def _discovery() -> dict[str, Any]:
    issuer = os.environ["NX_COLLECTOR_OIDC_ISSUER"].rstrip("/")
    return http_json("GET", f"{issuer}/.well-known/openid-configuration")


def _redirect_uri() -> str:
    return os.environ.get("NX_COLLECTOR_PUBLIC_URL", "").rstrip("/") + "/auth/callback"


def oidc_start(next_url: str) -> str:
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    db().execute(
        "INSERT INTO oidc_states (state, verifier, next, expires_at) VALUES (?,?,?,?)",
        (state, verifier, next_url, time.time() + STATE_TTL),
    )
    params = {
        "response_type": "code",
        "client_id": os.environ["NX_COLLECTOR_OIDC_CLIENT_ID"],
        "redirect_uri": _redirect_uri(),
        "scope": "openid profile email",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{_discovery()['authorization_endpoint']}?{urllib.parse.urlencode(params)}"


def _apply_groups(dev: str, claims: dict[str, Any]) -> None:
    """Se o provedor envia grupos, eles definem o perfil: `NX_COLLECTOR_OIDC_GROUP_ROLES` = {"grupo": "perfil"}."""
    mapping = json.loads(os.environ.get("NX_COLLECTOR_OIDC_GROUP_ROLES") or "{}")
    groups = claims.get(os.environ.get("NX_COLLECTOR_OIDC_GROUPS_CLAIM", "groups")) or []
    role = next((mapping[g] for g in groups if g in mapping), None)
    if role:
        db().execute("DELETE FROM org_people WHERE dev = ?", (dev,))
        db().execute("INSERT INTO org_people (dev, role) VALUES (?, ?)", (dev, role))


def create_invite(dev: str, role: str, created_by: str, days: int = 30) -> str:
    """Login antes do SSO: token por pessoa, guardado só como hash e com validade. A senha nunca fica no banco."""
    token = "nxi_" + secrets.token_urlsafe(32)
    now = time.time()
    db().execute(
        "INSERT INTO portal_invites (token_hash, dev, role, expires_at, used_at, created_by, created_at) VALUES (?,?,?,?,?,?,?)",
        (_hash(token), dev, role, now + days * 86400, None, created_by, now),
    )
    audit(created_by, "portal.invite", {"dev": dev, "role": role, "days": days})
    return token


def revoke_invites(dev: str, actor: str) -> None:
    db().execute("DELETE FROM portal_invites WHERE dev = ?", (dev,))
    audit(actor, "portal.invite_revoked", {"dev": dev})


MAX_ATTEMPTS = 8
LOCK_SECONDS = 900


def login_blocked(key: str) -> bool:
    row = db().one("SELECT attempts, until FROM portal_login_attempts WHERE key = ?", (key,))
    return bool(row and int(row["attempts"]) >= MAX_ATTEMPTS and float(row["until"]) > time.time())


def login_failed(key: str) -> None:
    row = db().one("SELECT attempts, until FROM portal_login_attempts WHERE key = ?", (key,))
    attempts = int(row["attempts"]) + 1 if row and float(row["until"]) > time.time() else 1
    db().execute("DELETE FROM portal_login_attempts WHERE key = ?", (key,))
    db().execute(
        "INSERT INTO portal_login_attempts (key, attempts, until) VALUES (?,?,?)",
        (key, attempts, time.time() + LOCK_SECONDS),
    )


def login_ok(key: str) -> None:
    db().execute("DELETE FROM portal_login_attempts WHERE key = ?", (key,))


def from_invite(token: str) -> dict[str, str] | None:
    row = db().one("SELECT dev, role, expires_at FROM portal_invites WHERE token_hash = ?", (_hash(token),))
    if row is None or float(row["expires_at"]) < time.time():
        return None
    db().execute("UPDATE portal_invites SET used_at = ? WHERE token_hash = ?", (time.time(), _hash(token)))
    db().execute("DELETE FROM org_people WHERE dev = ?", (row["dev"],))
    db().execute("INSERT INTO org_people (dev, role) VALUES (?, ?)", (row["dev"], row["role"]))
    return {"dev": str(row["dev"]), "role": str(row["role"]), "device_id": ""}


def oidc_finish(code: str, state: str) -> tuple[str, str]:
    """Troca o código, lê a identidade no userinfo e devolve (dev, próxima página)."""
    row = db().one("SELECT * FROM oidc_states WHERE state = ?", (state,))
    db().execute("DELETE FROM oidc_states WHERE state = ? OR expires_at < ?", (state, time.time()))
    if row is None or float(row["expires_at"]) < time.time():
        raise HTTPException(400, "login expirado: tente de novo")
    meta = _discovery()
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": _redirect_uri(),
        "client_id": os.environ["NX_COLLECTOR_OIDC_CLIENT_ID"],
        "code_verifier": str(row["verifier"]),
    }
    if os.environ.get("NX_COLLECTOR_OIDC_CLIENT_SECRET"):
        form["client_secret"] = os.environ["NX_COLLECTOR_OIDC_CLIENT_SECRET"]
    tokens = http_json("POST", str(meta["token_endpoint"]), form=form)
    access = str(tokens.get("access_token") or "")
    if not access:
        raise HTTPException(401, "provedor não emitiu token")
    claims = http_json("GET", str(meta["userinfo_endpoint"]), bearer=access)
    claim = os.environ.get("NX_COLLECTOR_OIDC_CLAIM", "preferred_username")
    dev = str(claims.get(claim) or "").strip().lower()
    if os.environ.get("NX_COLLECTOR_OIDC_STRIP_DOMAIN", "1") == "1":
        dev = dev.split("@")[0]
    if not dev:
        raise HTTPException(401, f"identidade sem o atributo {claim}")
    _apply_groups(dev, claims)
    nxt = str(row["next"] or "/dash")
    return dev, nxt if nxt.startswith("/") and not nxt.startswith("//") else "/dash"
