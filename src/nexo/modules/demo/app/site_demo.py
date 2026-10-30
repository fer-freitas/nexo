"""`nexo demo site`: o portal em 127.0.0.1, com SQLite e dados fictícios de três squads."""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from nexo.kernel.errors import NexoError
from nexo.kernel.monitoring.telemetry import CONTRACT

SQUADS = {
    "sq-pagamentos": ("demo.bia", "pagamentos"),
    "sq-cartoes": ("demo.caio", "cartoes"),
    "sq-risco": ("demo.duda", "risco"),
}
ROLES = ("admin", "nexo", "director", "manager", "lead", "finops", "security", "dev", "pm", "qa")


def people() -> dict[str, dict[str, str]]:
    """Um acesso por perfil, com token novo a cada execução."""
    return {secrets.token_urlsafe(12): {"role": role, "dev": f"demo.{role}"} for role in ROLES}


def directory() -> dict[str, Any]:
    return {
        "directorates": [
            {
                "id": "dir-demo",
                "name": "Diretoria de demonstração",
                "squads": [
                    {"id": squad, "leads": ["demo.lead"], "managers": ["demo.manager"], "members": [dev]}
                    for squad, (dev, _project) in SQUADS.items()
                ],
            }
        ],
        "roles": {f"demo.{r}": r for r in ROLES if r not in ("dev",)},
    }


def _rec(kind: str, type_: str, payload: dict[str, Any], at: datetime) -> dict[str, Any]:
    return {
        "event_id": uuid.uuid4().hex,
        "kind": kind,
        "seq": 0,
        "type": type_,
        "at": at.isoformat(timespec="milliseconds"),
        "payload": payload,
    }


def batch(squad: str, project: str, days: int = 20, stories_per_day: int = 1) -> dict[str, Any]:
    """Um mês curto de trabalho fictício. Os números variam por squad só para o painel ter o que mostrar."""
    now = datetime.now(UTC)
    # dentro do mês atual, para as páginas do mês não ficarem zeradas no dia 1º
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    reference = max(now, month_start + timedelta(days=days + 1))
    weight = 1 + sum(map(ord, squad)) % 3
    events: list[dict[str, Any]] = []
    for day in range(days):
        at = reference - timedelta(days=day)
        at = max(at, month_start + timedelta(hours=1))
        story = f"US-{day + 1}-1"
        tool = ("claude", "devin", "copilot")[day % 3]
        cost = round(4.0 + weight * 1.7 + day % 5, 2)
        events.append(
            _rec(
                "step",
                "step",
                {
                    "target": story,
                    "story": story,
                    "role": "developer",
                    "tool": tool,
                    "model": "sonnet",
                    "started_at": at.isoformat(timespec="milliseconds"),
                    "seconds": 40.0 + day,
                    "outcome": "success",
                    "usage_brl": cost,
                    "api_equiv_usd": round(cost / 5.4, 4),
                    "acu": 2.0 if tool == "devin" else None,
                    "brain_version": "demo",
                },
                at,
            )
        )
        done = day % 4 != 0
        events.append(
            _rec(
                "story",
                "story",
                {
                    "story": story,
                    "feature": f"F-{day + 1}",
                    "stack": "python",
                    "points": 1 + day % 3,
                    "state": "Concluido" if done else "AguardandoHumano",
                    "human_gate": not done,
                    "escalated": False,
                    "bugs": day % 3 == 0,
                    "attempts": 1 + day % 2,
                    "spec_returns": 0,
                    "clarifications": day % 2,
                    "handoffs": 0,
                    "cost_brl": cost,
                    "cap_brl": 45.0,
                    "estimate_brl": cost + 1,
                    "started_at": (at - timedelta(hours=6)).isoformat(timespec="milliseconds"),
                    "done_at": at.isoformat(timespec="milliseconds") if done else None,
                    "waiting_human_hours": 0.0 if done else 3.0,
                },
                at,
            )
        )
        decision = "auto" if done else "human"
        events.append(
            _rec(
                "event",
                "gate.decision",
                {
                    "gate": "code",
                    "story": story,
                    "decision": decision,
                    "score": 0.96 if done else 0.78,
                    "threshold": 0.9,
                },
                at,
            )
        )
        events.append(
            _rec(
                "event",
                "quality.run",
                {
                    "story": story,
                    "stack": "python",
                    "stages": [["unit", "passed"], ["secrets", "passed"]],
                    "failed": 0,
                    "coverage": 78.0 + weight * 4,
                    "findings": {"high": day % 7 == 0},
                },
                at,
            )
        )
    events.append(_rec("event", "policy.violation", {"rule": "deny_command", "task": "T-1-1"}, reference))
    return {
        "contract": CONTRACT,
        "device_id": "",
        "project": project,
        "context": {"squad": squad, "stack": "python"},
        "events": events,
    }


def seed(post: Any, token_of: dict[str, str]) -> None:
    """`post(método, caminho, corpo, token)`. Recebe a chamada para funcionar com o servidor e com o teste."""
    post("PUT", "/v2/admin/directory", directory(), token_of["admin"])
    for squad, (dev, project) in SQUADS.items():
        post("POST", "/v2/events", batch(squad, project), token_of[dev])


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _call(base: str, method: str, path: str, body: Any, token: str) -> None:
    request = urllib.request.Request(  # noqa: S310
        base + path,
        data=json.dumps(body).encode("utf-8"),
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as answer:  # noqa: S310
        answer.read()


def _wait(base: str, seconds: float = 30) -> None:
    limit = time.monotonic() + seconds
    while time.monotonic() < limit:
        try:
            with urllib.request.urlopen(base + "/health", timeout=2) as answer:  # noqa: S310
                if answer.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(0.3)
    raise NexoError("o site não respondeu a tempo")


def start(port: int = 0, folder: Path | None = None) -> dict[str, Any]:
    """Sobe o servidor e carrega os dados. Só escuta em 127.0.0.1, e a base é descartável."""
    if importlib_missing():
        raise NexoError("falta o que o site precisa: rode `nexo setup`")
    base_dir = folder or Path(tempfile.mkdtemp(prefix="nexo-demo-site-"))
    base_dir.mkdir(parents=True, exist_ok=True)
    chosen = port or free_port()
    tokens = people()
    devs = {dev: {"role": "dev", "dev": dev} for dev, _project in SQUADS.values()}
    dev_tokens = {secrets.token_urlsafe(12): v for v in devs.values()}
    env = {
        **os.environ,
        "NX_COLLECTOR_DB": str(base_dir / "demo.db"),
        "NX_COLLECTOR_TOKENS": json.dumps({**tokens, **dev_tokens}),
        "NX_COLLECTOR_PSEUDO_KEY": secrets.token_urlsafe(24),
        "NX_COLLECTOR_TOKEN_LOGIN": "1",
    }
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "nexo.central.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(chosen),
            "--log-level",
            "warning",
        ],
        env=env,
    )
    url = f"http://127.0.0.1:{chosen}"
    try:
        _wait(url)
        token_of = {v["dev"]: k for k, v in {**tokens, **dev_tokens}.items()} | {
            v["role"]: k for k, v in tokens.items()
        }
        seed(lambda method, path, body, token: _call(url, method, path, body, token), token_of)
    except Exception:
        server.terminate()
        raise
    return {"url": url, "logins": {v["role"]: k for k, v in tokens.items()}, "process": server, "folder": str(base_dir)}


def importlib_missing() -> bool:
    import importlib.util

    return importlib.util.find_spec("uvicorn") is None
