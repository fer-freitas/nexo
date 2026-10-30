"""Leitura e estado das ocorrências: visibilidade, listagem por escopo e mudança de estado."""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from nexo.central.common import audit, pid_of
from nexo.central.db import CollectorDB
from nexo.central.visibility import Principal
from nexo.kernel.finops.insights import can_transition


def visible(principal: Principal, row: dict[str, Any]) -> bool:
    if row.get("dev_pid"):
        if pid_of(principal.dev) == row["dev_pid"]:
            return True
        return principal.individual(str(row.get("purpose") or ""), row.get("squad"))
    if row.get("squad") is not None and row.get("scope_kind") != "org":
        return principal.sees_squad(row.get("squad"))
    return True


def _public(row: dict[str, Any]) -> dict[str, Any]:
    out = {
        k: row[k]
        for k in (
            "id",
            "detector",
            "title",
            "scope_kind",
            "squad",
            "severity",
            "owner",
            "action",
            "state",
            "reason",
            "period",
            "issue_url",
        )
    }
    out["evidence"] = json.loads(row["evidence"] or "{}")
    out["first_seen"] = datetime.fromtimestamp(float(row["first_seen"]), UTC).isoformat(timespec="seconds")
    out["individual"] = bool(row.get("dev_pid"))
    return out


SEVERITY_ORDER = {"alta": 0, "media": 1, "baixa": 2}


def list_for(d: CollectorDB, principal: Principal, state: str | None = None) -> list[dict[str, Any]]:
    rows = d.query("SELECT * FROM insights" + (" WHERE state = ?" if state else ""), (state,) if state else ())
    rows = [r for r in rows if visible(principal, r)]
    rows.sort(key=lambda r: (SEVERITY_ORDER.get(str(r["severity"]), 3), -float(r["first_seen"]), str(r["id"])))
    return [_public(r) for r in rows]


def set_state(d: CollectorDB, principal: Principal, insight_id: str, state: str, reason: str) -> dict[str, Any]:
    row = d.one("SELECT * FROM insights WHERE id = ?", (insight_id,))
    if row is None or not visible(principal, row):
        raise HTTPException(404, "insight não encontrado")
    own = bool(row["dev_pid"]) and pid_of(principal.dev) == row["dev_pid"]
    if principal.role == "dev" and not own:
        raise HTTPException(403, "dev só atualiza insights do próprio painel")
    if not can_transition(str(row["state"]), state):
        raise HTTPException(409, f"transição inválida: {row['state']} → {state}")
    if state == "descartado" and len(reason.strip()) < 10:
        raise HTTPException(400, "descartar exige motivo (silêncio de 7 dias)")
    now = time.time()
    d.execute(
        "UPDATE insights SET state = ?, reason = ?, updated_at = ?, updated_by = ? WHERE id = ?",
        (state, reason.strip() or None, now, principal.dev, insight_id),
    )
    d.execute(
        "INSERT INTO insight_history (id, insight, at, actor, from_state, to_state, reason) VALUES (?,?,?,?,?,?,?)",
        (uuid.uuid4().hex, insight_id, now, principal.dev, row["state"], state, reason.strip() or None),
    )
    audit(principal.dev, "insight.state", {"insight": insight_id, "from": row["state"], "to": state})
    fresh = d.one("SELECT * FROM insights WHERE id = ?", (insight_id,))
    return _public(fresh or row)
