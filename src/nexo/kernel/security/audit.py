"""Cadeia de eventos de auditoria com hash encadeado."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

GENESIS = "0" * 64


def canonical(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def event_hash(prev_hash: str, body: dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + canonical(body)).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ChainedEvent:
    seq: int
    at: str
    actor: str
    type: str
    payload: dict[str, Any]
    prev_hash: str
    hash: str

    def body(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "at": self.at,
            "actor": self.actor,
            "type": self.type,
            "payload": self.payload,
        }


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    checked: int
    broken_at: int | None = None
    reason: str = ""


def verify_chain(events: Iterable[ChainedEvent]) -> VerifyResult:
    prev = GENESIS
    expected_seq: int | None = None
    count = 0
    for ev in events:
        count += 1
        if expected_seq is not None and ev.seq != expected_seq:
            return VerifyResult(False, count, ev.seq, f"sequência quebrada: esperado {expected_seq}")
        if ev.prev_hash != prev:
            return VerifyResult(False, count, ev.seq, "prev_hash não corresponde ao evento anterior")
        if event_hash(prev, ev.body()) != ev.hash:
            return VerifyResult(False, count, ev.seq, "conteúdo alterado (hash não confere)")
        prev = ev.hash
        expected_seq = ev.seq + 1
    return VerifyResult(True, count)
