"""Rotas de entrada de dados: eventos de telemetria, âncoras da trilha, linha de base, deploys e incidentes."""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from nexo.central import alerts, autonomy, dora, facts, flags, siem
from nexo.central import insights as insight_engine
from nexo.central.app.admin import _monitor_autokill
from nexo.central.app.core import INSIGHTS_EVERY, _require, app
from nexo.central.auth import identity
from nexo.central.common import audit, db, pseudonym
from nexo.kernel.monitoring.telemetry import CONTRACT, validate, validate_context


class Record(BaseModel):
    event_id: str
    kind: str
    seq: int = 0
    type: str
    at: str
    payload: dict[str, Any] = Field(default_factory=dict)


class Batch(BaseModel):
    contract: str
    device_id: str
    project: str  # hash do caminho do projeto (nunca o caminho)
    context: dict[str, Any] = Field(default_factory=dict)  # squad, stack, versão, atraso da outbox
    events: list[Record]


class AnchorIn(BaseModel):
    device_id: str
    seq: int
    hash: str
    at: str


_last_insights = [0.0]


@app.post("/v2/events")
def post_events(batch: Batch, ident: dict[str, str] = Depends(identity)) -> dict[str, Any]:  # noqa: B008
    if batch.contract != CONTRACT:
        raise HTTPException(400, f"contrato não suportado: {batch.contract} (esperado {CONTRACT})")
    if ident["device_id"] and ident["device_id"] != batch.device_id:
        raise HTTPException(403, "token de outro dispositivo")
    problem = validate_context(batch.context)
    if problem:
        raise HTTPException(400, problem)
    squad = str(batch.context.get("squad") or "")
    stack = str(batch.context.get("stack") or "")
    pid = pseudonym(ident["dev"])
    accepted: list[str] = []
    duplicate: list[str] = []
    rejected: list[dict[str, str]] = []
    d = db()
    with d.tx():
        for ev in batch.events:
            reason = validate(ev.kind, ev.type, ev.payload)
            if reason:
                rejected.append({"event_id": ev.event_id, "reason": reason})
                continue
            payload = dict(ev.payload)
            if isinstance(payload.get("dev"), str):
                payload["dev"] = pseudonym(payload["dev"])
            raw = {
                "event_id": ev.event_id,
                "device_id": batch.device_id,
                "dev_pid": pid,
                "project": batch.project,
                "kind": ev.kind,
                "seq": ev.seq,
                "type": ev.type,
                "at": ev.at,
                "payload": json.dumps(payload, ensure_ascii=False),
                "received_at": time.time(),
                "squad": squad,
                "stack": stack,
            }
            inserted = d.execute(
                "INSERT INTO raw_events (event_id, device_id, dev_pid, project, kind, seq, type, at, payload,"
                " received_at, squad, stack) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                tuple(raw.values()),
            )
            if not inserted:
                duplicate.append(ev.event_id)
                continue
            accepted.append(ev.event_id)
            facts.apply(d, raw)
            if ev.kind == "step" and flags.enabled(d, "realtime_alerts", squad):  # O-04
                alerts.check_step(d, {**payload, "event_id": ev.event_id})
            elif ev.kind == "event":  # S-06: eventos de segurança para o SIEM
                siem.from_telemetry(d, ev.type, payload, raw)
                if ev.type == "bug.created":  # escape rebaixa a autonomia da área
                    autonomy.on_bug(d, raw, payload)
            if ev.type == "monitor.synthetic" and payload.get("status") == "fail" and payload.get("kind") == "format":
                _monitor_autokill(d, str(payload.get("route") or ""), ident["dev"])
        if batch.device_id and batch.context.get("nexo"):  # O-06: frota
            d.execute(
                "INSERT INTO fleet (device_id, dev_pid, squad, nexo, brain, agents, os, last_seen)"
                " VALUES (?,?,?,?,?,?,?,?) ON CONFLICT (device_id) DO UPDATE SET dev_pid = excluded.dev_pid,"
                " squad = excluded.squad, nexo = excluded.nexo, brain = COALESCE(NULLIF(excluded.brain, ''),"
                " fleet.brain), agents = excluded.agents, os = excluded.os, last_seen = excluded.last_seen",
                (
                    batch.device_id,
                    pid,
                    squad,
                    str(batch.context.get("nexo") or ""),
                    str(batch.context.get("brain") or ""),
                    json.dumps(batch.context.get("agents") or {}),
                    str(batch.context.get("os") or ""),
                    time.time(),
                ),
            )
        name = str(batch.context.get("project_name") or "").strip()
        if name:  # nome do projeto para o dashboard (o caminho nunca sai da máquina)
            d.execute(
                "INSERT INTO dim_project (project, name, squad, updated_at) VALUES (?,?,?,?) ON CONFLICT (project)"
                " DO UPDATE SET name = excluded.name, squad = excluded.squad, updated_at = excluded.updated_at",
                (batch.project, name[:80], squad, time.time()),
            )
        lag = batch.context.get("lag_hours")
        if isinstance(lag, (int, float)) and not isinstance(lag, bool):
            d.execute(
                "INSERT INTO device_lag (device_id, dev_pid, lag_hours, max_lag_hours, last_seen) VALUES (?,?,?,?,?)"
                " ON CONFLICT (device_id) DO UPDATE SET lag_hours = excluded.lag_hours,"
                " last_seen = excluded.last_seen, max_lag_hours = CASE WHEN excluded.lag_hours >"
                " device_lag.max_lag_hours THEN excluded.lag_hours ELSE device_lag.max_lag_hours END",
                (batch.device_id, pid, float(lag), float(lag), time.time()),
            )
    if siem.configured():
        siem.flush(d)
    if accepted and time.time() - _last_insights[0] > INSIGHTS_EVERY and flags.enabled(d, "insights"):
        _last_insights[0] = time.time()
        insight_engine.run(d)
    return {"accepted": accepted, "duplicate": duplicate, "rejected": rejected}


