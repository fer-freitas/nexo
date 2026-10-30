"""Decisões humanas sobre a história: aprovar, reprovar, retorno do PR, segregação de funções e aprovação externa."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nexo.kernel.errors import NexoError, PolicyViolation
from nexo.kernel.platform import config, identity
from nexo.modules.delivery.app import approval
from nexo.modules.execution.app.story import bugs as story_bugs
from nexo.modules.execution.app.story import cost as story_cost
from nexo.modules.execution.app.story import publish as story_publish
from nexo.modules.execution.app.story.common import StoryReport
from nexo.store.runs import now_iso

if TYPE_CHECKING:
    from nexo.modules.execution.app.story.runner import StoryRunner


def approve(
    r: StoryRunner,
    story_id: str,
    gate: str = "code",
    raise_pct: float | None = None,
    economic: bool = False,
    approver: identity.Identity | None = None,
) -> StoryReport:
    run = require_waiting(r, story_id)
    st = r._load_state(run.id)
    refuse_if_external(r, st)
    if st.get("cost_gate"):
        return story_cost.approve(r, story_id, run.id, st, raise_pct, economic)
    if raise_pct is not None or economic:
        raise NexoError("--raise e --economic só valem para o portão de custo")
    approver = check_segregation(r, st, approver)
    st["approved_by"] = approver.id  # vai para o registro de entrega (.nexo/entregas/), gravado na transição
    r._save(run.id, st)
    r._transition(story_id, "approve")
    r.ctx.audit(
        "gate.decision",
        {
            "gate": gate,
            "story": story_id,
            "decision": "approved",
            "approver": approver.id,
            "approver_verified": approver.verified,
        },
        ref=story_id,
    )
    r.store.update_run(run.id, state="Concluido", ended_at=now_iso())
    story_publish.as_built(r, story_id)
    return r._report(story_id, run.id, f"aprovado por {approver.id}")


def reject(r: StoryRunner, story_id: str, reason: str, approver: identity.Identity | None = None) -> StoryReport:
    if len(reason.strip()) < 5:
        raise NexoError("--reason é obrigatório para reprovar")
    run = require_waiting(r, story_id)
    st = r._load_state(run.id)
    refuse_if_external(r, st)
    if st.get("cost_gate"):
        return story_cost.reject(r, story_id, run.id, st, reason, (approver or identity.current(r.ctx)).id)
    approver = check_segregation(r, st, approver)
    bug = story_bugs.create(r, story_id, st, "human", [reason], [], {})
    st["phase"] = "bugfix"
    st["pending_bug"] = bug
    r._save(run.id, st)
    r._transition(story_id, "reject", reason)
    r.ctx.audit(
        "gate.decision",
        {
            "gate": "code",
            "story": story_id,
            "decision": "rejected",
            "approver": approver.id,
            "approver_verified": approver.verified,
            "reason": reason,
            "bug": bug,
        },
        ref=story_id,
    )
    return r._report(story_id, run.id, f"reprovado; bug {bug} criado — rode `nexo resume {story_id}`")


def pr_feedback(r: StoryRunner, story_id: str, source: str, failures: list[str], actor: str) -> StoryReport:
    """CI vermelho ou pedido de mudança no PR ainda aberto → bug e ciclo de correção automático
    (conta nas N tentativas); esgotadas, a história é escalada."""
    run = r.story_run(story_id)
    if run is None:
        raise NexoError(f"nenhuma execução encontrada para {story_id}")
    if not r.workflow.can(r.store.item_state(story_id) or "", "pr_feedback"):
        raise NexoError("o workflow de história não tem o evento `pr_feedback`: atualize o cérebro")
    st = r._load_state(run.id)
    r._transition(story_id, "pr_feedback", f"{source}: {failures[0][:120]}")
    if st["attempts"] >= r.max_attempts:
        return r._escalate(story_id, run.id, st, f"{r.max_attempts} tentativas esgotadas: {failures[0]}")
    bug = story_bugs.create(r, story_id, st, source, failures, [], {source: failures[:20]})
    st["phase"] = "bugfix"
    st["pending_bug"] = bug
    r._save(run.id, st)
    r.ctx.audit(
        "gate.decision",
        {
            "gate": "code",
            "story": story_id,
            "decision": "rejected",
            "source": source,
            "bug": bug,
            "approver": actor,
        },
        ref=story_id,
    )
    r.store.update_run(run.id, state="EmDev", ended_at=None)
    return r._drive(story_id, run.id)


def require_waiting(r: StoryRunner, story_id: str) -> Any:
    if r.store.item_state(story_id) != "AguardandoHumano":
        raise NexoError(f"{story_id} não está aguardando aprovação humana")
    run = r.story_run(story_id)
    if run is None:
        raise NexoError(f"nenhuma execução encontrada para {story_id}")
    return run


def check_segregation(
    r: StoryRunner, st: dict[str, Any], external: identity.Identity | None = None
) -> identity.Identity:
    approver = external or identity.current(r.ctx)  # decisão pelo Teams chega com a identidade SSO
    author = str(st.get("author") or "")
    if author and ":" not in author:
        author = f"os:{author}"  # histórias iniciadas antes da identidade verificada
    res = config.resolved(r.ctx)
    allow = (
        bool((res.values.get("gates") or {}).get("allow_self_approval"))
        and res.origin.get("gates.allow_self_approval", "default") in identity.TRUSTED_ORIGINS
    )  # desligar a segregação é decisão da org/squad, nunca do próprio dev
    if author == approver.id and not allow:
        r.ctx.audit
        raise PolicyViolation("segregação de funções: o autor não pode aprovar/reprovar o próprio portão")
    return approver


def apply_external(r: StoryRunner, story_id: str, request_id: str, decision: str, by: str) -> StoryReport:
    """Aplica o sinal externo (`continue`/`stop`) ao portão que o pediu."""
    run = require_waiting(r, story_id)
    st = r._load_state(run.id)
    gate = st.get("cost_gate") or {}
    if gate.get("request") == request_id:
        if decision == "continue":
            pct = approval.cost_raise_pct(r.ctx)
            return story_cost.raise_cap(r, story_id, run.id, st, pct, False, by, True)
        return story_cost.reject(r, story_id, run.id, st, f"parado pela aprovação externa {request_id}", by)
    if st.get("external_approval") != request_id:
        raise NexoError(f"{request_id} não corresponde ao portão pendente de {story_id}")
    del st["external_approval"]
    r._save(run.id, st)
    if decision == "continue":
        st["approved_by"] = by
        r._save(run.id, st)
        r._transition(story_id, "approve", f"aprovação externa {request_id}")
        r.ctx.audit(
            "gate.decision",
            {"gate": "code", "story": story_id, "decision": "approved", "approver": by, "source": request_id},
            ref=story_id,
        )
        r.store.update_run(run.id, state="Concluido", ended_at=now_iso())
        story_publish.as_built(r, story_id)
        return r._report(story_id, run.id, f"aprovado externamente ({request_id})")
    r._transition(story_id, "external_stop", f"parado pela aprovação externa {request_id}")
    r.ctx.audit(
        "gate.decision",
        {"gate": "code", "story": story_id, "decision": "rejected", "approver": by, "source": request_id},
        ref=story_id,
    )
    r.store.update_run(run.id, state="Escalado", reason=f"parado externamente ({request_id})")
    return r._report(story_id, run.id, f"parado pela aprovação externa {request_id}")


def refuse_if_external(r: StoryRunner, st: dict[str, Any]) -> None:
    pending = st.get("external_approval") or (st.get("cost_gate") or {}).get("request")
    if pending:
        raise NexoError(
            f"a aprovação deste portão é externa ({pending}): aguarde o sinal ou rode `nexo approvals sync`"
        )
