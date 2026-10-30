"""Aprovação pelo Teams: o portão humano da história vira um cartão com Aprovar/Reprovar, sem terminal.

Quando a história para no portão humano e a telemetria central está ligada, a CLI registra no central só o id da
aprovação e o pseudônimo de quem disparou (o motivo e o diff não saem da máquina); o Teams recebe o cartão com o
link do formulário no central (login SSO). Quem decide é identificado pelo SSO; o central recusa a decisão de quem
disparou a história. O `nexo watch` busca as decisões e aplica: aprova ou reprova com motivo.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.platform import flags

PENDING = "approvals-remote.json"


def _pending(ctx: AppContext) -> dict[str, Any]:
    path = ctx.home / PENDING
    return dict(json.loads(path.read_text(encoding="utf-8"))) if path.is_file() else {}


def _save(ctx: AppContext, data: dict[str, Any]) -> None:
    (ctx.home / PENDING).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def enabled(ctx: AppContext) -> bool:
    from nexo.kernel.platform import telemetry

    return telemetry.enabled(ctx) and flags.enabled(ctx, "approvals_teams")


def request(ctx: AppContext, root: Path, story_id: str, run_id: str, gate: str) -> str | None:
    """Registra a aprovação no central e devolve o link do formulário (None quando não há central)."""
    if not enabled(ctx):
        return None
    from nexo.kernel.platform import telemetry
    from nexo.kernel.runtime import http

    aid = f"{telemetry.device_id(ctx)[:8]}-{run_id[:12]}-{gate}"
    url = telemetry._url(ctx)
    http.request(
        "POST",
        f"{url}/v2/approvals",
        {"Authorization": f"Bearer {telemetry._bearer(ctx)}"},
        {"id": aid, "gate": gate},
    )
    pending = _pending(ctx)
    pending[aid] = {"root": str(root), "story": story_id, "run": run_id, "gate": gate}
    _save(ctx, pending)
    ctx.audit("approval.remote_requested", {"id": aid, "story": story_id, "gate": gate}, ref=story_id)
    return f"{url}/v2/approvals/{aid}/form"


def apply(ctx: AppContext) -> list[dict[str, str]]:
    """`nexo watch`: decisões do Teams viram aprovação ou reprovação da história (identidade SSO de quem decidiu)."""
    if not enabled(ctx):
        return []
    pending = _pending(ctx)
    if not pending:
        return []
    from nexo.kernel import services
    from nexo.kernel.platform import identity, telemetry
    from nexo.kernel.runtime import http

    RunOptions = services.get("execution.run", "RunOptions")
    from nexo.kernel import services

    StoryRunner = services.get("execution.story", "StoryRunner")

    body = http.request(
        "GET",
        f"{telemetry._url(ctx)}/v2/approvals/decisions",
        {"Authorization": f"Bearer {telemetry._bearer(ctx)}"},
    ).body
    applied = []
    for dec in dict(body or {}).get("decisions") or []:
        info = pending.pop(str(dec["id"]), None)
        if info is None:
            continue
        who = identity.Identity(f"sso:{dec['by']}", verified=True)
        project = AppContext(cwd=Path(info["root"]), home=ctx.home, options=ctx.options)
        try:
            runner = StoryRunner(project, RunOptions())
            if dec["decision"] == "approve":
                runner.approve(info["story"], approver=who)
            else:
                runner.reject(info["story"], str(dec.get("reason") or "reprovado pelo Teams"), approver=who)
            project.audit(
                "approval.remote_applied",
                {"id": dec["id"], "decision": dec["decision"], "by": who.id},
                ref=info["story"],
            )
            applied.append({"story": info["story"], "decision": str(dec["decision"]), "by": who.id})
        finally:
            project.close()
    _save(ctx, pending)
    return applied
