"""Feature flags no central: fonte remota de maior precedência, com histórico auditado.

Também governa as funcionalidades do próprio coletor (dashboard, resumo semanal, relatório de FinOps, issues
sistêmicas, alertas em tempo real, insights e export para BI), globalmente ou por squad.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from nexo.central.db import CollectorDB
from nexo.kernel.config.flags import BY_KEY, FlagSource, resolve

PROJECT_PREFIX = "project:"


def snapshot(d: CollectorDB) -> dict[str, Any]:
    global_ = {str(r["key"]): bool(r["value"]) for r in d.query("SELECT key, value FROM flags WHERE squad = ''")}
    squads: dict[str, dict[str, bool]] = {}
    projects: dict[str, dict[str, bool]] = {}
    for r in d.query("SELECT key, squad, value FROM flags WHERE squad <> ''"):
        scope = str(r["squad"])
        if scope.startswith(PROJECT_PREFIX):
            projects.setdefault(scope.removeprefix(PROJECT_PREFIX), {})[str(r["key"])] = bool(r["value"])
        else:
            squads.setdefault(scope, {})[str(r["key"])] = bool(r["value"])
    return {"global": global_, "squads": squads, "projects": projects}


def enabled(d: CollectorDB, key: str, squad: str = "") -> bool:
    snap = snapshot(d)
    return resolve(key, squad, [FlagSource("central", snap["global"], snap["squads"])]).enabled


def set_flag(d: CollectorDB, key: str, value: bool | None, squad: str, actor: str, reason: str) -> dict[str, Any]:
    from nexo.kernel.modules import FLOOR

    if key in FLOOR or key.split(".")[-1] in FLOOR:
        raise PermissionError(key)
    if key not in BY_KEY:
        raise KeyError(key)
    old = d.one("SELECT value FROM flags WHERE key = ? AND squad = ?", (key, squad))
    d.execute("DELETE FROM flags WHERE key = ? AND squad = ?", (key, squad))
    if value is not None:
        d.execute(
            "INSERT INTO flags (key, squad, value, updated_at, updated_by, reason) VALUES (?,?,?,?,?,?)",
            (key, squad, int(value), time.time(), actor, reason),
        )
    d.execute(
        "INSERT INTO flag_history (id, key, squad, from_value, to_value, at, actor, reason) VALUES (?,?,?,?,?,?,?,?)",
        (
            uuid.uuid4().hex,
            key,
            squad,
            None if old is None else int(old["value"]),
            None if value is None else int(value),
            time.time(),
            actor,
            reason,
        ),
    )
    return {"key": key, "squad": squad or None, "from": None if old is None else bool(old["value"]), "to": value}


def history(d: CollectorDB, limit: int = 50) -> list[dict[str, Any]]:
    return d.query(
        "SELECT key, squad, from_value, to_value, at, actor, reason FROM flag_history ORDER BY at DESC LIMIT ?",
        (limit,),
    )
