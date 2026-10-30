"""Feature flags na CLI: fontes central e cérebro, cache curto, bloqueio de comandos e de funcionalidades."""

from __future__ import annotations

import json
import time
import urllib.parse
from typing import Any

from nexo.kernel import brain as brain_adapter
from nexo.kernel.config.flags import BY_KEY, CATALOG, Decision, FlagSource, flag_for_command, resolve
from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.runtime import http, yamlfile

CACHE = "flags.json"
TTL = 120  # segundos: desligar remotamente vale em até 2 min; offline vale o último estado conhecido


def _squad(ctx: AppContext) -> str:
    from nexo.kernel.platform import telemetry

    return str(telemetry.batch_context(ctx).get("squad") or "")


def _central(ctx: AppContext, refresh: bool = False) -> FlagSource:
    from nexo.kernel.platform import telemetry

    if not telemetry.enabled(ctx):
        return FlagSource("central")
    path = ctx.home / CACHE
    cached = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    if refresh or cached is None or time.time() - float(cached.get("at", 0)) > TTL:
        try:
            body = http.request(
                "GET",
                f"{telemetry._url(ctx)}/v2/flags",
                {"Authorization": f"Bearer {telemetry._bearer(ctx)}"},
            ).body
            cached = {"at": time.time(), "data": dict(body or {})}
            path.write_text(json.dumps(cached), encoding="utf-8")
        except (NexoError, OSError):
            pass  # offline: última resposta conhecida
    data = dict((cached or {}).get("data") or {})
    return FlagSource("central", dict(data.get("global") or {}), dict(data.get("squads") or {}))


def _brain(ctx: AppContext) -> FlagSource:
    status = brain_adapter.cached_status(ctx.brain_dir) or {}
    if not ctx.brain_dir.is_dir() or status.get("verified") is False:
        return FlagSource("cérebro")
    org = dict(yamlfile.load(ctx.brain_dir / "org" / "flags.yaml").get("flags") or {})
    squads: dict[str, dict[str, bool]] = {}
    squads_dir = ctx.brain_dir / "squads"
    if squads_dir.is_dir():
        for folder in squads_dir.iterdir():
            data = dict(yamlfile.load(folder / "flags.yaml").get("flags") or {}) if folder.is_dir() else {}
            if data:
                squads[folder.name] = {k: bool(v) for k, v in data.items() if k in BY_KEY}
    return FlagSource("cérebro", {k: bool(v) for k, v in org.items() if k in BY_KEY}, squads)


def sources(ctx: AppContext, refresh: bool = False) -> list[FlagSource]:
    cached = ctx.__dict__.get("_flag_sources")
    if cached is None or refresh:
        cached = [_central(ctx, refresh), _brain(ctx)]
        ctx.__dict__["_flag_sources"] = cached
    return list(cached)


def decide(ctx: AppContext, key: str) -> Decision:
    return resolve(key, _squad(ctx), sources(ctx))


def enabled(ctx: AppContext, key: str) -> bool:
    return decide(ctx, key).enabled


def require(ctx: AppContext, key: str) -> None:
    d = decide(ctx, key)
    if d.enabled:
        return
    squad = _squad(ctx)
    where = f"para o squad {squad}" if d.scope == "squad" else "para a empresa"
    ctx.audit("flag.blocked", {"flag": key, "source": d.source})
    raise NexoError(f"funcionalidade desligada {where} por feature flag: {BY_KEY[key].title} ({key}, fonte {d.source})")


def check_command(ctx: AppContext, args: list[str]) -> None:
    flag = flag_for_command(args)
    if flag is not None:
        require(ctx, flag.key)


def status(ctx: AppContext) -> dict[str, Any]:
    srcs = sources(ctx, refresh=True)
    squad = _squad(ctx)
    return {
        "squad": squad,
        "flags": [
            {"key": f.key, "title": f.title, "area": f.area, "where": f.where, **_d(resolve(f.key, squad, srcs))}
            for f in CATALOG
        ],
    }


def _d(d: Decision) -> dict[str, Any]:
    return {"enabled": d.enabled, "source": d.source, "scope": d.scope}


def set_remote(ctx: AppContext, key: str, value: bool | None, squad: str | None, reason: str) -> dict[str, Any]:
    """Liga/desliga no central (papéis admin ou time do Nexo); `value=None` volta a herdar."""
    from nexo.kernel.platform import telemetry

    if key not in BY_KEY:
        raise NexoError(f"flag desconhecida: {key} (veja `nexo admin flags list`)")
    body = http.request(
        "PUT",
        f"{telemetry._url(ctx)}/v2/admin/flags/{urllib.parse.quote(key)}",
        {"Authorization": f"Bearer {telemetry._bearer(ctx)}"},
        {"value": value, "squad": squad, "reason": reason},
    ).body
    (ctx.home / CACHE).unlink(missing_ok=True)
    return dict(body or {})
