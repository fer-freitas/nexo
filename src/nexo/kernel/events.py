"""Barramento de eventos: os módulos ouvem o que acontece sem importar uns aos outros.

Quem assina declara no manifesto (`subscribe` e `events.consumes`). Quem assina nunca derruba quem publicou: falha
vira um evento de auditoria.
"""

from __future__ import annotations

import functools
import importlib
from collections.abc import Callable
from typing import Any

Handler = Callable[[Any, str, dict[str, Any], str | None], None]


@functools.lru_cache(maxsize=1)
def _subscriptions() -> tuple[tuple[frozenset[str], str], ...]:
    from nexo.kernel import modules

    return tuple((frozenset(m.events.get("consumes", [])), m.subscribe) for m in modules.builtin() if m.subscribe)


def publish(ctx: Any, type_: str, payload: dict[str, Any], ref: str | None) -> None:
    for consumes, target in _subscriptions():
        if type_ not in consumes:
            continue
        module, _, name = target.partition(":")
        try:
            handler: Handler = getattr(importlib.import_module(module), name)  # nosemgrep -- alvo do manifesto embutido
            handler(ctx, type_, payload, ref)
        except Exception as exc:  # o assinante nunca interrompe o fluxo de quem publicou
            ctx.events.append(ctx.actor, "event.handler_failed", {"event": type_, "error": str(exc)[:300]}, ref=ref)
