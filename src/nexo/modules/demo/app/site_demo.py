"""`nexo demo site`: o portal em 127.0.0.1, com SQLite e dados fictícios de três squads."""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
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


def _story_events(story: str, day: int, weight: int, at: datetime) -> list[dict[str, Any]]:
    tool = ("claude", "devin", "copilot")[day % 3]
    cost = round(4.0 + weight * 1.7 + day % 5, 2)
    human = day % 4 == 0
    waiting = day % 9 == 0
    stamp = at.isoformat(timespec="milliseconds")
    step = {
        "target": story,
        "story": story,
        "role": "developer",
        "tool": tool,
        "model": "sonnet",
        "started_at": stamp,
        "seconds": 40.0 + day,
        "outcome": "success",
        "usage_brl": cost,
        "api_equiv_usd": round(cost / 5.4, 4),
        "acu": 2.0 if tool == "devin" else None,
        "brain_version": "demo",
    }
    snapshot = {
        "story": story,
        "feature": f"F-{day + 1}",
        "stack": "python",
        "points": 1 + day % 3,
        "state": "AguardandoHumano" if waiting else "Concluido",
        "human_gate": human,
        "escalated": False,
        "bugs": 1 if day % 5 == 0 else 0,
        "attempts": 1 + day % 2,
        "spec_returns": 0,
        "clarifications": day % 2,
        "handoffs": 0,
        "cost_brl": cost,
        "cap_brl": 45.0,
        "estimate_brl": cost + 1,
        "started_at": (at - timedelta(hours=6)).isoformat(timespec="milliseconds"),
        "done_at": None if waiting else stamp,
        "waiting_human_hours": 3.0 if human else 0.0,
    }
    gate = {
        "gate": "code",
        "story": story,
        "decision": "human" if human else "auto",
        "score": 0.78 if human else 0.96,
        "threshold": 0.9,
    }
    quality = {
        "story": story,
        "stack": "python",
        "stages": [["unit", "passed"], ["secrets", "passed"]],
        "failed": 0,
        "coverage": 78.0 + weight * 4,
        "findings": {"high": 1 if day % 7 == 0 else 0},
    }
    return [
        _rec("step", "step", step, at),
        _rec("story", "story", snapshot, at),
        _rec("event", "gate.decision", gate, at),
        _rec("event", "quality.run", quality, at),
    ]


DEMANDS = (
    ("F-101", "feature", "technical", "Simulador de parcelas", 0, "tl:demo.lead"),
    ("US-101-1", "story", "build", "Mostrar parcelas no extrato", 0, "dev:demo.bia"),
    ("US-101-2", "story", "qa", "Validar arredondamento", 1, "qa:demo.qa"),
    ("IDEA-12", "idea", "split", "Aviso de limite por aplicativo", 0, "pm:demo.pm"),
    ("BUG-101-1", "bug", "build", "Parcela zero aceita no simulador", 1, "dev:demo.bia"),
)


def _demand_events(squad: str, at: datetime) -> list[dict[str, Any]]:
    prefix = squad.removeprefix("sq-")[:3].upper()
    return [
        _rec(
            "event",
            "demand.created",
            {
                "demand": f"{demand}-{prefix}",
                "demand_type": kind,
                "stage": stage,
                "title": title,
                "blocked": blocked,
                "holders": holder,
                "origin": "manual",
            },
            at,
        )
        for demand, kind, stage, title, blocked, holder in DEMANDS
    ]


def batch(squad: str, project: str, months: int = 6, per_month: int = 6) -> dict[str, Any]:
    """Seis meses de trabalho fictício, com variação por squad só para o painel ter o que mostrar."""
    now = datetime.now(UTC)
    reference = now
    weight = 1 + sum(map(ord, squad)) % 3
    events: list[dict[str, Any]] = []
    first_of_month = now.replace(day=1, hour=9, minute=0, second=0, microsecond=0)
    for back in range(months):
        start = (first_of_month - timedelta(days=30 * back)).replace(day=1)
        for n in range(per_month + (months - back)):
            at = start + timedelta(days=(n * 3) % 27, hours=n)
            if at > now:
                at = now - timedelta(hours=n + 1)
            day = back * 7 + n
            story = f"US-{back + 1}-{n + 1}"
            events += _story_events(story, day, weight, at)
    events += _demand_events(squad, reference)
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
    month = datetime.now(UTC).strftime("%Y-%m")
    for squad, (dev, project) in SQUADS.items():
        whole = batch(squad, project)
        events = whole["events"]
        for i in range(0, len(events), 80):  # cada envio tem limite de tamanho
            post("POST", "/v2/events", {**whole, "events": events[i : i + 80]}, token_of[dev])
        post("PUT", f"/v1/budgets/{dev}", {"month": month, "limit_brl": 400.0}, token_of["admin"])


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
    from nexo.kernel import services

    command = services.get("execution.agents", "nexo_command")()
    server = subprocess.Popen([*command, "central", "serve", "--port", str(chosen)], env=env)
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
