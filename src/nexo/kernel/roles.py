"""Fluxo por papéis: discovery do PM, sessão conjunta com a IA e aprovação de cada papel sobre o pacote.

O pacote tem três partes: requisitos (aprova o PM), design técnico e SDD (aprova o tech lead) e plano (aprova o
dev). Cada aprovação guarda o hash da parte; a parte depende das anteriores, então mudar um requisito invalida as
aprovações de design e plano. A sessão conjunta é exigida pelo risco e pelo tamanho da feature (política da org).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, field_validator

from nexo.kernel.backlog import Backlog
from nexo.kernel.spec.model import Spec

PARTS = ("requisitos", "design", "plano")
ROLES = ("pm", "tech_lead", "dev")
ROLE_FOR = {"requisitos": "pm", "design": "tech_lead", "plano": "dev"}
ROLE_LABEL = {"pm": "PM", "tech_lead": "tech lead", "dev": "dev"}
PART_LABEL = {"requisitos": "requisitos funcionais", "design": "design técnico e SDD", "plano": "plano de execução"}
DECISIONS = ("build", "drop", "investigate")

REQUIREMENT_FIELDS = (
    "title",
    "objective",
    "problem",
    "personas",
    "scope_in",
    "scope_out",
    "acceptance",
    "requirements",
    "rules",
    "epic",
    "data_class",
)
DESIGN_FIELDS: tuple[str, ...] = (
    "design",
    "contracts",
    "contracts_not_applicable",
    "decisions",
    "nfr",
    "risks",
    "test_strategy",
    "dependencies",
    "dependencies_none",
)


# ------------------------------------------------------------------ discovery


class Evidence(BaseModel):
    source: str
    finding: str


class Hypothesis(BaseModel):
    id: str
    statement: str
    metric: str = ""
    target: str = ""

    @field_validator("id")
    @classmethod
    def _hid(cls, value: str) -> str:
        if not re.fullmatch(r"H-\d+", value):
            raise ValueError("id de hipótese deve ser H-<n>")
        return value


class Experiment(BaseModel):
    hypothesis: str
    method: str
    result: str = ""


class Discovery(BaseModel):
    """Discovery do PM: oportunidade, evidências, hipóteses e experimentos até a decisão de construir."""

    title: str = ""
    problem: str = ""
    audience: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    experiments: list[Experiment] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    recommendation: str = ""  # build | drop | investigate (sugestão do agente; quem decide é o PM)
    decision: str = ""
    decision_reason: str = ""
    decided_by: str = ""


def discovery_missing(d: Discovery) -> list[str]:
    """O que falta para o PM decidir com base em evidência (determinístico)."""
    missing = []
    if len(d.problem.strip()) < 3:
        missing.append("problema")
    if not d.opportunities:
        missing.append("oportunidade")
    if not d.evidence:
        missing.append("evidência")
    if not any(h.metric.strip() for h in d.hypotheses):
        missing.append("hipótese com métrica de sucesso")
    return missing


def discovery_brief(d: Discovery) -> str:
    """Resumo do discovery que vira insumo do refinamento, sem cópia manual."""
    lines = [f"# Discovery: {d.title}", "", f"Problema: {d.problem}", ""]
    if d.audience:
        lines += ["Público: " + ", ".join(d.audience), ""]
    lines += ["## Oportunidades", *[f"- {o}" for o in d.opportunities], "", "## Evidências"]
    lines += [f"- {e.finding} (fonte: {e.source})" for e in d.evidence]
    lines += ["", "## Hipóteses"]
    lines += [f"- {h.id}: {h.statement} — métrica {h.metric or '?'}, meta {h.target or '?'}" for h in d.hypotheses]
    if d.experiments:
        lines += ["", "## Experimentos"]
        lines += [f"- {x.hypothesis}: {x.method}" + (f" → {x.result}" if x.result else "") for x in d.experiments]
    if d.open_questions:
        lines += ["", "## Dúvidas em aberto", *[f"- {q}" for q in d.open_questions]]
    if d.decision:
        lines += ["", f"Decisão do PM: {d.decision} — {d.decision_reason}"]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ pacote e aprovações


def _digest(data: Any) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


def part_digests(spec: Spec, draft: Backlog | None) -> dict[str, str]:
    """Hash de cada parte, encadeado: design inclui requisitos; plano inclui design."""
    data = spec.model_dump(mode="json")
    req = _digest({k: data.get(k) for k in REQUIREMENT_FIELDS})
    design = _digest({"up": req, **{k: data.get(k) for k in DESIGN_FIELDS}})
    plan = _digest({"up": design, "backlog": draft.model_dump(mode="json") if draft else None})
    return {"requisitos": req, "design": design, "plano": plan}


class Approval(BaseModel):
    part: str
    by: str
    verified: bool = False
    at: str
    digest: str
    session: str = ""


class Rejection(BaseModel):
    part: str
    by: str
    at: str
    reason: str
    digest: str  # a rejeição vale enquanto a parte não mudar; o retrabalho do agente muda o hash


class Session(BaseModel):
    id: str
    started_at: str
    started_by: str
    participants: dict[str, list[str]] = Field(default_factory=dict)  # papel → identidades
    closed_at: str = ""
    notes: str = ""

    @property
    def complete(self) -> bool:
        """Sessão conjunta válida: encerrada e com os três papéis presentes."""
        return bool(self.closed_at) and all(self.participants.get(r) for r in ROLES)


class RoleRecord(BaseModel):
    """Estado do fluxo por papéis da feature (`specs/F-n/roles.yaml`)."""

    sessions: list[Session] = Field(default_factory=list)
    approvals: list[Approval] = Field(default_factory=list)
    rejections: list[Rejection] = Field(default_factory=list)
    packaged_at: str = ""

    def open_session(self) -> Session | None:
        return next((s for s in reversed(self.sessions) if not s.closed_at), None)

    def joint_done(self) -> bool:
        return any(s.complete for s in self.sessions)

    def current(self, part: str) -> Approval | None:
        return next((a for a in reversed(self.approvals) if a.part == part), None)


@dataclass(frozen=True)
class PartStatus:
    part: str
    role: str
    status: str  # aprovada | desatualizada | rejeitada | pendente
    by: str = ""
    detail: str = ""


def part_status(record: RoleRecord, digests: dict[str, str]) -> list[PartStatus]:
    out = []
    for part in PARTS:
        approval = record.current(part)
        rejection = next((r for r in reversed(record.rejections) if r.part == part), None)
        if rejection and rejection.digest == digests[part] and (approval is None or rejection.at > approval.at):
            out.append(PartStatus(part, ROLE_FOR[part], "rejeitada", rejection.by, rejection.reason))
        elif approval is None:
            out.append(PartStatus(part, ROLE_FOR[part], "pendente"))
        elif approval.digest != digests[part]:
            out.append(
                PartStatus(part, ROLE_FOR[part], "desatualizada", approval.by, "a parte mudou depois da aprovação")
            )
        else:
            out.append(PartStatus(part, ROLE_FOR[part], "aprovada", approval.by))
    return out


def all_approved(statuses: list[PartStatus]) -> bool:
    return all(s.status == "aprovada" for s in statuses)


# ------------------------------------------------------------------ política da sessão conjunta


@dataclass(frozen=True)
class JointPolicy:
    mode: str = "auto"  # always | auto | never
    min_points: int = 13
    max_stories: int = 5
    sensitive: tuple[str, ...] = ("auth", "payments", "personal_data", "migration", "infra")

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> JointPolicy:
        return cls(
            mode=str(cfg.get("joint_session", "auto")),
            min_points=int(cfg.get("min_points", 13)),
            max_stories=int(cfg.get("max_stories", 5)),
            sensitive=tuple(str(x) for x in cfg.get("sensitive") or cls.sensitive),
        )


def joint_reasons(spec: Spec, draft: Backlog | None, policy: JointPolicy) -> list[str]:
    """Por que a feature exige sessão conjunta (vazio = refinamento assíncrono basta)."""
    if policy.mode == "never":
        return []
    if policy.mode == "always":
        return ["a org exige sessão conjunta em toda feature"]
    reasons = []
    areas = sorted({r.area for r in spec.risks if r.area in policy.sensitive})
    if areas:
        reasons.append("risco em área sensível: " + ", ".join(areas))
    if spec.data_class == "restricted":
        reasons.append("dados restritos")
    if draft is not None:
        points = sum(s.points for s in draft.stories)
        if points >= policy.min_points:
            reasons.append(f"tamanho: {points} pontos (limite {policy.min_points})")
        if len(draft.stories) > policy.max_stories:
            reasons.append(f"tamanho: {len(draft.stories)} histórias (limite {policy.max_stories})")
    return reasons
