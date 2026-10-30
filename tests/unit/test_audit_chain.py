from __future__ import annotations

import dataclasses

from nexo.kernel.security.audit import GENESIS, ChainedEvent, event_hash, verify_chain


def chain(n: int) -> list[ChainedEvent]:
    out: list[ChainedEvent] = []
    prev = GENESIS
    for i in range(1, n + 1):
        body = {"seq": i, "at": f"t{i}", "actor": "dev", "type": "x", "payload": {"i": i}}
        h = event_hash(prev, body)
        out.append(ChainedEvent(i, f"t{i}", "dev", "x", {"i": i}, prev, h))
        prev = h
    return out


def test_valid_chain() -> None:
    res = verify_chain(chain(5))
    assert res.ok and res.checked == 5


def test_empty_chain_is_valid() -> None:
    assert verify_chain([]).ok


def test_detects_payload_tampering() -> None:
    events = chain(4)
    events[2] = dataclasses.replace(events[2], payload={"i": 999})
    res = verify_chain(events)
    assert not res.ok and res.broken_at == 3


def test_detects_removed_event() -> None:
    events = chain(4)
    del events[1]
    res = verify_chain(events)
    assert not res.ok and res.broken_at == 3


def test_detects_relinked_hash() -> None:
    events = chain(3)
    events[1] = dataclasses.replace(events[1], prev_hash="f" * 64)
    assert verify_chain(events).broken_at == 2
