"""Rotas de administração.

expurgo, reidentificação, diretório, licenças, totais do tracker, jobs, flags e kill switch.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field

from nexo.central import auth, distribution, facts, flags, tracing
from nexo.central.app.core import _require, app
from nexo.central.auth import identity
from nexo.central.common import audit, db, pseudonym
from nexo.central.visibility import ROLES


@app.on_event("startup")
def _scheduler() -> None:
    if os.environ.get("NX_COLLECTOR_SCHEDULER") == "1":
        distribution.start_scheduler(db)
    if tracing.enabled():
        tracing.start_exporter()


class PurgeIn(BaseModel):
    older_than_days: int = 395  # 13 meses


@app.post("/v2/admin/purge")
def purge(body: PurgeIn, ident: dict[str, str] = Depends(identity)) -> dict[str, int]:  # noqa: B008
    _require(ident, "admin")
    deleted = db().execute(
        "DELETE FROM raw_events WHERE received_at < ?", (time.time() - body.older_than_days * 86400,)
    )
    audit(ident["dev"], "retention.purge", {"older_than_days": body.older_than_days, "deleted": deleted})
    return {"deleted": deleted}


@app.get("/v2/admin/reidentify/{dev_pid}")
def reidentify(dev_pid: str, reason: str, ident: dict[str, str] = Depends(identity)) -> dict[str, str]:  # noqa: B008
    _require(ident, "admin")
    if len(reason.strip()) < 10:
        raise HTTPException(400, "reidentificação exige motivo (auditado)")
    row = db().one("SELECT dev FROM identities WHERE dev_pid = ?", (dev_pid,))
    if row is None:
        raise HTTPException(404, "pseudônimo desconhecido")
    audit(ident["dev"], "identity.reidentified", {"dev_pid": dev_pid, "reason": reason})
    return {"dev_pid": dev_pid, "dev": str(row["dev"])}


class SquadIn(BaseModel):
    id: str
    name: str = ""
    cost_center: str = Field(default="", max_length=64)  # vazio: herda o da diretoria
    leads: list[str] = Field(default_factory=list)
    managers: list[str] = Field(default_factory=list)
    members: list[str] = Field(default_factory=list)


class DirectorateIn(BaseModel):
    id: str
    name: str = ""
    cost_center: str = Field(default="", max_length=64)
    squads: list[SquadIn] = Field(default_factory=list)


class DirectoryIn(BaseModel):
    directorates: list[DirectorateIn] = Field(default_factory=list)
    roles: dict[str, str] = Field(default_factory=dict)  # pessoa → papel (lead, manager, director, finops…)


@app.put("/v2/admin/directory")
def put_directory(body: DirectoryIn, ident: dict[str, str] = Depends(identity)) -> dict[str, int]:  # noqa: B008
    """Diretório da org (sincronizado do RH/Entra): diretorias, squads, pessoas e papéis. Substitui o anterior."""
    _require(ident, "admin")
    bad = sorted({r for r in body.roles.values() if r not in ROLES})
    if bad:
        raise HTTPException(400, f"papel desconhecido: {', '.join(bad)}")
    d = db()
    with d.tx():
        for table_name in ("dim_directorate", "dim_squad", "org_people", "org_members"):
            d.execute(f"DELETE FROM {table_name}")
        for dr in body.directorates:
            d.execute(
                "INSERT INTO dim_directorate (directorate, name, cost_center) VALUES (?,?,?)",
                (dr.id, dr.name or dr.id, dr.cost_center.strip()),
            )
            for sq in dr.squads:
                d.execute(
                    "INSERT INTO dim_squad (squad, name, directorate, cost_center) VALUES (?,?,?,?)",
                    (sq.id, sq.name or sq.id, dr.id, sq.cost_center.strip()),
                )
                for relation, people in (("lead", sq.leads), ("manager", sq.managers), ("member", sq.members)):
                    for dev in people:
                        d.execute(
                            "INSERT INTO org_members (dev, squad, relation) VALUES (?,?,?) ON CONFLICT DO NOTHING",
                            (dev, sq.id, relation),
                        )
        for dev, role in body.roles.items():
            d.execute("INSERT INTO org_people (dev, role) VALUES (?, ?)", (dev, role))
    squads = sum(len(dr.squads) for dr in body.directorates)
    audit(
        ident["dev"],
        "directory.set",
        {"directorates": len(body.directorates), "squads": squads, "people": len(body.roles)},
    )
    return {"directorates": len(body.directorates), "squads": squads, "people": len(body.roles)}


class LicenseIn(BaseModel):
    dev: str
    tool: str
    monthly_brl: float
    acu_included: float | None = None


@app.put("/v2/admin/licenses")
def put_licenses(body: list[LicenseIn], ident: dict[str, str] = Depends(identity)) -> dict[str, int]:  # noqa: B008
    _require(ident, "finops", "admin")
    d = db()
    with d.tx():
        d.execute("DELETE FROM licenses")
        for lic in body:
            d.execute(
                "INSERT INTO licenses (dev, tool, monthly_brl, acu_included) VALUES (?,?,?,?)",
                (lic.dev, lic.tool, lic.monthly_brl, lic.acu_included),
            )
    audit(ident["dev"], "licenses.set", {"count": len(body)})
    return {"licenses": len(body)}


class TrackerTotalIn(BaseModel):
    squad: str
    month: str
    stories: int


@app.put("/v2/admin/tracker-totals")
def put_tracker_totals(
    body: list[TrackerTotalIn],
    ident: dict[str, str] = Depends(identity),  # noqa: B008
) -> dict[str, int]:
    """Total de histórias do tracker por squad × mês (integração do tracker) — base da adoção."""
    _require(ident, "admin", "nexo")
    d = db()
    for t in body:
        d.execute("DELETE FROM tracker_totals WHERE squad = ? AND month = ?", (t.squad, t.month))
        d.execute("INSERT INTO tracker_totals (squad, month, stories) VALUES (?,?,?)", (t.squad, t.month, t.stories))
    return {"rows": len(body)}


@app.post("/v2/admin/reprocess")
def reprocess(ident: dict[str, str] = Depends(identity)) -> dict[str, Any]:  # noqa: B008
    """Reconstrói o modelo de fatos a partir dos eventos brutos."""
    _require(ident, "admin")
    total = facts.reprocess(db())
    audit(ident["dev"], "facts.reprocessed", {"events": total})
    return {"events": total, "fingerprint": facts.fingerprint(db())}


@app.post("/v2/admin/jobs/{job}")
def run_job(job: str, period: str = "", ident: dict[str, str] = Depends(identity)) -> dict[str, Any]:  # noqa: B008
    """Execução manual (ou por CronJob) de insights, resumo semanal, relatório mensal e issues sistêmicas."""
    _require(ident, "admin", "nexo")
    if job not in distribution.JOBS:
        raise HTTPException(404, f"job desconhecido: {job}")
    audit(ident["dev"], "job.run", {"job": job, "period": period})
    return distribution.run_job(db(), job, period or None)


def _monitor_autokill(d: Any, route: str, actor: str) -> None:
    """O-03: formato de CLI divergente no monitor sintético → rota no kill switch central (se habilitado)."""
    if os.environ.get("NX_COLLECTOR_MONITOR_AUTOKILL") != "1" or not route:
        return
    row = d.one("SELECT data FROM kill_switch WHERE id = 'org'")
    data = dict(json.loads(row["data"])) if row else {}
    models = data.get("models") or {}
    models = {m: "kill switch" for m in models} if isinstance(models, list) else dict(models)
    if route in models:
        return
    models[route] = "monitor sintético: formato da CLI divergente"
    data["models"] = models
    d.execute("DELETE FROM kill_switch WHERE id = 'org'")
    d.execute(
        "INSERT INTO kill_switch (id, data, updated_at, updated_by) VALUES ('org', ?, ?, ?)",
        (json.dumps(data, ensure_ascii=False), time.time(), "monitor"),
    )
    audit("monitor", "killswitch.auto", {"route": route, "reported_by": pseudonym(actor)})


class FlagIn(BaseModel):
    value: bool | None
    squad: str | None = None
    reason: str


@app.get("/v2/flags")
def get_flags(ident: dict[str, str] = Depends(identity)) -> dict[str, Any]:  # noqa: B008
    """Feature flags do central: a CLI consulta a cada 2 min; global e por squad."""
    return flags.snapshot(db())


@app.put("/v2/admin/flags/{key}")
def put_flag(key: str, body: FlagIn, request: Request) -> dict[str, Any]:
    """Liga/desliga uma funcionalidade para a empresa ou um squad (admin e time do Nexo), com motivo auditado."""
    principal = auth.principal(request)
    if principal.role not in ("admin", "nexo"):
        raise HTTPException(403, "feature flags são alteradas pelo time do Nexo ou admin")
    if len(body.reason.strip()) < 10:
        raise HTTPException(400, "mudança de flag exige motivo")
    try:
        change = flags.set_flag(db(), key, body.value, body.squad or "", principal.dev, body.reason)
    except KeyError as exc:
        raise HTTPException(404, f"flag desconhecida: {key}") from exc
    audit(principal.dev, "flag.set", change | {"reason": body.reason})
    return change


class KillSwitchIn(BaseModel):
    tools: dict[str, str] | list[str] = Field(default_factory=dict)
    models: dict[str, str] | list[str] = Field(default_factory=dict)
    nexo_versions: dict[str, str] | list[str] = Field(default_factory=dict)
    brain_versions: dict[str, str] | list[str] = Field(default_factory=dict)
    reason: str = ""


@app.get("/v2/kill-switch")
def get_kill_switch(ident: dict[str, str] = Depends(identity)) -> dict[str, Any]:  # noqa: B008
    """Kill switch central: toda CLI consulta a cada 5 min; offline vale a última resposta."""
    row = db().one("SELECT data FROM kill_switch WHERE id = 'org'")
    return dict(json.loads(row["data"])) if row else {}


@app.put("/v2/admin/kill-switch")
def put_kill_switch(body: KillSwitchIn, ident: dict[str, str] = Depends(identity)) -> dict[str, Any]:  # noqa: B008
    _require(ident, "admin", "security")
    from nexo.kernel.security.killswitch import parse

    data = body.model_dump()
    try:
        parse(data, "central")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    d = db()
    d.execute("DELETE FROM kill_switch WHERE id = 'org'")
    d.execute(
        "INSERT INTO kill_switch (id, data, updated_at, updated_by) VALUES ('org', ?, ?, ?)",
        (json.dumps(data, ensure_ascii=False), time.time(), ident["dev"]),
    )
    audit(ident["dev"], "killswitch.set", data)
    return data
