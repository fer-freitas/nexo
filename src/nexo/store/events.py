"""Persistência do log de auditoria (append-only, hash encadeado)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

from nexo.kernel.monitoring.telemetry import project_event
from nexo.kernel.security.audit import GENESIS, ChainedEvent, VerifyResult, event_hash, verify_chain
from nexo.kernel.security.redaction import redact_obj


class EventStore:
    def __init__(
        self,
        conn: sqlite3.Connection,
        outbox: bool = False,
        context: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._conn = conn
        self._outbox = outbox  # telemetria ligada → projeção do evento vai para a outbox na mesma transação
        self._context = context  # projeto/squad/stack do evento

    def append(self, actor: str, type_: str, payload: dict[str, Any], ref: str | None = None) -> ChainedEvent:
        safe_payload = redact_obj(payload)
        if ref is not None:
            safe_payload = {**safe_payload, "ref": ref}
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute("SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
            seq = (row["seq"] + 1) if row else 1
            prev = row["hash"] if row else GENESIS
            at = datetime.now(UTC).isoformat(timespec="milliseconds")
            body = {"seq": seq, "at": at, "actor": actor, "type": type_, "payload": safe_payload}
            digest = event_hash(prev, body)
            self._conn.execute(
                "INSERT INTO events(seq, at, actor, type, ref, payload, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?)",
                (seq, at, actor, type_, ref, json.dumps(safe_payload, ensure_ascii=False), prev, digest),
            )
            if self._outbox:
                projected = project_event(type_, safe_payload)
                if projected is not None:
                    context = self._context() if self._context else None
                    enqueue(self._conn, "event", type_, at, projected, seq, context)
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        return ChainedEvent(seq, at, actor, type_, safe_payload, prev, digest)

    def iter_all(self) -> Iterator[ChainedEvent]:
        for row in self._conn.execute("SELECT * FROM events ORDER BY seq"):
            yield _to_event(row)

    def recent(self, limit: int = 50, ref: str | None = None) -> list[ChainedEvent]:
        if ref is None:
            rows = self._conn.execute("SELECT * FROM events ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE ref = ? ORDER BY seq DESC LIMIT ?", (ref, limit)
            ).fetchall()
        return [_to_event(r) for r in reversed(rows)]

    def verify(self) -> VerifyResult:
        return verify_chain(self.iter_all())


def _to_event(row: sqlite3.Row) -> ChainedEvent:
    return ChainedEvent(
        seq=row["seq"],
        at=row["at"],
        actor=row["actor"],
        type=row["type"],
        payload=json.loads(row["payload"]),
        prev_hash=row["prev_hash"],
        hash=row["hash"],
    )


def enqueue(
    conn: sqlite3.Connection,
    kind: str,
    type_: str,
    at: str,
    payload: dict[str, Any],
    seq: int = 0,
    context: dict[str, Any] | None = None,
) -> str:
    """Registro na outbox da telemetria (chamado dentro da transação do chamador)."""
    import time
    import uuid

    event_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO outbox(event_id, kind, seq, type, at, payload, created_at, context) VALUES (?,?,?,?,?,?,?,?)",
        (
            event_id,
            kind,
            seq,
            type_,
            at,
            json.dumps(payload, ensure_ascii=False),
            time.time(),
            json.dumps(context or {}, sort_keys=True),
        ),
    )
    return event_id
