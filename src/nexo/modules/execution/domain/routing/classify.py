"""Classificação determinística de atividades (sem LLM)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from nexo.kernel.models.work import Task

KIND_KEYWORDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("migration", re.compile(r"(?i)\b(migra(?:tion|ção|cao)|schema|alter table|ef core migration|alembic)\b")),
    ("infra", re.compile(r"(?i)\b(terraform|bicep|helm|kubernetes|dockerfile|pipeline|iac)\b")),
    ("test", re.compile(r"(?i)\b(teste?s?|tests?|spec|cobertura|coverage)\b")),
    ("docs", re.compile(r"(?i)\b(docs?|documenta(?:ção|cao|tion)|readme|adr)\b")),
    ("frontend", re.compile(r"(?i)\b(react|tela|componente|component|ui|frontend|página|page|css)\b")),
    ("contract", re.compile(r"(?i)\b(openapi|contrato|contract|schema json|asyncapi)\b")),
    ("backend", re.compile(r"(?i)\b(endpoint|api|serviço|service|controller|handler|repository|backend)\b")),
)

RISK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("auth", re.compile(r"(?i)(auth|login|senha|password|token|jwt|oauth|permiss|role)")),
    ("payments", re.compile(r"(?i)(pagamento|payment|refund|estorno|cart[aã]o|card|pix|boleto)")),
    ("migration", re.compile(r"(?i)(migra|migration|alter table|schema)")),
    ("infra", re.compile(r"(?i)(terraform|bicep|helm|k8s|kubernetes|\.github/workflows|pipeline)")),
    ("personal_data", re.compile(r"(?i)(cpf|cnpj|lgpd|pii|dados pessoais|personal data|e-?mail|telefone)")),
    (
        "dependency",
        re.compile(r"(?i)(package\.json|\.csproj|pyproject\.toml|requirements\.txt|nova depend|new depend)"),
    ),
)


@dataclass(frozen=True)
class Classification:
    kind: str | None
    size: str
    risk_tags: tuple[str, ...] = field(default_factory=tuple)
    source: str = "rules"  # rules | declared | llm

    @property
    def resolved(self) -> bool:
        return self.kind is not None


def estimate_size(task: Task) -> str:
    if task.size:
        return task.size
    files = len(task.files_hint)
    criteria = len(task.acceptance)
    if files <= 2 and criteria <= 2:
        return "S"
    if files <= 8 and criteria <= 6:
        return "M"
    return "L"


def classify(task: Task) -> Classification:
    text = " ".join([task.title, task.description, *task.acceptance])
    haystack = " ".join([text, *task.files_hint])
    risks = tuple(tag for tag, pattern in RISK_PATTERNS if pattern.search(haystack))
    size = estimate_size(task)
    if task.kind:
        return Classification(task.kind, size, risks, "declared")
    matches = [kind for kind, pattern in KIND_KEYWORDS if pattern.search(text)]
    if len(matches) == 1 or (matches and matches[0] in {"migration", "infra"}):
        return Classification(matches[0], size, risks, "rules")
    return Classification(None, size, risks, "rules")
