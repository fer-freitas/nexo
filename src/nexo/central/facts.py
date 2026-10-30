"""Modelo de fatos do central: consolidação idempotente a partir de `raw_events`.

A mesma função (`apply`) roda na ingestão e no reprocessamento: passo, portão, qualidade e evento entram por
`event_id` (inserir de novo não muda nada); história e classificação de tarefa guardam o retrato mais recente
(`at`, com desempate por `event_id`) — a ordem de chegada não altera o resultado.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from nexo.central.db import CollectorDB
from nexo.kernel.quality.standards import RULE_ID

STEP_COLUMNS = (
    "step",
    "run",
    "target",
    "story",
    "role",
    "tool",
    "model",
    "started_at",
    "seconds",
    "outcome",
    "error_kind",
    "tokens_in",
    "tokens_out",
    "cache_read",
    "cache_write",
    "premium_request",
    "acu",
    "ai_credits",
    "api_equiv_usd",
    "usage_brl",
    "estimated",
    "brain_version",
    "tool_calls",
)
STORY_COLUMNS = (
    "feature",
    "points",
    "state",
    "human_gate",
    "escalated",
    "bugs",
    "attempts",
    "spec_returns",
    "clarifications",
    "handoffs",
    "lead_hours",
    "cycle_hours",
    "waiting_human_hours",
    "cost_brl",
    "cap_brl",
    "estimate_brl",
    "started_at",
    "done_at",
)
EVENT_FACTS = frozenset(
    {
        "run.blocked",
        "run.handoff",
        "run.escalated",
        "run.complete",
        "story.escalated",
        "story.cost_alert",
        "bug.created",
        "policy.violation",
        "quality.flaky",
        "finops.estimate",
        "approval.requested",
        "approval.resolved",
        "budget.granted",
    }
)
USAGE_COLUMNS = (
    "tool",
    "model",
    "sessions",
    "responses",
    "tokens_in",
    "tokens_out",
    "cache_read",
    "cache_write",
    "premium_request",
    "ai_credits",
    "acu",
    "api_equiv_usd",
    "usage_brl",
)
USAGE_UPSERT = (
    "INSERT INTO fact_external_usage (device_id, dev_pid, squad, month, day, "
    + ", ".join(USAGE_COLUMNS)
    + ", at) VALUES ("
    + ", ".join("?" * (len(USAGE_COLUMNS) + 6))
    + ") ON CONFLICT (device_id, tool, model, day) DO UPDATE SET "
    + ", ".join(f"{c} = excluded.{c}" for c in ("dev_pid", "squad", "sessions", "responses", *USAGE_COLUMNS[4:], "at"))
    + " WHERE excluded.at > fact_external_usage.at"
)
EVENT_COLUMNS = ("story", "task", "feature", "bug", "tool", "kind", "source", "rule", "points", "brl")
FACT_TABLES = (
    "fact_step",
    "fact_story",
    "fact_gate",
    "fact_quality",
    "fact_standards_rule",
    "fact_event",
    "fact_task",
    "fact_external_usage",
)

# colunas fixas do contrato; valores sempre parametrizados
STEP_INSERT = (
    f"INSERT INTO fact_step (event_id, dev_pid, project, month, squad, stack, {', '.join(STEP_COLUMNS)})"
    f" VALUES ({', '.join('?' * (6 + len(STEP_COLUMNS)))}) ON CONFLICT DO NOTHING"
)
STORY_UPSERT = (
    "INSERT INTO fact_story (project, story, event_id, at, squad, stack, dev_pid, month,"
    f" {', '.join(STORY_COLUMNS)}) VALUES ({', '.join('?' * (8 + len(STORY_COLUMNS)))})"
    " ON CONFLICT (project, story) DO UPDATE SET "
    + ", ".join(f"{c} = excluded.{c}" for c in ("event_id", "at", "squad", "stack", "dev_pid", "month", *STORY_COLUMNS))
    + " WHERE excluded.at > fact_story.at OR (excluded.at = fact_story.at AND excluded.event_id > fact_story.event_id)"
)
EVENT_INSERT = (
    "INSERT INTO fact_event (event_id, at, month, type, project, squad, stack, dev_pid,"
    f" {', '.join(EVENT_COLUMNS)}) VALUES ({', '.join('?' * (8 + len(EVENT_COLUMNS)))}) ON CONFLICT DO NOTHING"
)


def _month(value: Any) -> str:
    return str(value or "")[:7]


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _story_of(payload: dict[str, Any]) -> str | None:
    """História de um evento: `story`, ou tarefa/item de história (US-1-T2 → US-1)."""
    for key in ("story", "item", "task"):
        value = payload.get(key)
        if isinstance(value, str) and value.startswith("US-"):
            return value.split("-QA")[0].split("-FIX")[0].split("-CHAR")[0].rsplit("-T", 1)[0]
    return None


def _demand(d: CollectorDB, raw: dict[str, Any], payload: dict[str, Any], base: tuple[str, str, str]) -> None:
    """Estado atual de cada demanda, para o board: o evento mais novo vence, em qualquer ordem de chegada."""
    current = d.one("SELECT updated_at, demand_type, title, parent, origin FROM fact_demand WHERE project = ? AND demand = ?", (base[0], payload.get("demand")))
    if current is not None and str(current["updated_at"]) > str(raw["at"]):
        return
    d.execute("DELETE FROM fact_demand WHERE project = ? AND demand = ?", (base[0], payload.get("demand")))
    keep = current or {}
    d.execute(
        "INSERT INTO fact_demand (project, demand, squad, demand_type, stage, origin, title, parent, holders, blocked, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            base[0],
            payload.get("demand"),
            base[1],
            payload.get("demand_type") or keep.get("demand_type"),
            payload.get("stage"),
            payload.get("origin") or keep.get("origin"),
            payload.get("title") or keep.get("title"),
            payload.get("parent") or keep.get("parent"),
            payload.get("holders"),
            int(payload.get("blocked") or 0),
            raw["at"],
        ),
    )


def apply(d: CollectorDB, raw: dict[str, Any]) -> None:
    """Consolida um registro de `raw_events` (chamado dentro da transação da ingestão ou do reprocessamento)."""
    payload = raw["payload"] if isinstance(raw["payload"], dict) else json.loads(raw["payload"] or "{}")
    kind, type_ = raw["kind"], raw["type"]
    base = (raw["project"], raw.get("squad") or "", raw.get("stack") or "")
    if kind == "step":
        values = [payload.get(c) for c in STEP_COLUMNS]
        values[STEP_COLUMNS.index("estimated")] = int(bool(payload.get("estimated")))
        month = _month(payload.get("started_at") or raw["at"])
        d.execute(STEP_INSERT, (raw["event_id"], raw["dev_pid"], base[0], month, base[1], base[2], *values))
    elif type_ in ("demand.created", "demand.transitioned"):
        _demand(d, raw, payload, base)
    elif kind == "usage":
        values = [payload.get(c) for c in USAGE_COLUMNS]
        day = str(payload.get("day") or "")
        d.execute(
            USAGE_UPSERT,
            (raw.get("device_id") or "", raw["dev_pid"], base[1], day[:7], day, *values, raw["at"]),
        )
    elif kind == "story":
        values = [payload.get(c) for c in STORY_COLUMNS]
        for flag in ("human_gate", "escalated"):
            values[STORY_COLUMNS.index(flag)] = int(bool(payload.get(flag)))
        month = _month(payload.get("done_at") or payload.get("started_at") or raw["at"])
        stack = str(payload.get("stack") or base[2])
        d.execute(
            STORY_UPSERT,
            (base[0], payload.get("story"), raw["event_id"], raw["at"], base[1], stack, raw["dev_pid"], month, *values),
        )
    elif type_ == "gate.decision":
        d.execute(
            "INSERT INTO fact_gate (event_id, at, month, project, squad, stack, dev_pid, story, feature, gate,"
            " decision, score, threshold, kind) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
            (
                raw["event_id"],
                raw["at"],
                _month(raw["at"]),
                *base,
                raw["dev_pid"],
                payload.get("story"),
                payload.get("feature"),
                payload.get("gate"),
                payload.get("decision"),
                _num(payload.get("score")),
                _num(payload.get("threshold")),
                payload.get("kind"),
            ),
        )
    elif type_ == "quality.run":
        raw_findings = payload.get("findings")
        findings: dict[str, Any] = raw_findings if isinstance(raw_findings, dict) else {}
        raw_std = payload.get("standards")
        std: dict[str, Any] = raw_std if isinstance(raw_std, dict) else {}
        d.execute(
            "INSERT INTO fact_quality (event_id, at, month, project, squad, stack, dev_pid, story, failed, coverage,"
            " findings_critical, findings_high, findings_medium, findings_low, standards_files, standards_ok,"
            " standards_violations, standards_errors) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT DO NOTHING",
            (
                raw["event_id"],
                raw["at"],
                _month(raw["at"]),
                base[0],
                base[1],
                str(payload.get("stack") or base[2]),
                raw["dev_pid"],
                _story_of(payload),
                int(payload.get("failed") or 0),
                _num(payload.get("coverage")),
                int(findings.get("critical", 0)),
                int(findings.get("high", 0)),
                int(findings.get("medium", 0)),
                int(findings.get("low", 0)) + int(findings.get("note", 0)),
                _int(std.get("files")),
                _int(std.get("files_ok")),
                _int(std.get("violations")),
                _int(std.get("errors")),
            ),
        )
        rules = std.get("rules") if isinstance(std.get("rules"), dict) else {}
        for rule, count in sorted((rules or {}).items()):  # violações por regra (id da regra, nunca texto)
            if isinstance(count, int) and RULE_ID.match(str(rule)):
                d.execute(
                    "INSERT INTO fact_standards_rule (event_id, rule, at, month, project, squad, stack, count)"
                    " VALUES (?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                    (
                        raw["event_id"],
                        str(rule),
                        raw["at"],
                        _month(raw["at"]),
                        base[0],
                        base[1],
                        str(payload.get("stack") or base[2]),
                        count,
                    ),
                )
    elif type_ == "route.classified" and payload.get("task"):
        d.execute(
            "INSERT INTO fact_task (project, task, event_id, at, kind, size) VALUES (?,?,?,?,?,?)"
            " ON CONFLICT (project, task) DO UPDATE SET event_id = excluded.event_id, at = excluded.at,"
            " kind = excluded.kind, size = excluded.size WHERE excluded.at > fact_task.at"
            " OR (excluded.at = fact_task.at AND excluded.event_id > fact_task.event_id)",
            (base[0], payload["task"], raw["event_id"], raw["at"], payload.get("kind"), payload.get("size")),
        )
    elif type_ in EVENT_FACTS:
        values = [payload.get(c) for c in EVENT_COLUMNS]
        values[0] = _story_of(payload)
        if type_ == "finops.estimate":
            values[EVENT_COLUMNS.index("feature")] = payload.get("target")
        if type_ == "run.handoff" and not values[EVENT_COLUMNS.index("tool")]:
            values[EVENT_COLUMNS.index("tool")] = str(payload.get("from") or "").split("/")[0] or None
        values[EVENT_COLUMNS.index("points")] = _num(payload.get("points"))
        values[EVENT_COLUMNS.index("brl")] = _num(payload.get("brl"))
        d.execute(
            EVENT_INSERT,
            (raw["event_id"], raw["at"], _month(raw["at"]), type_, *base, raw["dev_pid"], *values),
        )


def reprocess(d: CollectorDB, batch: int = 2000) -> int:
    """Reconstrói todos os fatos a partir de `raw_events` (evolução do contrato ou correção de consolidação)."""
    total = 0
    with d.tx():
        for table in FACT_TABLES:
            d.execute(f"DELETE FROM {table}")
        last: tuple[str, str] = ("", "")
        while True:
            rows = d.query(
                "SELECT * FROM raw_events WHERE at > ? OR (at = ? AND event_id > ?) ORDER BY at, event_id LIMIT ?",
                (last[0], last[0], last[1], batch),
            )
            if not rows:
                break
            for row in rows:
                apply(d, row)
            total += len(rows)
            last = (rows[-1]["at"], rows[-1]["event_id"])
    return total


def fingerprint(d: CollectorDB) -> str:
    """Resumo do conteúdo dos fatos (reprocessar deve dar o mesmo resultado)."""
    digest = hashlib.sha256()
    for table in FACT_TABLES:
        rows = d.query(f"SELECT * FROM {table}")
        for line in sorted(json.dumps(r, sort_keys=True, default=str) for r in rows):
            digest.update(line.encode())
    return digest.hexdigest()
