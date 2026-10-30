"""Eventos de segurança para o SIEM da empresa.

Um único ponto de integração (o coletor): violações de política, kill switch, suspeita de injeção, falha de formato
do monitor, âncora divergente, reidentificação, revogação de dispositivo, export e mudanças administrativas.
Saída por HTTPS com fila e nova tentativa:

- `NX_COLLECTOR_SIEM_FORMAT=json` (padrão): `POST` de uma lista de eventos em `NX_COLLECTOR_SIEM_URL`
  (Logic App/Function, Cribl, Logstash…), com `Authorization: Bearer $NX_COLLECTOR_SIEM_TOKEN`.
- `splunk-hec`: HTTP Event Collector (`Authorization: Splunk <token>`, sourcetype `nexo:security`).
- `sentinel`: Logs Ingestion API do Azure Monitor (URL do DCR/stream; token Entra do coletor).

Violação de política vai com a pessoa identificada (a Segurança vê pessoas nesse caso, §9.2) e a reidentificação
fica na auditoria.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

from nexo.central.db import CollectorDB

SECURITY_EVENTS = {
    "policy.violation": "alta",
    "killswitch.blocked": "media",
    "killswitch.local": "media",
    "security.injection_suspect": "alta",
    "monitor.synthetic": "media",
}
SECURITY_AUDIT = {
    "anchor.divergent": "alta",
    "identity.reidentified": "alta",
    "identity.view": "baixa",
    "device.revoked": "media",
    "device.approved": "baixa",
    "killswitch.set": "alta",
    "killswitch.auto": "alta",
    "export": "baixa",
    "directory.set": "media",
    "retention.purge": "media",
    "session.created": "baixa",
    "autonomy.set": "media",
    "flag.set": "media",
    "autonomy.demoted": "media",
}


def configured() -> bool:
    return bool(os.environ.get("NX_COLLECTOR_SIEM_URL"))


def enqueue(d: CollectorDB, event: dict[str, Any]) -> None:
    if not configured():
        return
    d.execute(
        "INSERT INTO siem_outbox (id, at, event, status, attempts) VALUES (?,?,?, 'pending', 0)",
        (uuid.uuid4().hex, time.time(), json.dumps(event, ensure_ascii=False, default=str)),
    )


def from_telemetry(d: CollectorDB, type_: str, payload: dict[str, Any], raw: dict[str, Any]) -> None:
    severity = SECURITY_EVENTS.get(type_)
    if not configured() or severity is None or (type_ == "monitor.synthetic" and payload.get("status") != "fail"):
        return
    person = None
    if type_ == "policy.violation":  # §9.2: Segurança vê a pessoa em violações; a reidentificação é auditada
        row = d.one("SELECT dev FROM identities WHERE dev_pid = ?", (raw.get("dev_pid"),))
        person = str(row["dev"]) if row else None
    enqueue(
        d,
        {
            "time": raw.get("at"),
            "source": "nexo",
            "category": "telemetry",
            "action": type_,
            "severity": severity,
            "device": raw.get("device_id"),
            "dev_pid": raw.get("dev_pid"),
            "user": person,
            "squad": raw.get("squad"),
            "project": raw.get("project"),
            "details": payload,
        },
    )
    if person:
        d.execute(
            "INSERT INTO audit (id, at, actor, action, detail) VALUES (?, ?, ?, ?, ?)",
            (
                uuid.uuid4().hex,
                time.time(),
                "siem",
                "identity.view",
                json.dumps({"purpose": "violations", "count": 1, "channel": "siem"}),
            ),
        )


def from_audit(d: CollectorDB, actor: str, action: str, detail: dict[str, Any]) -> None:
    severity = SECURITY_AUDIT.get(action)
    if severity is None:
        return
    enqueue(
        d,
        {
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source": "nexo-collector",
            "category": "admin",
            "action": action,
            "severity": severity,
            "user": actor,
            "details": detail,
        },
    )


def _post(url: str, body: Any, headers: dict[str, str]) -> None:
    from nexo.central import distribution

    status, _ = distribution.transport(url, body, headers)
    if status >= 300:
        raise OSError(f"SIEM respondeu {status}")


def flush(d: CollectorDB, limit: int = 500) -> dict[str, int]:
    url = os.environ.get("NX_COLLECTOR_SIEM_URL", "")
    if not url:
        return {"sent": 0, "pending": 0}
    rows = d.query("SELECT * FROM siem_outbox WHERE status = 'pending' ORDER BY at LIMIT ?", (limit,))
    if not rows:
        return {"sent": 0, "pending": 0}
    events = [json.loads(r["event"]) for r in rows]
    fmt = os.environ.get("NX_COLLECTOR_SIEM_FORMAT", "json")
    token = os.environ.get("NX_COLLECTOR_SIEM_TOKEN", "")
    try:
        if fmt == "splunk-hec":  # HEC: um objeto por evento
            for e in events:
                _post(
                    url,
                    {"event": e, "sourcetype": "nexo:security", "source": e.get("source")},
                    {"Authorization": f"Splunk {token}"},
                )
        elif fmt == "sentinel":
            _post(
                url,
                [{"TimeGenerated": e.get("time"), **{k: v for k, v in e.items() if k != "time"}} for e in events],
                {"Authorization": f"Bearer {token}"},
            )
        else:
            _post(url, events, {"Authorization": f"Bearer {token}"} if token else {})
    except (OSError, ValueError) as exc:
        for r in rows:
            d.execute(
                "UPDATE siem_outbox SET attempts = attempts + 1, last_error = ? WHERE id = ?", (str(exc)[:200], r["id"])
            )
        return {"sent": 0, "pending": len(rows)}
    for r in rows:
        d.execute("UPDATE siem_outbox SET status = 'sent' WHERE id = ?", (r["id"],))
    left = d.one("SELECT COUNT(*) AS n FROM siem_outbox WHERE status = 'pending'")
    return {"sent": len(rows), "pending": int(left["n"] if left else 0)}
