"""Identidade de quem dispara e de quem aprova.

Modo `verified` (padrão): a identidade vem do provedor corporativo, a partir do token no keyring — GitHub
(`GET /user`). Usuário do SO e `NX_ACTOR` não servem para aprovar.
Modo `os`: só vale se definido pela org ou pelo squad (cérebro); definido no projeto ou no pessoal é ignorado.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.platform import config
from nexo.kernel.runtime import http
from nexo.kernel.secrets import credentials

CACHE_TTL_S = 12 * 3600
TRUSTED_ORIGINS = ("default", "org", "squad")


@dataclass(frozen=True)
class Identity:
    id: str  # ex.: github:pessoa ou os:pessoa
    verified: bool


def mode(ctx: AppContext) -> str:
    res = config.resolved(ctx)
    value = str((res.values.get("identity") or {}).get("mode") or "verified")
    origin = res.origin.get("identity.mode", "default")
    if value != "verified" and origin not in TRUSTED_ORIGINS:
        ctx.audit(
            "policy.violation",
            {"rule": "SEC-52", "reason": f"identity.mode={value} só vale na org ou no squad", "origin": origin},
        )
        return "verified"
    return value


def _provider(ctx: AppContext) -> str:
    cfg = config.resolved(ctx).values
    explicit = (cfg.get("identity") or {}).get("provider")
    if explicit:
        return str(explicit)
    return "github"


def _cache_get(ctx: AppContext, provider: str, token: str) -> str | None:
    path = ctx.home / "identity.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    key = hashlib.sha256(f"{provider}:{token}".encode()).hexdigest()
    if data.get("key") == key and time.time() - float(data.get("at", 0)) < CACHE_TTL_S:
        return str(data["login"])
    return None


def _cache_put(ctx: AppContext, provider: str, token: str, login: str) -> None:
    ctx.home.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(f"{provider}:{token}".encode()).hexdigest()
    (ctx.home / "identity.json").write_text(json.dumps({"key": key, "login": login, "at": time.time()}), "utf-8")


def _lookup(ctx: AppContext, provider: str) -> str:
    if provider == "github":
        token = credentials.get_secret("github")
        if not token:
            raise NexoError("identidade verificada exigida: rode `nexo login github` (SEC-52)")
        cached = _cache_get(ctx, provider, token)
        if cached:
            return cached
        body = http.request(
            "GET", "https://api.github.com/user", {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        ).body
        login = str(body.get("login") or "")
    else:
        raise NexoError(f"provedor de identidade desconhecido: {provider} (github)")
    if not login:
        raise NexoError(f"o {provider} não confirmou a identidade do token")
    _cache_put(ctx, provider, token, login)
    return login


def current(ctx: AppContext) -> Identity:
    """Identidade para autoria e aprovação de portões."""
    if mode(ctx) == "os":
        return Identity(f"os:{ctx.actor}", verified=False)
    provider = _provider(ctx)
    try:
        login = _lookup(ctx, provider)
    except OSError as exc:
        raise NexoError(f"não foi possível verificar a identidade no {provider}: {exc}") from exc
    return Identity(f"{provider}:{login}", verified=True)
