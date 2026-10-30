"""Leitura da conta pela CLI da AWS da própria pessoa. Só comandos de listagem, e nunca pelo agente local."""

from __future__ import annotations

import json
import subprocess
from typing import Any

from nexo.kernel.errors import NexoError
from nexo.kernel.runtime import system


def _call(*args: str) -> dict[str, Any]:
    exe = system.which("aws")
    if exe is None:
        raise NexoError("CLI da AWS não encontrada: instale e autentique, com permissão de leitura")
    done = subprocess.run(
        [exe, *args, "--output", "json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    if done.returncode != 0:
        raise NexoError(f"aws {args[0]} falhou: {done.stderr.strip()[:200]}")
    return dict(json.loads(done.stdout or "{}"))


def inventory() -> dict[str, Any]:
    services: list[str] = []
    for cluster in _call("ecs", "list-clusters").get("clusterArns") or []:
        services += _call("ecs", "list-services", "--cluster", str(cluster)).get("serviceArns") or []
    return {
        "subscriptions": _call("sns", "list-subscriptions").get("Subscriptions") or [],
        "mappings": _call("lambda", "list-event-source-mappings").get("EventSourceMappings") or [],
        "services": services,
    }
