"""Autonomia conquistada do lado da CLI: qual nível vale para este squad × área.

Fonte: o central (nível aprovado pelo gestor, rebaixado automaticamente em escape/incidente), com cache de 5 min;
sem central, `autonomy.levels.<squad>.<área>` ou `autonomy.default` da org/squad. O dev nunca sobe a própria
autonomia por configuração local.
"""

from __future__ import annotations

import json
import time
import urllib.parse
from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.platform import config
from nexo.kernel.runtime import http
from nexo.kernel.workflow.autonomy import DEFAULT_LEVEL, LEVELS

TRUSTED = ("default", "org", "squad")
TTL = 300


def _trusted(ctx: AppContext) -> dict[str, Any]:
    from nexo.kernel.config.cascade import resolve

    layers = [x for x in config.load_layers(ctx) if x.level in TRUSTED]
    return dict(resolve(layers).values.get("autonomy") or {})


def scope(ctx: AppContext) -> tuple[str, str]:
    from nexo.kernel.platform import telemetry

    context = telemetry.batch_context(ctx)
    return str(context.get("squad") or ""), str(context.get("stack") or "")


def level(ctx: AppContext) -> str:
    from nexo.kernel.platform import telemetry

    squad, area = scope(ctx)
    cfg = _trusted(ctx)
    fallback = str(((cfg.get("levels") or {}).get(squad) or {}).get(area) or cfg.get("default") or DEFAULT_LEVEL)
    if fallback not in LEVELS:
        fallback = DEFAULT_LEVEL
    if not telemetry.enabled(ctx):
        return fallback
    cache = ctx.home / "autonomy.json"
    data = json.loads(cache.read_text(encoding="utf-8")) if cache.is_file() else {}
    entry = data.get(f"{squad}|{area}")
    if entry and time.time() - float(entry["at"]) < TTL:
        return str(entry["level"])
    try:
        query = urllib.parse.urlencode({"squad": squad, "area": area})
        body = http.request(
            "GET",
            f"{telemetry._url(ctx)}/v2/autonomy?{query}",
            {"Authorization": f"Bearer {telemetry._bearer(ctx)}"},
        ).body
        value = str(dict(body or {}).get("level") or fallback)
    except (NexoError, OSError):
        return str(entry["level"]) if entry else fallback  # offline: último nível conhecido
    data[f"{squad}|{area}"] = {"level": value, "at": time.time()}
    cache.write_text(json.dumps(data), encoding="utf-8")
    return value


def status(ctx: AppContext) -> dict[str, Any]:
    from nexo.kernel.platform import telemetry

    squad, area = scope(ctx)
    out: dict[str, Any] = {"squad": squad, "area": area, "level": level(ctx), "eligibility": None}
    if telemetry.enabled(ctx) and squad:
        try:
            body = http.request(
                "GET",
                f"{telemetry._url(ctx)}/v2/autonomy/eligibility?squad={urllib.parse.quote(squad)}",
                {"Authorization": f"Bearer {telemetry._bearer(ctx)}"},
            ).body
            out["eligibility"] = next((a for a in dict(body).get("areas") or [] if a["area"] == area), None)
            out["history"] = dict(body).get("history") or []
        except (NexoError, OSError) as exc:
            out["error"] = str(exc)[:200]
    return out


def set_level(
    ctx: AppContext, target: str, reason: str, squad: str | None = None, area: str | None = None
) -> dict[str, Any]:
    """Pedido de mudança ao central (o gestor do squad sobe; lead/gestor/Segurança descem)."""
    from nexo.kernel.platform import telemetry

    own_squad, own_area = scope(ctx)
    squad, area = squad or own_squad, area or own_area
    if not squad or not area:
        raise NexoError("squad e área não identificados: configure `squad` no projeto ou informe --squad/--area")
    body = http.request(
        "POST",
        f"{telemetry._url(ctx)}/v2/autonomy/{urllib.parse.quote(squad)}/{urllib.parse.quote(area)}",
        {"Authorization": f"Bearer {telemetry._bearer(ctx)}"},
        {"level": target, "reason": reason},
    ).body
    (ctx.home / "autonomy.json").unlink(missing_ok=True)
    return dict(body or {})
