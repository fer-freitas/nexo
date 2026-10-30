"""`nexo status`: visão da máquina de status local."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.workspace import tasks
from nexo.store.runs import RunStore, project_key

ACTIVE_STATES = ("EmDev", "EmQA", "EmReview", "AguardandoHumano", "Bloqueado")


def _story_state(root: Path, run_id: str) -> dict[str, Any]:
    path = root / ".nexo" / "runs" / run_id / "story.json"
    return dict(json.loads(path.read_text(encoding="utf-8"))) if path.is_file() else {}


def story_cost(ctx: AppContext, story_id: str) -> dict[str, Any]:
    """Custo ao vivo de uma história: acumulado, teto e projeção."""
    from nexo.modules.finops.app import costs

    root = ctx.project_root
    if root is None:
        raise NexoError("nenhum projeto Nexo encontrado; rode `nexo init`")
    store = RunStore(ctx.conn, project_key(root))
    st: dict[str, Any] = {}
    for run in reversed(store.runs(story_id)):  # a execução da história é a que tem story.json
        st = _story_state(root, run.id)
        if st:
            break
    return {
        "story": story_id,
        "state": store.item_state(story_id) or "Pronto",
        "cost_brl": round(sum(costs.story_costs(ctx, story_id).values()), 2),
        "cap_brl": st.get("cap_brl"),
        "projection_brl": st.get("projection_brl"),
        "estimate_brl": st.get("estimate_brl"),
    }


def overview(ctx: AppContext) -> list[dict[str, Any]]:
    root = ctx.project_root
    if root is None:
        raise NexoError("nenhum projeto Nexo encontrado; rode `nexo init`")
    from nexo.modules.finops.app import costs

    store = RunStore(ctx.conn, project_key(root))
    out: list[dict[str, Any]] = []
    for task in tasks.list_tasks(root):
        run = store.latest_run(task.id)
        out.append(
            {
                "id": task.id,
                "title": task.title,
                "state": store.item_state(task.id) or "Pronto",
                "run": run.id if run else None,
                "reason": run.reason if run else None,
                "blocked_until": run.blocked_until if run else None,
                "cost_brl": round(sum(costs.story_costs(ctx, task.id).values()), 2) if run else None,
            }
        )
    for row in ctx.conn.execute(
        "SELECT id, title FROM work_items WHERE project = ? AND type = 'story' ORDER BY id", (project_key(root),)
    ):
        run = store.latest_run(str(row["id"]))
        if run is None:
            continue
        data = story_cost(ctx, str(row["id"]))
        out.append(
            {
                "id": row["id"],
                "title": row["title"],
                "state": data["state"],
                "run": run.id,
                "reason": run.reason,
                "blocked_until": run.blocked_until,
                "cost_brl": data["cost_brl"],
                "cap_brl": data["cap_brl"],
                "projection_brl": data["projection_brl"],
            }
        )
    return out


def detail(ctx: AppContext, target: str) -> dict[str, Any]:
    root = ctx.project_root
    store = RunStore(ctx.conn, project_key(root) if root else "")
    run = store.get_run(target) or store.latest_run(target)
    task_id = run.target_id if run else target
    state = store.item_state(task_id)
    if run is None and state is None:
        raise NexoError(f"nada encontrado para {target}")
    data: dict[str, Any] = {"id": task_id, "state": state or "Pronto", "runs": []}
    for r in store.runs(task_id):
        data["runs"].append(
            {
                "id": r.id,
                "state": r.state,
                "reason": r.reason,
                "branch": r.branch,
                "started_at": r.started_at,
                "ended_at": r.ended_at,
                "blocked_until": r.blocked_until,
                "classification": r.classification,
                "steps": [
                    {
                        "seq": s["seq"],
                        "route": f"{s['tool']}/{s['model']}",
                        "outcome": s["outcome"],
                        "message": s["message"],
                        "checkpoint": (s["checkpoint_sha"] or "")[:10],
                    }
                    for s in store.steps(r.id)
                ],
                "handoffs": [
                    {"seq": h["seq"], "from": h["from_route"], "reason": h["reason"], "packet": h["packet_path"]}
                    for h in store.handoffs(r.id)
                ],
                "usage": [dict(u) for u in store.usage_for_run(r.id)],
                "progress": [p.__dict__ for p in store.progress(r.id)],
            }
        )
    return data
