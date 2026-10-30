"""Kill switch: junta cérebro, central e bloqueio local; aplica no roteamento e no início de cada passo."""

from __future__ import annotations

import json
import time
from typing import Any

from nexo import __version__
from nexo.kernel import brain as brain_adapter
from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.runtime import http, yamlfile
from nexo.kernel.security.killswitch import KillSwitch, merge, parse, route_block, version_block

BRAIN_FILE = "org/kill-switch.yaml"
CENTRAL_TTL = 300  # consulta ao central no máximo a cada 5 min; offline vale a última resposta


def _cache_path(ctx: AppContext, name: str) -> Any:
    return ctx.home / name


def _brain(ctx: AppContext) -> KillSwitch:
    path = ctx.brain_dir / BRAIN_FILE
    status = brain_adapter.cached_status(ctx.brain_dir) or {}
    if not path.is_file() or not status.get("verified", True):
        return KillSwitch()
    return parse(dict(yamlfile.load(path) or {}), "cérebro")


def _central(ctx: AppContext, refresh: bool = False) -> KillSwitch:
    from nexo.kernel.platform import telemetry

    cache = _cache_path(ctx, "kill-switch.central.json")
    cached = json.loads(cache.read_text(encoding="utf-8")) if cache.is_file() else None
    if not telemetry.enabled(ctx):
        return KillSwitch()
    fresh = cached is not None and time.time() - float(cached.get("fetched_at", 0)) < CENTRAL_TTL
    if not fresh or refresh:
        try:
            url = telemetry._url(ctx)
            body = http.request(
                "GET", f"{url}/v2/kill-switch", {"Authorization": f"Bearer {telemetry._bearer(ctx)}"}
            ).body
            cached = {"fetched_at": time.time(), "data": dict(body or {})}
            cache.write_text(json.dumps(cached), encoding="utf-8")
        except (NexoError, OSError):
            pass  # offline: vale a última resposta conhecida
    return parse(dict((cached or {}).get("data") or {}), "central") if cached else KillSwitch()


def _local(ctx: AppContext) -> KillSwitch:
    path = _cache_path(ctx, "kill-switch.local.json")
    if not path.is_file():
        return KillSwitch()
    data = json.loads(path.read_text(encoding="utf-8"))
    live = {k: v for k, v in dict(data.get("models") or {}).items() if float(v.get("until", 0)) > time.time()}
    return parse({"models": {k: v["reason"] for k, v in live.items()}}, "local")


def block_local(ctx: AppContext, route: str, reason: str, hours: float = 24.0) -> None:
    """Bloqueio local e temporário de uma rota (monitor sintético detectou mudança de formato)."""
    path = _cache_path(ctx, "kill-switch.local.json")
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    models = dict(data.get("models") or {})
    models[route] = {"reason": reason, "until": time.time() + hours * 3600}
    path.write_text(json.dumps({"models": models}), encoding="utf-8")
    ctx.audit("killswitch.local", {"route": route, "reason": reason, "hours": hours})


def current(ctx: AppContext, refresh: bool = False) -> KillSwitch:
    return merge(_brain(ctx), _central(ctx, refresh), _local(ctx))


def blocked_routes(ctx: AppContext, routes: dict[str, Any]) -> dict[str, str]:
    ks = current(ctx)
    if ks.empty():
        return {}
    out = {}
    for route_id, route in routes.items():
        why = route_block(ks, str(route.tool), str(route.model))
        if why:
            out[route_id] = why
    return out


def check(ctx: AppContext) -> None:
    """Início de cada passo: versão do Nexo ou do cérebro bloqueada para tudo."""
    ks = current(ctx)
    if ks.empty():
        return
    brain_version = str((brain_adapter.cached_status(ctx.brain_dir) or {}).get("version") or "")
    why = version_block(ks, __version__, brain_version)
    if why:
        ctx.audit("killswitch.blocked", {"kind": "version", "sources": ks.sources})
        raise NexoError(f"kill switch: {why} — atualize (`nexo update` / `nexo brain pull`) ou aguarde a liberação")


def status(ctx: AppContext) -> dict[str, Any]:
    ks = current(ctx, refresh=True)
    brain_version = str((brain_adapter.cached_status(ctx.brain_dir) or {}).get("version") or "")
    return {
        **ks.as_dict(),
        "nexo": __version__,
        "brain": brain_version,
        "blocked_here": version_block(ks, __version__, brain_version),
    }
