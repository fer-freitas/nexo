"""Recursos compartilhados do coletor: banco, pseudônimos e trilha de auditoria."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from typing import Any

from nexo.central.db import CollectorDB

_dbs: dict[str, CollectorDB] = {}


def db() -> CollectorDB:
    url = os.environ.get("NX_COLLECTOR_DB", "nexo-collector.db")
    if url not in _dbs:
        _dbs[url] = CollectorDB(url)
    return _dbs[url]


def pid_of(dev: str) -> str:
    key = os.environ.get("NX_COLLECTOR_PSEUDO_KEY", "chave-de-desenvolvimento").encode()
    return hmac.new(key, dev.encode(), hashlib.sha256).hexdigest()[:16]


def pseudonym(dev: str) -> str:
    """SEC-73: dev pseudonimizado com HMAC (chave só no coletor); reidentificação só nos fluxos da matriz."""
    pid = pid_of(dev)
    d = db()
    if d.one("SELECT dev_pid FROM identities WHERE dev_pid = ?", (pid,)) is None:
        d.execute("INSERT INTO identities (dev_pid, dev) VALUES (?, ?) ON CONFLICT DO NOTHING", (pid, dev))
    return pid


def audit(actor: str, action: str, detail: dict[str, Any]) -> None:
    db().execute(
        "INSERT INTO audit (id, at, actor, action, detail) VALUES (?, ?, ?, ?, ?)",
        (uuid.uuid4().hex, time.time(), actor, action, json.dumps(detail, ensure_ascii=False)),
    )
    from nexo.central import siem

    siem.from_audit(db(), actor, action, detail)  # S-06: ações administrativas e de privacidade


def names(actor: str, purpose: str, pids: list[str]) -> dict[str, str]:
    """Reidentificação para um propósito da matriz (orçamento, bloqueios, portões, licença, violações) — auditada."""
    unique = sorted({p for p in pids if p})
    if not unique:
        return {}
    rows = db().query(
        f"SELECT dev_pid, dev FROM identities WHERE dev_pid IN ({', '.join('?' * len(unique))})",
        tuple(unique),
    )
    audit(actor, "identity.view", {"purpose": purpose, "count": len(rows)})
    return {str(r["dev_pid"]): str(r["dev"]) for r in rows}
