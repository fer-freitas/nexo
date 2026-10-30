"""Rotas de governança: autonomia do squad, dúvidas dos agentes e aprovações remotas (API e formulários)."""

from __future__ import annotations

import os
import time
import urllib.parse
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel

from nexo.central import auth, autonomy, dashboard
from nexo.central.app.core import _form, _html, _require, app
from nexo.central.auth import identity
from nexo.central.common import audit, db, pseudonym


class AutonomyIn(BaseModel):
    level: str
    reason: str


@app.get("/v2/autonomy")
def get_autonomy(squad: str, area: str, ident: dict[str, str] = Depends(identity)) -> dict[str, Any]:  # noqa: B008
    """Nível de autonomia vigente — a CLI consulta antes de cada história."""
    return {"squad": squad, "area": area, "level": autonomy.level(db(), squad, area)}


@app.get("/v2/autonomy/eligibility")
def autonomy_eligibility(squad: str, request: Request) -> dict[str, Any]:
    principal = auth.principal(request)
    if not principal.sees_squad(squad):
        raise HTTPException(403, "sem acesso ao squad")
    return {"squad": squad, "areas": autonomy.summary(db(), squad), "history": autonomy.history(db(), squad)[:20]}


@app.post("/v2/autonomy/{squad}/{area}")
def set_autonomy(squad: str, area: str, body: AutonomyIn, request: Request) -> dict[str, Any]:
    """Subir um nível exige elegibilidade e o gestor do squad (C5, configurável); descer, só motivo."""
    principal = auth.principal(request)
    d = db()
    current = autonomy.level(d, squad, area)
    from nexo.modules.execution.domain.autonomy import index

    try:
        target = index(body.level)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if len(body.reason.strip()) < 10:
        raise HTTPException(400, "mudança de autonomia exige motivo")
    approvers = {r.strip() for r in os.environ.get("NX_COLLECTOR_AUTONOMY_APPROVERS", "manager,admin").split(",")}
    if target > index(current):
        if target != index(current) + 1:
            raise HTTPException(409, "sobe um nível por vez")
        allowed = principal.role == "admin" or (principal.role in approvers and squad in principal.manages)
        if not allowed:
            raise HTTPException(403, "subir a autonomia exige o gestor do squad")
        check = autonomy.check(d, squad, area)
        if not check.eligible:
            raise HTTPException(409, "squad ainda não elegível: " + "; ".join(check.reasons))
    elif principal.role not in ("lead", "manager", "security", "admin", "nexo") or (
        principal.role in ("lead", "manager") and not principal.sees_squad(squad)
    ):
        raise HTTPException(403, "sem permissão para reduzir a autonomia deste squad")
    change = autonomy.set_level(d, squad, area, body.level, principal.dev, body.reason, "manual")
    audit(principal.dev, "autonomy.set", change | {"reason": body.reason})
    return change


class ClarificationIn(BaseModel):
    id: str


class AnswerIn(BaseModel):
    answer: str
    by: str = ""


@app.post("/v2/clarifications")
def register_clarification(body: ClarificationIn, ident: dict[str, str] = Depends(identity)) -> dict[str, str]:  # noqa: B008
    """A-05: a CLI registra só o id da pergunta (o texto vai direto ao Teams, nunca ao central)."""
    db().execute(
        "INSERT INTO clarifications (id, dev_pid, device_id, created_at) VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
        (body.id, pseudonym(ident["dev"]), ident["device_id"], time.time()),
    )
    return {"status": "ok"}


@app.post("/v2/clarifications/{cid}/answer")
def answer_clarification(cid: str, body: AnswerIn, ident: dict[str, str] = Depends(identity)) -> dict[str, str]:  # noqa: B008
    """Resposta vinda do cartão do Teams (fluxo do Power Automate com o papel `ci`) ou da pessoa pelo dashboard."""
    _require(ident, "ci", "admin", "dev", "lead", "manager")
    row = db().one("SELECT id, answer FROM clarifications WHERE id = ?", (cid,))
    if row is None:
        raise HTTPException(404, "pergunta desconhecida")
    if row["answer"]:
        raise HTTPException(409, "pergunta já respondida")
    if not body.answer.strip():
        raise HTTPException(400, "resposta vazia")
    db().execute(
        "UPDATE clarifications SET answer = ?, answered_by = ?, answered_at = ? WHERE id = ?",
        (body.answer[:4000], body.by or ident["dev"], time.time(), cid),
    )
    audit(ident["dev"], "clarification.answered", {"id": cid})
    return {"status": "respondida"}


@app.get("/v2/clarifications/{cid}/form", response_model=None)
def clarification_form(cid: str, request: Request) -> Response:
    session = auth.session_of(request)
    if session is None:
        return RedirectResponse(f"/login?next=/v2/clarifications/{urllib.parse.quote(cid)}/form", status_code=303)
    principal = auth.principal_for(str(session["dev"]), session["role"] or None, "session")
    if db().one("SELECT id FROM clarifications WHERE id = ?", (cid,)) is None:
        raise HTTPException(404, "pergunta desconhecida ou já respondida")
    return _html(dashboard.render_clarification(principal, cid, str(session["csrf"])))


@app.post("/v2/clarifications/{cid}/form", response_model=None)
async def clarification_submit(cid: str, request: Request) -> Response:
    session = auth.session_of(request)
    if session is None:
        raise HTTPException(401, "sessão ausente")
    form = await _form(request)
    auth.check_csrf(request, form.get("csrf", ""))
    ident = {"role": str(session["role"] or "dev"), "dev": str(session["dev"]), "device_id": ""}
    answer_clarification(cid, AnswerIn(answer=form.get("answer", ""), by=str(session["dev"])), ident)
    principal = auth.principal_for(str(session["dev"]), session["role"] or None, "session")
    return _html(dashboard.render_clarification(principal, cid, str(session["csrf"]), "resposta enviada ao Nexo"))


