"""O que as pessoas enxergam: board, árvore, por que está com quem, resumo da passagem e verificação."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from nexo.kernel.context import AppContext
from nexo.modules.demands.app import core
from nexo.modules.demands.domain import model as m


def _age(since: str) -> str:
    try:
        then = datetime.fromisoformat(since)
    except ValueError:
        return "—"
    hours = (datetime.now(UTC) - then).total_seconds() / 3600
    return f"{hours / 24:.0f}d" if hours >= 24 else f"{hours:.0f}h"


def board(
    ctx: AppContext,
    mine: bool = False,
    role: str = "",
    state: str = "",
    blocked: bool = False,
    origin: str = "",
    triage: bool = False,
    include_done: bool = False,
) -> list[dict[str, Any]]:
    demands = core.load(ctx)
    me = core._actor(ctx)
    rows = []
    for d in demands.values():
        if d.type == "task":
            continue
        stage = m.stage_of(d, demands)
        if stage in ("done", "cancelled") and not include_done:
            continue
        if triage != (stage == "triage") and (triage or stage == "triage"):
            continue
        people = m.holders_of(d, demands)
        if mine and not any(h["who"] == me for h in people):
            continue
        if role and not any(h["role"] == role for h in people):
            continue
        if state and d.state != state and stage != state:
            continue
        blocks = m.blocked_by(d)
        if blocked and not (blocks or d.state in ("Bloqueado", "Escalado")):
            continue
        if origin and d.origin.get("kind") != origin:
            continue
        rows.append(
            {
                "project": d.project,
                "id": d.id,
                "type": d.type,
                "title": d.title,
                "stage": stage,
                "state": d.state,
                "with": [f"{h['role']}:{h['who'] or 'fila'}" for h in people],
                "age": _age(d.since),
                "origin": d.origin.get("kind", ""),
                "blocked_by": blocks,
            }
        )
    return sorted(rows, key=lambda r: (str(r["project"]), str(r["id"])))


def tree(ctx: AppContext, did: str) -> dict[str, Any]:
    d, demands = core.get(ctx, did)

    def node(x: m.Demand) -> dict[str, Any]:
        spawned = [y.id for y in demands.values() if y.spawned_by == x.id and y.id not in x.children]
        kids = [*x.children, *spawned]
        return {
            "id": x.id,
            "type": x.type,
            "title": x.title,
            "stage": m.stage_of(x, demands),
            "with": [f"{h['role']}:{h['who'] or 'fila'}" for h in m.holders_of(x, demands)],
            "children": [node(demands[k]) for k in kids if k in demands],
        }

    return node(d)


def why(ctx: AppContext, did: str) -> dict[str, Any]:
    d, demands = core.get(ctx, did)
    return {
        "id": d.id,
        "stage": m.stage_of(d, demands),
        "text": m.why(d, demands),
        "holders": m.holders_of(d, demands),
        "blocked_by": m.blocked_by(d),
        "premises": [p.id for p in d.open_premises],
    }


NEXT = {
    "functional": "nexo pm refine {id}",
    "technical": "nexo tl analyze {id}",
    "validation": "nexo qa trace {id}",
    "gate": "nexo tl gate {id}",
    "pr": "nexo dev pr",
    "accept": "nexo pm accept {id}",
    "build": "nexo dev run {id}",
    "verify": "nexo qa verify {id}",
    "idea": "nexo pm decide {id} build --reason ...",
    "ready": "nexo tl ready {id}",
    "triage": "nexo demand triage {id}",
}


def brief(ctx: AppContext, did: str) -> dict[str, Any]:
    """Resumo da passagem, sem IA: vem dos eventos, então sai na hora e sem custo."""
    d, demands = core.get(ctx, did)
    stage = m.stage_of(d, demands)
    me = core._actor(ctx)
    t = core.team(ctx)
    my_roles = set(t.roles_of(me)) if t.declared else set(m.ROLE_LABEL)
    events = [e for e in core.store(ctx).events() if e.get("demand") == d.id]
    last_mine = max((str(e["id"]) for e in events if e.get("actor") == me), default="")
    changes = [e for e in events if str(e["id"]) > last_mine and e.get("actor") != me]
    sender = next((str(e.get("actor")) for e in reversed(changes)), "")
    people = m.holders_of(d, demands)
    mine = [h for h in people if h["who"] == me or (not h["who"] and h["role"] in my_roles)]
    decided = [
        f"{p.text} ({p.id}, {p.status} por {p.decided_by})" for p in d.proposals if p.status in ("accepted", "assumed")
    ]
    if d.type == "story" and d.parent in demands:
        parent = demands[d.parent]
        decided += [f"{p.text} ({p.id}, na feature)" for p in parent.proposals if p.status == "accepted"]
    open_items = [f"{p.id}: {p.text} ({'bloqueante, ' if p.blocking else ''}com {p.owner_role})" for p in d.proposals if p.open]
    command = next((v.format(id=d.id) for k, v in NEXT.items() if k == stage), "")
    if stage == "refinement" and mine:
        command = f"nexo {mine[0]['role']} {'plan' if mine[0]['role'] == 'qa' else 'refine'} {d.id}"
    return {
        "id": d.id,
        "title": d.title,
        "stage": stage,
        "from": sender,
        "age": _age(d.since),
        "expected": [h["action"] for h in mine] or [h["action"] for h in people],
        "for_you": bool(mine),
        "decided": decided,
        "open": open_items,
        "changes_since_you": len(changes),
        "next": command,
    }


def verify(ctx: AppContext) -> dict[str, Any]:
    """Refaz a projeção a partir dos eventos e confere que toda demanda aberta tem alguém responsável por agir."""
    store = core.store(ctx)
    events = store.events()
    ok_ids = {str(e.get("id")) for e in events}
    bad_files = []
    folder = store.events_dir()
    if folder.is_dir():
        for path in folder.rglob("*.json"):
            try:
                event = json.loads(path.read_text(encoding="utf-8"))
                if str(event.get("id")) != path.stem:
                    bad_files.append(str(path))
            except ValueError:
                bad_files.append(str(path))
    demands = m.project(events)
    orphans = [
        d.id
        for d in demands.values()
        if m.stage_of(d, demands) not in ("done", "cancelled") and not m.holders_of(d, demands)
    ]
    return {"events": len(ok_ids), "demands": len(demands), "bad_files": bad_files, "orphans": orphans, "ok": not bad_files and not orphans}
