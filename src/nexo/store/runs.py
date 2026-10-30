"""Repositório de itens de trabalho, execuções e seus artefatos."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexo.kernel.models.agent import UsageRecord
from nexo.kernel.workflow.handoff import ProgressEntry


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def project_key(root: Path) -> str:
    return str(root.resolve())


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


@dataclass
class RunRow:
    id: str
    target_id: str
    project: str
    dev: str
    profile: str
    state: str
    reason: str | None
    branch: str | None
    worktree: str | None
    base_sha: str | None
    brain_version: str | None
    classification: dict[str, Any] | None
    blocked_until: float | None
    started_at: str
    ended_at: str | None


class RunStore:
    """`scope` = identificador do projeto (caminho absoluto da raiz). Itens e buscas por alvo são escopados."""

    def __init__(self, conn: sqlite3.Connection, scope: str = "") -> None:
        self.conn = conn
        self.scope = scope

    def _project(self, project: str) -> str:
        return self.scope or project

    # ---- itens de trabalho ----
    def upsert_item(self, item_id: str, type_: str, title: str, project: str, source: str, state: str) -> str:
        key = self._project(project)
        row = self.conn.execute("SELECT state FROM work_items WHERE project = ? AND id = ?", (key, item_id)).fetchone()
        if row:
            self.conn.execute(
                "UPDATE work_items SET title = ?, source = ?, type = ? WHERE project = ? AND id = ?",
                (title, source, type_, key, item_id),
            )
            return str(row["state"])
        self.conn.execute(
            "INSERT INTO work_items(project, id, type, title, state, source, updated_at) VALUES (?,?,?,?,?,?,?)",
            (key, item_id, type_, title, state, source, now_iso()),
        )
        return state

    def item_state(self, item_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT state FROM work_items WHERE project = ? AND id = ?", (self.scope, item_id)
        ).fetchone()
        return str(row["state"]) if row else None

    def set_item_state(self, item_id: str, state: str) -> None:
        self.conn.execute(
            "UPDATE work_items SET state = ?, updated_at = ? WHERE project = ? AND id = ?",
            (state, now_iso(), self.scope, item_id),
        )

    def items(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM work_items WHERE project = ? ORDER BY updated_at DESC", (self.scope,)
        ).fetchall()

    # ---- execuções ----
    def create_run(self, target_id: str, project: str, dev: str, profile: str, brain_version: str | None) -> str:
        run_id = new_id("run")
        self.conn.execute(
            "INSERT INTO runs(id, target_id, project, dev, profile, state, brain_version, started_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (run_id, target_id, self._project(project), dev, profile, "EmDev", brain_version, now_iso()),
        )
        return run_id

    def update_run(self, run_id: str, **fields: Any) -> None:
        if "classification" in fields and fields["classification"] is not None:
            fields["classification"] = json.dumps(fields["classification"], ensure_ascii=False)
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE runs SET {cols} WHERE id = ?", (*fields.values(), run_id))  # noqa: S608

    def get_run(self, run_id: str) -> RunRow | None:
        row = self.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return _run(row) if row else None

    def latest_run(self, target_id: str) -> RunRow | None:
        row = self.conn.execute(
            "SELECT * FROM runs WHERE target_id = ? AND project = ? ORDER BY started_at DESC, rowid DESC LIMIT 1",
            (target_id, self.scope),
        ).fetchone()
        return _run(row) if row else None

    def runs(self, target_id: str) -> list[RunRow]:
        rows = self.conn.execute(
            "SELECT * FROM runs WHERE target_id = ? AND project = ? ORDER BY started_at", (target_id, self.scope)
        ).fetchall()
        return [_run(r) for r in rows]

    # ---- passos ----
    def start_step(self, run_id: str, role: str, tool: str, model: str) -> str:
        seq = int(self.conn.execute("SELECT COUNT(*) FROM steps WHERE run_id = ?", (run_id,)).fetchone()[0]) + 1
        step_id = f"{run_id}-s{seq}"
        self.conn.execute(
            "INSERT INTO steps(id, run_id, seq, role, tool, model, started_at) VALUES (?,?,?,?,?,?,?)",
            (step_id, run_id, seq, role, tool, model, now_iso()),
        )
        return step_id

    def end_step(
        self,
        step_id: str,
        outcome: str,
        error_kind: str | None,
        message: str,
        checkpoint: str | None,
        tool_calls: int | None = None,
    ) -> None:
        self.conn.execute(
            "UPDATE steps SET outcome = ?, error_kind = ?, message = ?, checkpoint_sha = ?, ended_at = ?,"
            " tool_calls = ? WHERE id = ?",
            (outcome, error_kind, message[:2000], checkpoint, now_iso(), tool_calls, step_id),
        )

    def steps(self, run_id: str) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM steps WHERE run_id = ? ORDER BY seq", (run_id,)).fetchall()

    def add_usage(self, step_id: str, records: list[UsageRecord]) -> None:
        self.conn.executemany(
            "INSERT INTO usage(step_id, unit, quantity, api_equiv_usd, estimated) VALUES (?,?,?,?,?)",
            [(step_id, r.unit, r.quantity, r.api_equiv_usd, int(r.estimated)) for r in records],
        )

    def usage_for_run(self, run_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT s.tool, s.model, u.unit, SUM(u.quantity) AS quantity, SUM(u.api_equiv_usd) AS usd,"
            " MAX(u.estimated) AS estimated FROM usage u JOIN steps s ON s.id = u.step_id"
            " WHERE s.run_id = ? GROUP BY s.tool, s.model, u.unit",
            (run_id,),
        ).fetchall()

    # ---- handoffs e progresso ----
    def add_handoff(self, run_id: str, from_route: str, to_route: str | None, reason: str, packet: str | None) -> int:
        seq = int(self.conn.execute("SELECT COUNT(*) FROM handoffs WHERE run_id = ?", (run_id,)).fetchone()[0]) + 1
        self.conn.execute(
            "INSERT INTO handoffs(run_id, seq, from_route, to_route, reason, packet_path, at) VALUES (?,?,?,?,?,?,?)",
            (run_id, seq, from_route, to_route, reason, packet, now_iso()),
        )
        return seq

    def handoffs(self, run_id: str) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM handoffs WHERE run_id = ? ORDER BY seq", (run_id,)).fetchall()

    def add_progress(self, run_id: str, step_id: str | None, entry: ProgressEntry) -> None:
        self.conn.execute(
            "INSERT INTO progress(run_id, step_id, at, data) VALUES (?,?,?,?)",
            (run_id, step_id, now_iso(), json.dumps(entry.__dict__, ensure_ascii=False)),
        )

    def progress(self, run_id: str) -> list[ProgressEntry]:
        rows = self.conn.execute("SELECT data FROM progress WHERE run_id = ? ORDER BY rowid", (run_id,)).fetchall()
        return [ProgressEntry(**json.loads(r["data"])) for r in rows]

    # ---- estado das rotas (cota) ----
    def set_route_state(self, tool: str, limited_until: float | None, utilization: float | None, reason: str) -> None:
        self.conn.execute(
            "INSERT INTO route_state(tool, limited_until, utilization, reason, updated_at) VALUES (?,?,?,?,?)"
            " ON CONFLICT(tool) DO UPDATE SET limited_until = excluded.limited_until,"
            " utilization = excluded.utilization, reason = excluded.reason, updated_at = excluded.updated_at",
            (tool, limited_until, utilization, reason, now_iso()),
        )

    def route_states(self) -> dict[str, sqlite3.Row]:
        return {r["tool"]: r for r in self.conn.execute("SELECT * FROM route_state").fetchall()}


def _run(row: sqlite3.Row) -> RunRow:
    data = dict(row)
    data["classification"] = json.loads(data["classification"]) if data.get("classification") else None
    return RunRow(**data)