@app.post("/v2/anchors")
def post_anchor(anchor: AnchorIn, ident: dict[str, str] = Depends(identity)) -> dict[str, str]:  # noqa: B008
    """Âncora do log local: uma reescrita local posterior diverge do hash ancorado."""
    existing = db().one("SELECT hash FROM anchors WHERE device_id = ? AND seq = ?", (anchor.device_id, anchor.seq))
    if existing and existing["hash"] != anchor.hash:
        audit(ident["dev"], "anchor.divergent", {"device_id": anchor.device_id, "seq": anchor.seq})
        raise HTTPException(409, "âncora diverge: log local reescrito")
    db().execute(
        "INSERT INTO anchors (device_id, seq, hash, at) VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
        (anchor.device_id, anchor.seq, anchor.hash, anchor.at),
    )
    return {"status": "ancorado"}


class BaselineIn(BaseModel):
    project: str
    squad: str = ""
    data: dict[str, Any]


BASELINE_KEYS = {
    "period",
    "stories",
    "bugs",
    "points",
    "throughput_per_week",
    "bug_ratio",
    "lead_time_hours",
    "cycle_time_hours",
    "excluded_nexo",
}


@app.post("/v2/baselines")
def post_baseline(body: BaselineIn, ident: dict[str, str] = Depends(identity)) -> dict[str, str]:  # noqa: B008
    """Linha de base antes do Nexo, por squad × projeto: só números agregados do tracker."""
    from nexo.kernel.monitoring.telemetry import _clean

    extra = sorted(set(body.data) - BASELINE_KEYS)
    if extra or not _clean(body.data) or not _clean({"squad": body.squad, "project": body.project}):
        raise HTTPException(400, f"linha de base fora do contrato: {', '.join(extra) or 'valores'}")
    period = dict(body.data.get("period") or {})
    d = db()
    d.execute("DELETE FROM baselines WHERE squad = ? AND project = ?", (body.squad, body.project))
    d.execute(
        "INSERT INTO baselines (squad, project, period, data, at) VALUES (?,?,?,?,?)",
        (
            body.squad,
            body.project,
            f"{period.get('since', '')}..{period.get('until', '')}",
            json.dumps(body.data, ensure_ascii=False),
            time.time(),
        ),
    )
    audit(ident["dev"], "baseline.set", {"squad": body.squad, "project": body.project})
    return {"status": "ok"}


class CommitIn(BaseModel):
    sha: str
    authored_at: str | None = None
    nexo: bool = False  # trailer Nexo-Run no commit


class DeploymentIn(BaseModel):
    id: str
    service: str
    env: str = "production"
    sha: str = ""
    status: str = "success"
    started_at: str | None = None
    finished_at: str
    squad: str = ""
    commits: list[CommitIn] = Field(default_factory=list)


class IncidentIn(BaseModel):
    id: str
    service: str
    opened_at: str
    resolved_at: str | None = None
    severity: str = ""
    cause_sha: str | None = None
    squad: str = ""


@app.post("/v2/deployments")
def post_deployment(body: DeploymentIn, ident: dict[str, str] = Depends(identity)) -> dict[str, Any]:  # noqa: B008
    """Implantação vinda do pipeline: serviço, ambiente, status e commits (com marca do Nexo)."""
    _require(ident, "integration", "admin", "nexo")
    return dora.record_deployment(db(), body.model_dump())


@app.post("/v2/incidents")
def post_incident(body: IncidentIn, ident: dict[str, str] = Depends(identity)) -> dict[str, Any]:  # noqa: B008
    """Incidente vindo do ServiceNow; ligado à implantação e à mudança do Nexo pelo commit causador."""
    _require(ident, "integration", "admin", "nexo")
    return dora.record_incident(db(), body.model_dump())
