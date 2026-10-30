"""Contrato de telemetria `nexo.telemetry/v1` — fonte única para a CLI e o coletor.

Só metadados: tipos de evento e campos numa lista de permissão, sem texto livre, caminhos, código, prompts nem
identificação de pessoas. A CLI **projeta** (seleciona só o permitido); o coletor **valida** (campo fora da lista
recusa o evento inteiro).
"""

from __future__ import annotations

import re
from typing import Any

CONTRACT = "nexo.telemetry/v1"
MAX_BATCH_BYTES = 64 * 1024
MAX_TEXT = 120

EVENT_TYPES = frozenset(
    {
        "run.start",
        "run.complete",
        "run.escalated",
        "run.blocked",
        "run.handoff",
        "route.decision",
        "route.classified",
        "state.transition",
        "gate.decision",
        "bug.created",
        "story.start",
        "story.escalated",
        "story.cost_alert",
        "story.characterized",
        "story.characterization_failed",
        "finops.month_closed",
        "budget.set",
        "budget.granted",
        "policy.violation",
        "llm.result",
        "quality.flaky",
        "quality.run",
        "tracker.error",
        "prompt.used",
        "brain.pulled",
        "approval.requested",
        "approval.resolved",
        "killswitch.blocked",  # P0 pré-piloto
        "killswitch.local",
        "security.injection_suspect",
        "monitor.synthetic",
        "nexo.crash",  # F15
        "demand.created",
        "demand.transitioned",
        "demand.linked",
        "demand.triaged",
        "review.pr",  # revisão de PR humano (veredito e contagens, sem conteúdo)
    }
)
EVENT_KEYS = frozenset(
    {
        "task",
        "story",
        "item",
        "feature",
        "run",
        "route",
        "role",
        "from",
        "to",
        "event",
        "decision",
        "score",
        "gate",
        "breakdown",
        "human_areas",
        "state",
        "bug",
        "source",
        "rule",
        "tool",
        "model",
        "month",
        "limit_brl",
        "dev",
        "handoffs",
        "routes",
        "threshold",
        "kind",
        "cost_brl",
        "cap_brl",
        "old_cap_brl",
        "new_cap_brl",
        "economic",
        "sha256",
        "version",
        "verified",
        "request",
        "channel",
        # insumos do modelo de fatos (números e ids, nunca texto livre)
        "target",
        "points",
        "brl",
        "samples",
        "stack",
        "stages",
        "coverage",
        "findings",
        "failed",
        "size",
        "sources",
        "rules",
        "status",
        "hours",
        "command",
        "frames",
        "mutation",
        "pr",
        "verdict",
        "quality_failed",
        "posted",
        "demand",
        "demand_type",
        "stage",
        "origin",
        "title",
        "parent",
        "holders",
        "blocked",
        "standards",  # aderência ao padrão de código (números e ids de regra)
    }
)
STEP_FIELDS = frozenset(
    {
        "step",
        "run",
        "target",
        "story",
        "role",
        "tool",
        "model",
        "started_at",
        "seconds",
        "outcome",
        "error_kind",
        "tokens_in",
        "tokens_out",
        "cache_read",
        "cache_write",
        "premium_request",
        "acu",
        "ai_credits",
        "api_equiv_usd",
        "usage_brl",
        "estimated",
        "brain_version",
        "project",
        "tool_calls",
    }
)
STORY_FIELDS = frozenset(  # retrato da história a cada transição (o central guarda o mais recente)
    {
        "story",
        "feature",
        "stack",
        "points",
        "state",
        "human_gate",
        "escalated",
        "bugs",
        "attempts",
        "spec_returns",
        "clarifications",
        "handoffs",
        "lead_hours",
        "cycle_hours",
        "waiting_human_hours",
        "cost_brl",
        "cap_brl",
        "estimate_brl",
        "started_at",
        "done_at",
        "project",
    }
)
CONTEXT_FIELDS = frozenset(
    {"squad", "stack", "nexo", "lag_hours", "brain", "agents", "os", "project_name"}
)  # frota  # contexto do lote
USAGE_FIELDS = frozenset(  # totais do dia de IA usada fora do Nexo (sem pasta, projeto, sessão ou conteúdo)
    {
        "tool",
        "model",
        "day",
        "sessions",
        "responses",
        "tokens_in",
        "tokens_out",
        "cache_read",
        "cache_write",
        "premium_request",
        "ai_credits",
        "acu",
        "api_equiv_usd",
        "usage_brl",
    }
)
KINDS = ("event", "step", "story", "usage")
PATHLIKE = re.compile(
    r"^[/\\~]|^[A-Za-z]:[/\\]"
    r"|[/\\][^ ]*\.(py|cs|ts|tsx|js|jsx|java|go|rb|sql|ya?ml|json|md|txt|env|cfg|toml|xml|html|css|sh|ps1)$",
    re.I,
)


def _clean(value: Any) -> bool:
    if isinstance(value, str):
        return len(value) <= MAX_TEXT and not PATHLIKE.search(value)
    if isinstance(value, (bool, int, float)) or value is None:
        return True
    if isinstance(value, list):
        return all(_clean(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _clean(v) for k, v in value.items())
    return False


def project_event(event_type: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """CLI: seleciona só os campos permitidos (texto curto, sem caminhos); tipo fora da lista não sai."""
    if event_type not in EVENT_TYPES:
        return None
    return {k: v for k, v in payload.items() if k in EVENT_KEYS and _clean(v)}


def validate(kind: str, event_type: str, payload: dict[str, Any]) -> str | None:
    """Coletor: devolve o motivo da recusa, ou None se o evento cumpre o contrato."""
    if kind not in KINDS:
        return f"tipo de registro desconhecido: {kind}"
    allowed = {"step": STEP_FIELDS, "story": STORY_FIELDS, "usage": USAGE_FIELDS}.get(kind, EVENT_KEYS)
    if kind == "event" and event_type not in EVENT_TYPES:
        return f"evento fora da lista: {event_type}"
    if kind == "usage" and event_type != "usage.external":
        return f"registro de consumo fora da lista: {event_type}"
    extra = sorted(set(payload) - allowed)
    if extra:
        return f"campos fora da lista: {', '.join(extra)}"
    if "title" in payload and not event_type.startswith("demand."):
        return "título só é aceito em eventos de demanda"
    if not _clean(payload):
        return "valor fora do contrato (texto longo, caminho ou tipo não permitido)"
    return None


def validate_context(context: dict[str, Any]) -> str | None:
    """Contexto do lote: squad e stack declarados pela configuração do projeto, versão e atraso da outbox."""
    extra = sorted(set(context) - CONTEXT_FIELDS)
    if extra:
        return f"contexto fora da lista: {', '.join(extra)}"
    if not _clean(context):
        return "contexto fora do contrato"
    return None
