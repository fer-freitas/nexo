"""Autonomia por squad × área no central: nível vigente, elegibilidade, promoção e rebaixamento."""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from nexo.central.db import CollectorDB
from nexo.modules.execution.domain.autonomy import (
    DEFAULT_LEVEL,
    LEVELS,
    Criteria,
    Eligibility,
    demoted,
    eligibility,
    index,
)


def default_level() -> str:
    level = os.environ.get("NX_COLLECTOR_AUTONOMY_DEFAULT", DEFAULT_LEVEL)
    return level if level in LEVELS else DEFAULT_LEVEL


def level(d: CollectorDB, squad: str, area: str) -> str:
    row = d.one("SELECT level FROM autonomy WHERE squad = ? AND area = ?", (squad, area))
    return str(row["level"]) if row else default_level()


def _ts(value: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def escapes(d: CollectorDB, squad: str, area: str | None, days: int = 30) -> int:
    """Bugs abertos depois da conclusão da história (até 30 dias), no período — mesma regra do I-08."""
    since = datetime.now(UTC) - timedelta(days=days)
    done = {
        (str(r["project"]), str(r["story"])): _ts(r["done_at"])
        for r in d.query(
            "SELECT project, story, done_at, stack FROM fact_story WHERE squad = ? AND done_at IS NOT NULL", (squad,)
        )
        if area is None or r["stack"] == area
    }
    count = 0
    for b in d.query("SELECT project, story, at FROM fact_event WHERE type = 'bug.created' AND squad = ?", (squad,)):
        finished, at = done.get((str(b["project"]), str(b["story"]))), _ts(b["at"])
        if finished and at and at >= since and finished < at <= finished + timedelta(days=30):
            count += 1
    return count


def check(d: CollectorDB, squad: str, area: str) -> Eligibility:
    rows = d.query(
        "SELECT bugs FROM fact_story WHERE squad = ? AND stack = ? AND done_at IS NOT NULL ORDER BY done_at DESC",
        (squad, area),
    )
    crit = Criteria(
        min_stories=int(os.environ.get("NX_COLLECTOR_AUTONOMY_MIN_STORIES", "20")),
        min_fpy=float(os.environ.get("NX_COLLECTOR_AUTONOMY_MIN_FPY", "0.85")),
    )
    return eligibility(level(d, squad, area), rows, escapes(d, squad, area), crit)


def set_level(d: CollectorDB, squad: str, area: str, new: str, actor: str, reason: str, kind: str) -> dict[str, Any]:
    old = level(d, squad, area)
    index(new)
    d.execute("DELETE FROM autonomy WHERE squad = ? AND area = ?", (squad, area))
    d.execute(
        "INSERT INTO autonomy (squad, area, level, updated_at, updated_by, reason) VALUES (?,?,?,?,?,?)",
        (squad, area, new, time.time(), actor, reason),
    )
    d.execute(
        "INSERT INTO autonomy_history (id, squad, area, from_level, to_level, at, actor, reason, kind)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (uuid.uuid4().hex, squad, area, old, new, time.time(), actor, reason, kind),
    )
    return {"squad": squad, "area": area, "from": old, "to": new, "kind": kind}


def areas_of(d: CollectorDB, squad: str) -> list[str]:
    found = {
        str(r["stack"])
        for r in d.query("SELECT DISTINCT stack FROM fact_story WHERE squad = ?", (squad,))
        if r["stack"]
    }
    found |= {str(r["area"]) for r in d.query("SELECT area FROM autonomy WHERE squad = ?", (squad,))}
    return sorted(found)


def auto_demote(d: CollectorDB, squad: str, areas: list[str], reason: str) -> list[dict[str, Any]]:
    """Escape grave ou incidente do Nexo rebaixa um nível na hora (auditado e avisado no Teams do squad)."""
    from nexo.central import distribution
    from nexo.central.common import audit

    changes = []
    for area in areas:
        current = level(d, squad, area)
        if index(current) == 0:
            continue
        change = set_level(d, squad, area, demoted(current), "nexo", reason, "auto")
        audit("nexo", "autonomy.demoted", change | {"reason": reason})
        changes.append(change)
        url = distribution._webhooks().get(squad) or distribution._webhooks().get("default")
        if url:
            card = distribution.card(
                f"Nexo — autonomia rebaixada · squad {squad}",
                [("área", area), ("nível", f"{change['from']} → {change['to']}")],
                [reason],
                None,
            )
            distribution._deliver(d, "teams", "autonomy", squad, url, card)
    return changes


def on_bug(d: CollectorDB, raw: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Bug depois da conclusão (fonte externa: tracker/ServiceNow) é escape → rebaixa a área da história."""
    if str(payload.get("source") or "") not in ("tracker", "servicenow", "incident", "production"):
        return []
    story = d.one(
        "SELECT squad, stack, done_at FROM fact_story WHERE project = ? AND story = ?",
        (raw["project"], payload.get("story")),
    )
    if story is None or not story["done_at"]:
        return []
    done_at, at = _ts(story["done_at"]), _ts(raw["at"])
    if not (done_at and at and done_at < at <= done_at + timedelta(days=30)):
        return []
    return auto_demote(
        d,
        str(story["squad"] or ""),
        [str(story["stack"] or "")],
        f"escape: bug {payload.get('bug')} em {payload.get('story')} após a conclusão",
    )


def history(d: CollectorDB, squad: str) -> list[dict[str, Any]]:
    rows = d.query("SELECT * FROM autonomy_history WHERE squad = ? ORDER BY at DESC", (squad,))
    return [
        {k: r[k] for k in ("area", "from_level", "to_level", "actor", "reason", "kind")}
        | {"at": datetime.fromtimestamp(float(r["at"]), UTC).isoformat(timespec="seconds")}
        for r in rows
    ]


def summary(d: CollectorDB, squad: str) -> list[dict[str, Any]]:
    return [{"area": a, **check(d, squad, a).as_dict()} for a in areas_of(d, squad)]


def as_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
