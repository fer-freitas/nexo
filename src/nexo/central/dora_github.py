"""DORA sem Actions: lê a API do GitHub, só leitura. Implantação vem de Deployments e releases; lead time, de PRs.

Sem fonte, o painel mostra `sem dado`, em vez de um número inventado.
"""

from __future__ import annotations

import os
import time
from typing import Any

from nexo.central.db import CollectorDB
from nexo.central.dora import record_deployment
from nexo.kernel.runtime import http

API = "https://api.github.com"


def _headers() -> dict[str, str]:
    token = os.environ.get("NX_COLLECTOR_GITHUB_TOKEN", "")
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"} if token else {}


def collect(d: CollectorDB, repos: list[str], squads: dict[str, str] | None = None) -> dict[str, Any]:
    """Traz o que é novo. Idempotente: o ID da implantação no GitHub evita contar duas vezes."""
    total = 0
    sourceless = []
    for repo in repos:
        items = (
            http.request("GET", f"{API}/repos/{repo}/deployments?environment=production&per_page=50", _headers()).body
            or []
        )
        kind = "deployment"
        if not items:
            items = http.request("GET", f"{API}/repos/{repo}/releases?per_page=50", _headers()).body or []
            kind = "release"
        if not items:
            sourceless.append(repo)
            continue
        for item in items:
            at = item.get("created_at") or item.get("published_at")
            if not at:
                continue
            record_deployment(
                d,
                {
                    "id": f"{repo}#{kind}-{item.get('id')}",
                    "service": repo,
                    "squad": (squads or {}).get(repo, ""),
                    "sha": item.get("sha") or item.get("target_commitish") or "",
                    "commits": [],
                    "finished_at": str(at),
                },
            )
            total += 1
    return {"deployments": total, "without_source": sourceless, "at": time.time()}
