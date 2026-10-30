"""Demanda como projeção de eventos: a etapa e quem está com ela são calculadas, nunca digitadas.

Eventos são a fonte. `project()` monta o estado atual; `stage_of()` e `holders_of()` derivam a etapa e as pessoas a
partir dos fatos (aprovações, responsáveis, estado da execução, propostas abertas). Assim a demanda nunca fica com
a pessoa errada nem sem dono, e `verify()` consegue conferir a projeção gravada.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ROLE_LABEL = {"pm": "PM", "tl": "tech lead", "qa": "QA", "dev": "dev"}
PART_OWNER = {"requisitos": "pm", "design": "tl", "testes": "qa", "plano": "dev"}
SECTION_OWNER = {"funcional": "pm", "tecnica": "tl", "testes": "qa", "implementacao": "dev"}
SECTION_PART = {"funcional": "requisitos", "tecnica": "design", "testes": "testes", "implementacao": "plano"}
KIND_SECTION = {"technical": "tecnica", "functional": "funcional", "test": "testes"}
TYPES = ("idea", "feature", "story", "task", "bug", "finding", "vuln", "incident", "lesson")
AI_TRIAGE_ORIGINS = frozenset({"review", "followup", "learning"})

# etapa → (nível, papel, ação)
STAGES: dict[str, tuple[str, str, str]] = {
    "triage": ("qualquer", "tl", "Aceitar ou descartar o achado"),
    "idea": ("ideia", "pm", "Decidir e dividir a ideia em features"),
    "functional": ("feature", "pm", "Refinar e aprovar os requisitos"),
    "technical": ("feature", "tl", "Desenhar, aprovar e indicar os responsáveis"),
    "refinement": ("história", "", "Plano de teste e refinamento do dev"),
    "ready": ("história", "tl", "Conferir a Definition of Ready"),
    "build": ("história", "dev", "Construir com o agente"),
    "validation": ("história", "qa", "Validar a entrega"),
    "gate": ("história", "tl", "Decidir o portão"),
    "pr": ("história", "dev", "Abrir o PR e reagir à revisão"),
    "accept": ("história", "pm", "Dar o aceite de negócio"),
    "verify": ("bug", "qa", "Verificar a correção"),
    "split": ("feature", "tl", "Acompanhar as histórias"),
    "done": ("", "", ""),
    "cancelled": ("", "", ""),
}
BUILD_STATES = frozenset({"EmDev", "EmQA", "EmReview", "Bloqueado", "Escalado"})


@dataclass
class Proposal:
    id: str
    demand: str
    section: str
    text: str
    by: str
    at: str
    kind: str = ""
    blocking: bool = False
    status: str = "open"  # open | accepted | declined | assumed
    decided_by: str = ""
    reason: str = ""
    affected: list[str] = field(default_factory=list)

    @property
    def owner_role(self) -> str:
        return SECTION_OWNER.get(self.section, "tl")

    @property
    def open(self) -> bool:
        return self.status in ("open", "assumed")

    @property
    def holds(self) -> bool:
        """Trava o trabalho: bloqueante, sem resposta nem premissa."""
        return self.status == "open" and self.blocking


@dataclass
class Demand:
    id: str
    type: str
    title: str = ""
    project: str = ""
    parent: str = ""
    spawned_by: str = ""
    origin: dict[str, Any] = field(default_factory=dict)
    state: str = ""
    decided: bool = False
    triage: bool = False
    cancelled: bool = False
    accepted: bool = False
    merged: bool = False
    verified: bool = False
    qa_validated: bool = False
    dor_passed: bool = False
    created_at: str = ""
    updated_at: str = ""
    since: str = ""
    responsible: dict[str, str] = field(default_factory=dict)
    queue: list[str] = field(default_factory=list)
    approvals: dict[str, dict[str, str]] = field(default_factory=dict)
    links: dict[str, Any] = field(default_factory=dict)
    returned_to: str = ""
    returned_reason: str = ""
    returned_by: str = ""
    lock: int = 0
    children: list[str] = field(default_factory=list)
    proposals: list[Proposal] = field(default_factory=list)
    stale_parts: list[str] = field(default_factory=list)

    def approved(self, part: str) -> bool:
        return part in self.approvals and part not in self.stale_parts

    @property
    def holding_proposals(self) -> list[Proposal]:
        return [p for p in self.proposals if p.holds]

    @property
    def open_premises(self) -> list[Proposal]:
        return [p for p in self.proposals if p.status == "assumed"]

    @property
    def assigned(self) -> bool:
        return all(self.responsible.get(r) or r in self.queue for r in ("dev", "qa"))


def apply(demands: dict[str, Demand], ev: dict[str, Any]) -> None:
    """Aplica um evento. Evento de demanda desconhecida é ignorado (a ordem é a do ID ordenável)."""
    kind = str(ev.get("type", ""))
    did = str(ev.get("demand", ""))
    data = dict(ev.get("data") or {})
    at = str(ev.get("at", ""))
    if kind == "demand.created":
        demand = Demand(
            id=did,
            type=str(data.get("type", "story")),
            title=str(data.get("title", "")),
            project=str(data.get("project", "")),
            parent=str(data.get("parent", "")),
            spawned_by=str(data.get("spawned_by", "")),
            origin=dict(data.get("origin") or {}),
            state=str(data.get("state", "")),
            triage=bool(data.get("triage")),
            responsible=dict(data.get("responsible") or {}),
            created_at=at,
            since=at,
        )
        demands[did] = demand
        if demand.parent in demands and did not in demands[demand.parent].children:
            demands[demand.parent].children.append(did)
    demand_ = demands.get(did)
    if demand_ is None:
        return
    before = stage_of(demand_, demands)
    _apply_fact(demand_, kind, data, str(ev.get("actor", "")), at)
    demand_.updated_at = at
    if stage_of(demand_, demands) != before:
        demand_.since = at


def _apply_fact(d: Demand, kind: str, data: dict[str, Any], actor: str, at: str) -> None:
    if kind == "demand.state":
        d.state = str(data.get("state", d.state))
    elif kind == "demand.titled":
        d.title = str(data.get("title", d.title))
    elif kind == "demand.decided":
        d.decided = data.get("decision") == "build"
        d.cancelled = d.cancelled or data.get("decision") == "drop"
    elif kind == "demand.assigned":
        role = str(data.get("role"))
        who = str(data.get("who", ""))
        if who:
            d.responsible[role] = who
            d.queue = [r for r in d.queue if r != role]
        elif role not in d.queue:
            d.queue.append(role)
    elif kind == "demand.approved":
        part = str(data.get("part"))
        d.approvals[part] = {"by": actor, "digest": str(data.get("digest", "")), "at": at}
        d.stale_parts = [p for p in d.stale_parts if p != part]
        if d.returned_to == PART_OWNER.get(part):
            d.returned_to = ""
    elif kind == "demand.invalidated":
        part = str(data.get("part"))
        if part in d.approvals and part not in d.stale_parts:
            d.stale_parts.append(part)
        d.dor_passed = False
    elif kind == "demand.rejected":
        d.approvals.pop(str(data.get("part")), None)
    elif kind == "demand.dor":
        d.dor_passed = bool(data.get("passed"))
    elif kind == "demand.validated":
        d.qa_validated = bool(data.get("passed", True))
    elif kind == "demand.verified":
        d.verified = bool(data.get("passed", True))
    elif kind == "demand.merged":
        d.merged = True
    elif kind == "demand.accepted":
        d.accepted = True
    elif kind == "demand.cancelled":
        d.cancelled = True
    elif kind == "demand.triaged":
        d.triage = False
        d.cancelled = d.cancelled or not data.get("accepted", True)
    elif kind == "demand.returned":
        d.returned_to = str(data.get("to", ""))
        d.returned_reason = str(data.get("reason", ""))
        d.returned_by = actor
    elif kind == "demand.linked":
        d.links.update(dict(data.get("links") or {}))
    elif kind == "demand.locked":
        d.lock = int(data.get("version", d.lock + 1))
    elif kind == "proposal.opened":
        d.proposals.append(
            Proposal(
                id=str(data["id"]),
                demand=d.id,
                section=str(data.get("section", "")),
                text=str(data.get("text", "")),
                by=actor,
                at=at,
                kind=str(data.get("kind", "")),
                blocking=bool(data.get("blocking")),
                affected=list(data.get("affected") or []),
            )
        )
    elif kind == "proposal.decided":
        for p in d.proposals:
            if p.id == data.get("id"):
                p.status = str(data.get("status", p.status))
                p.decided_by = actor
                p.reason = str(data.get("reason", ""))


def project(events: list[dict[str, Any]]) -> dict[str, Demand]:
    demands: dict[str, Demand] = {}
    for ev in sorted(events, key=lambda e: str(e.get("id", ""))):
        apply(demands, ev)
    return demands


def stage_of(d: Demand, all_: dict[str, Demand] | None = None) -> str:
    if d.cancelled:
        return "cancelled"
    if d.triage:
        return "triage"
    if d.type == "idea":
        return "split" if d.decided and d.children else "idea"
    if d.type == "feature":
        if not d.approved("requisitos"):
            return "functional"
        kids = [all_[c] for c in d.children if all_ and c in all_ and all_[c].type == "story"]
        if not d.approved("design") or not kids or not all(k.assigned for k in kids):
            return "technical"
        return "done" if kids and all(stage_of(k, all_) == "done" for k in kids) else "split"
    if d.type == "story":
        return _story_stage(d)
    if d.type == "task":
        return "done" if d.state == "Concluido" else "build"
    if d.state == "Concluido":
        return "done" if d.verified or d.type not in ("bug",) else "verify"
    return "technical" if d.type in ("vuln", "incident") and not d.responsible.get("dev") else "build"


def _story_stage(d: Demand) -> str:
    if d.accepted:
        return "done"
    if d.state == "Concluido":
        return "accept" if d.merged else "pr"
    if d.state == "AguardandoHumano":
        return "gate" if d.qa_validated else "validation"
    if d.state in BUILD_STATES:
        return "build"
    if not d.assigned:
        return "technical"
    if not (d.approved("testes") and d.approved("plano")):
        return "refinement"
    if not d.dor_passed:
        return "ready"
    return "build"


def _who(d: Demand, role: str, all_: dict[str, Demand] | None) -> str:
    """Responsável pelo papel: o da demanda ou, na falta, o do pai (bug herda da história; história, da feature)."""
    node: Demand | None = d
    seen: set[str] = set()
    while node is not None and node.id not in seen:
        seen.add(node.id)
        if node.responsible.get(role):
            return node.responsible[role]
        node = (all_ or {}).get(node.parent) if node.parent else None
    return ""


def holders_of(d: Demand, all_: dict[str, Demand] | None = None) -> list[dict[str, str]]:
    """Com quem está: pode ser mais de uma pessoa (QA e dev em paralelo, dono de uma proposta bloqueante)."""
    stage = stage_of(d, all_)
    if stage in ("done", "cancelled"):
        return []
    out: list[dict[str, str]] = []
    if d.returned_to:
        role = d.returned_to
        out.append(
            {"role": role, "who": _who(d, role, all_), "action": f"Responder: {d.returned_reason}", "since": d.since}
        )
        return out
    if stage == "refinement":
        if not d.approved("testes"):
            out.append({"role": "qa", "who": _who(d, "qa", all_), "action": "Aprovar o plano de teste", "since": d.since})
        if not d.approved("plano"):
            out.append({"role": "dev", "who": _who(d, "dev", all_), "action": "Melhorar o refinamento", "since": d.since})
    else:
        _level, role, action = STAGES[stage]
        out.append({"role": role, "who": _who(d, role, all_), "action": action, "since": d.since})
    for p in d.holding_proposals:
        role = p.owner_role
        out.append({"role": role, "who": _who(d, role, all_), "action": f"Decidir {p.id}: {p.text}", "since": p.at})
    for p in d.open_premises:
        role = p.owner_role
        out.append({"role": role, "who": _who(d, role, all_), "action": f"Confirmar a premissa {p.id}", "since": p.at})
    return out


def blocked_by(d: Demand) -> list[str]:
    return [p.id for p in d.holding_proposals]


def why(d: Demand, all_: dict[str, Demand] | None = None) -> str:
    stage = stage_of(d, all_)
    if stage == "done":
        return f"{d.id} está concluída."
    if stage == "cancelled":
        return f"{d.id} foi cancelada."
    people = holders_of(d, all_)
    who = "; ".join(
        f"{ROLE_LABEL.get(h['role'], h['role'])} {h['who'] or '(fila do papel)'}: {h['action']}" for h in people
    )
    return f"{d.id} está na etapa {stage}, com {who}."


def merge_conflict_free_id(existing: list[str], prefix: str) -> str:
    """Próximo número livre, olhando o que já existe (a corrida entre máquinas é tratada no push)."""
    numbers = [int(i.rsplit("-", 1)[1]) for i in existing if i.startswith(prefix + "-") and i.rsplit("-", 1)[1].isdigit()]
    return f"{prefix}-{max(numbers, default=0) + 1}"
