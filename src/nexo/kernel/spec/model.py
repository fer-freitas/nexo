"""Modelo estruturado da especificação (fonte de verdade: spec.yaml) e nota de prontidão."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

TEST_TYPES = ("unit", "integration", "contract", "e2e", "load", "architecture", "lint", "security")
NFR_KEYS = ("performance", "security", "availability")
RISK_AREAS = ("auth", "payments", "migration", "infra", "personal_data", "dependency", "other")
DATA_CLASSES = ("public", "internal", "confidential", "restricted")


class Scenario(BaseModel):
    id: str
    title: str
    given: list[str] = Field(default_factory=list)
    when: list[str] = Field(default_factory=list)
    then: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not re.fullmatch(r"AC-\d+", value):
            raise ValueError("id de critério deve ser AC-<n>")
        return value

    @property
    def complete(self) -> bool:
        return bool(self.title.strip() and self.when and self.then)


class Contract(BaseModel):
    id: str
    type: str  # openapi | jsonschema | asyncapi
    path: str
    summary: str = ""


class Risk(BaseModel):
    area: str
    description: str

    @field_validator("area")
    @classmethod
    def _area(cls, value: str) -> str:
        if value not in RISK_AREAS:
            raise ValueError(f"área de risco inválida: {value} (use {', '.join(RISK_AREAS)})")
        return value


class Metric(BaseModel):
    name: str
    baseline: str = ""
    target: str = ""
    source: str = ""


class Epic(BaseModel):
    """Épico ao qual a feature pertence: resultado de negócio mensurável."""

    id: str = ""
    title: str = ""
    outcome: str = ""
    metrics: list[Metric] = Field(default_factory=list)
    sponsor: str = ""
    horizon: str = ""


class Requirement(BaseModel):
    id: str
    title: str
    statement: str
    priority: str = "must"  # must | should | could | wont (MoSCoW)
    source: str = ""
    rules: list[str] = Field(default_factory=list)  # ids RN-n
    acceptance: list[str] = Field(default_factory=list)  # ids AC-n

    @field_validator("id")
    @classmethod
    def _rid(cls, value: str) -> str:
        if not re.fullmatch(r"RF-\d+", value):
            raise ValueError("id de requisito deve ser RF-<n>")
        return value


class BusinessRule(BaseModel):
    id: str
    text: str
    example: str = ""


class Component(BaseModel):
    name: str
    responsibility: str = ""
    change: str = ""  # novo | alterado | sem mudança
    owner: str = ""


class DataChange(BaseModel):
    entity: str
    change: str = ""
    migration: str = ""
    reversible: bool = True
    data_class: str = ""


class ObservabilityItem(BaseModel):
    flow: str
    logs: str = ""
    metrics: str = ""
    traces: str = ""
    alerts: str = ""


class Alternative(BaseModel):
    name: str
    summary: str = ""
    reason: str = ""


class Design(BaseModel):
    """Design técnico. Obrigatório quando há contrato, mudança de dado ou infraestrutura."""

    context: str = ""
    solution: str = ""
    components: list[Component] = Field(default_factory=list)
    data: list[DataChange] = Field(default_factory=list)
    authz: str = ""
    privacy: str = ""
    threats: list[str] = Field(default_factory=list)
    observability: list[ObservabilityItem] = Field(default_factory=list)
    testing: str = ""
    rollout: str = ""
    flag: str = ""
    rollback: str = ""
    alternatives: list[Alternative] = Field(default_factory=list)
    impacted: list[str] = Field(default_factory=list)
    hotspots: list[str] = Field(default_factory=list)
    coverage: str = ""
    characterization: str = ""


class Decision(BaseModel):
    """Decisão relevante da feature: vira ADR pelo template."""

    title: str
    context: str = ""
    decision: str = ""
    consequences: str = ""
    alternatives: list[Alternative] = Field(default_factory=list)


class Spec(BaseModel):
    id: str
    title: str
    objective: str = ""
    problem: str = ""
    personas: list[str] = Field(default_factory=list)
    scope_in: list[str] = Field(default_factory=list)
    scope_out: list[str] = Field(default_factory=list)
    acceptance: list[Scenario] = Field(default_factory=list)
    contracts: list[Contract] = Field(default_factory=list)
    contracts_not_applicable: str = ""
    nfr: dict[str, str] = Field(default_factory=dict)
    data_class: str = ""
    dependencies: list[str] = Field(default_factory=list)
    dependencies_none: bool = False
    test_strategy: dict[str, str] = Field(default_factory=dict)
    risks: list[Risk] = Field(default_factory=list)
    risks_reviewed: bool = False
    assumptions: list[str] = Field(default_factory=list)
    # artefatos completos (épico, requisitos funcionais, regras, design técnico, decisões)
    epic: Epic | None = None
    requirements: list[Requirement] = Field(default_factory=list)
    rules: list[BusinessRule] = Field(default_factory=list)
    design: Design | None = None
    decisions: list[Decision] = Field(default_factory=list)

    @property
    def needs_design(self) -> bool:
        return bool(self.contracts or (self.design and self.design.data)) or any(
            r.area in ("migration", "infra") for r in self.risks
        )

    @field_validator("data_class")
    @classmethod
    def _dc(cls, value: str) -> str:
        if value and value not in DATA_CLASSES:
            raise ValueError(f"classe de dados inválida: {value}")
        return value


# Campos que o planner pode alterar.
PATCHABLE = tuple(f for f in Spec.model_fields if f not in ("id",))


@dataclass(frozen=True)
class ReadinessItem:
    key: str
    label: str
    weight: int
    satisfied: bool
    hint: str


@dataclass(frozen=True)
class Readiness:
    items: tuple[ReadinessItem, ...]
    blocking_questions: int

    @property
    def score(self) -> int:
        """Percentual ponderado (0–100); 100 somente com todos os itens atendidos."""
        total = sum(i.weight for i in self.items)
        done = sum(i.weight for i in self.items if i.satisfied)
        pct = round(100 * done / total) if total else 0
        return 99 if pct == 100 and self.missing else pct

    @property
    def ready(self) -> bool:
        return not self.missing and self.blocking_questions == 0

    @property
    def missing(self) -> list[ReadinessItem]:
        return [i for i in self.items if not i.satisfied]


def _filled(text: str) -> bool:
    return len(text.strip()) >= 3


def readiness(spec: Spec, blocking_questions: int) -> Readiness:
    """Determinística: nenhum LLM participa do cálculo."""
    contracts_ok = bool(spec.contracts) or _filled(spec.contracts_not_applicable)
    items = (
        ReadinessItem(
            "objective",
            "Objetivo e problema de negócio",
            10,
            _filled(spec.objective) and _filled(spec.problem),
            "descreva objetivo e problema",
        ),
        ReadinessItem("personas", "Personas/atores", 5, bool(spec.personas), "liste quem usa"),
        ReadinessItem(
            "scope",
            "Escopo e fora de escopo",
            10,
            bool(spec.scope_in) and bool(spec.scope_out),
            "defina o que entra e o que não entra",
        ),
        ReadinessItem(
            "acceptance",
            "Critérios de aceite em Gherkin",
            20,
            bool(spec.acceptance) and all(s.complete for s in spec.acceptance),
            "cada critério precisa de título, when e then",
        ),
        ReadinessItem(
            "contracts",
            "Contratos (API/eventos/dados)",
            15,
            contracts_ok,
            "declare contratos ou justifique que não se aplicam",
        ),
        ReadinessItem(
            "nfr",
            "Requisitos não funcionais",
            10,
            all(_filled(spec.nfr.get(k, "")) for k in NFR_KEYS),
            "desempenho, segurança e disponibilidade",
        ),
        ReadinessItem(
            "data_class", "Classificação de dados", 5, bool(spec.data_class), "public/internal/confidential/restricted"
        ),
        ReadinessItem(
            "dependencies",
            "Dependências e integrações",
            5,
            bool(spec.dependencies) or spec.dependencies_none,
            "liste dependências ou declare que não há",
        ),
        ReadinessItem(
            "test_strategy",
            "Estratégia de testes por tipo",
            10,
            all(_filled(spec.test_strategy.get(t, "")) for t in TEST_TYPES),
            "um item por tipo: " + ", ".join(TEST_TYPES) + " (use 'n/a: motivo')",
        ),
        ReadinessItem(
            "risks",
            "Riscos e áreas sensíveis",
            5,
            bool(spec.risks) or spec.risks_reviewed,
            "liste riscos ou declare que foram revisados",
        ),
    )
    return Readiness(items, blocking_questions)


def apply_patch(spec: Spec, patch: dict[str, Any]) -> tuple[Spec, list[str]]:
    """Aplica um patch por seção validando o resultado; devolve a nova spec e as seções alteradas."""
    unknown = [k for k in patch if k not in PATCHABLE]
    if unknown:
        raise ValueError(f"seções desconhecidas no patch: {unknown}")
    data = spec.model_dump()
    changed = [k for k, v in patch.items() if data.get(k) != v]
    data.update(patch)
    try:
        return Spec.model_validate(data), changed
    except ValidationError as exc:
        first = exc.errors()[0]
        raise ValueError(f"patch inválido em {'.'.join(str(x) for x in first['loc'])}: {first['msg']}") from exc