@app.get("/v2/clarifications/answers")
def my_answers(ident: dict[str, str] = Depends(identity)) -> dict[str, Any]:  # noqa: B008
    """Respostas das perguntas deste dev ainda não entregues; entregues são apagadas do central."""
    pid = pseudonym(ident["dev"])
    rows = db().query(
        "SELECT id, answer, answered_by FROM clarifications WHERE dev_pid = ? AND answer IS NOT NULL AND delivered = 0",
        (pid,),
    )
    for r in rows:
        db().execute("DELETE FROM clarifications WHERE id = ?", (r["id"],))
    return {"answers": [{"id": r["id"], "answer": r["answer"], "by": r["answered_by"]} for r in rows]}


class ApprovalIn(BaseModel):
    id: str
    gate: str = "code"


class DecisionIn(BaseModel):
    decision: str
    reason: str = ""
    by: str = ""


@app.post("/v2/approvals")
def register_approval(body: ApprovalIn, ident: dict[str, str] = Depends(identity)) -> dict[str, str]:  # noqa: B008
    """a CLI registra só o id do portão e quem disparou (pseudônimo); o motivo vai direto ao Teams."""
    db().execute(
        "INSERT INTO remote_approvals (id, dev_pid, device_id, gate, created_at) VALUES (?,?,?,?,?)"
        " ON CONFLICT DO NOTHING",
        (body.id, pseudonym(ident["dev"]), ident["device_id"], body.gate[:20], time.time()),
    )
    return {"status": "ok"}


@app.post("/v2/approvals/{aid}/decide")
def decide_approval(aid: str, body: DecisionIn, ident: dict[str, str] = Depends(identity)) -> dict[str, str]:  # noqa: B008
    """Decisão do cartão do Teams (fluxo com papel `ci`, que informa `by`) ou da pessoa pelo formulário (SSO)."""
    _require(ident, "ci", "admin", "dev", "lead", "manager")
    if body.decision not in ("approve", "reject"):
        raise HTTPException(400, "decisão deve ser approve ou reject")
    if body.decision == "reject" and len(body.reason.strip()) < 5:
        raise HTTPException(400, "reprovação exige motivo")
    decider = (body.by or ident["dev"]) if ident["role"] == "ci" else ident["dev"]
    row = db().one("SELECT dev_pid, decision FROM remote_approvals WHERE id = ?", (aid,))
    if row is None:
        raise HTTPException(404, "aprovação desconhecida")
    if row["decision"]:
        raise HTTPException(409, "aprovação já decidida")
    if pseudonym(decider) == row["dev_pid"]:  # SEC-52: quem disparou não aprova o próprio portão
        audit(decider, "policy.violation", {"rule": "SEC-52", "approval": aid})
        raise HTTPException(403, "segregação de funções: quem disparou a história não decide o próprio portão")
    db().execute(
        "UPDATE remote_approvals SET decision = ?, reason = ?, decided_by = ?, decided_pid = ?, decided_at = ?"
        " WHERE id = ?",
        (body.decision, body.reason[:1000], decider, pseudonym(decider), time.time(), aid),
    )
    audit(decider, "approval.decided", {"id": aid, "decision": body.decision})
    return {"status": "decidida"}


@app.get("/v2/approvals/decisions")
def my_decisions(ident: dict[str, str] = Depends(identity)) -> dict[str, Any]:  # noqa: B008
    """Decisões dos portões deste dev ainda não entregues; entregues são apagadas do central."""
    rows = db().query(
        "SELECT id, decision, reason, decided_by FROM remote_approvals WHERE dev_pid = ? AND decision IS NOT NULL",
        (pseudonym(ident["dev"]),),
    )
    for r in rows:
        db().execute("DELETE FROM remote_approvals WHERE id = ?", (r["id"],))
    return {
        "decisions": [
            {"id": r["id"], "decision": r["decision"], "reason": r["reason"], "by": r["decided_by"]} for r in rows
        ]
    }


@app.get("/v2/approvals/{aid}/form", response_model=None)
def approval_form(aid: str, request: Request) -> Response:
    session = auth.session_of(request)
    if session is None:
        return RedirectResponse(f"/login?next=/v2/approvals/{urllib.parse.quote(aid)}/form", status_code=303)
    principal = auth.principal_for(str(session["dev"]), session["role"] or None, "session")
    row = db().one("SELECT gate, decision FROM remote_approvals WHERE id = ?", (aid,))
    if row is None:
        raise HTTPException(404, "aprovação desconhecida ou já entregue")
    return _html(dashboard.render_approval(principal, aid, str(row["gate"]), str(session["csrf"]), row["decision"]))


@app.post("/v2/approvals/{aid}/form", response_model=None)
async def approval_submit(aid: str, request: Request) -> Response:
    session = auth.session_of(request)
    if session is None:
        raise HTTPException(401, "sessão ausente")
    form = await _form(request)
    auth.check_csrf(request, form.get("csrf", ""))
    ident = {"role": str(session["role"] or "dev"), "dev": str(session["dev"]), "device_id": ""}
    decide_approval(aid, DecisionIn(decision=form.get("decision", ""), reason=form.get("reason", "")), ident)
    principal = auth.principal_for(str(session["dev"]), session["role"] or None, "session")
    row = db().one("SELECT gate, decision FROM remote_approvals WHERE id = ?", (aid,))
    return _html(
        dashboard.render_approval(
            principal,
            aid,
            str(row["gate"]) if row else "",
            str(session["csrf"]),
            row["decision"] if row else "",
            "decisão enviada ao Nexo",
        )
    )
