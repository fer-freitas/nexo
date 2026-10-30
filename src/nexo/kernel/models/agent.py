"""Contrato neutro entre o Nexo e as ferramentas de agente (AgentPort)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Tipos de evento normalizados emitidos pelos adaptadores.
INIT = "init"
MESSAGE = "message"
TOOL_CALL = "tool_call"
TOOL_RESULT = "tool_result"
FILE_CHANGE = "file_change"
USAGE = "usage"
QUOTA = "quota"  # informação de cota ainda disponível (utilização, reset)
RATE_LIMITED = "rate_limited"
ERROR = "error"
DONE = "done"
CLARIFICATION = "clarification"  # emitido pelo adaptador fake (agentes reais usam request_clarification)
PROGRESS = "progress"  # emitido pelo adaptador fake (agentes reais usam a tool report_progress)

# Classes de erro
ERR_AUTH = "auth"
ERR_CONTEXT = "context_limit"
ERR_OVERLOADED = "overloaded"
ERR_TIMEOUT = "timeout"
ERR_POLICY = "policy_violation"
ERR_TECHNICAL = "technical"
ERR_NOT_INSTALLED = "not_installed"
ERR_MODEL = "model_unavailable"  # o plano da conta não libera o modelo pedido (achado no teste real do Copilot)


@dataclass(frozen=True)
class AgentEvent:
    kind: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentRequest:
    role: str
    prompt: str
    workdir: str
    model: str
    allowed_tools: tuple[str, ...] = ()
    denied_tools: tuple[str, ...] = ()
    max_turns: int = 40
    timeout_s: float = 1800.0
    output_schema: dict[str, Any] | None = None
    mcp_config: dict[str, Any] | None = None
    settings: dict[str, Any] | None = None
    plugin_dirs: tuple[str, ...] = ()
    data_class: str = "confidential"
    env: dict[str, str] = field(default_factory=dict)
    env_allow: tuple[str, ...] = ()  # permissões explícitas além da lista base
    container: dict[str, Any] | None = None  # modo isolado — image, engine, network, proxy


@dataclass
class UsageRecord:
    unit: str  # tokens_in | tokens_out | cache_read | cache_write | premium_request | acu
    quantity: float
    api_equiv_usd: float = 0.0
    estimated: bool = False


@dataclass
class AgentOutcome:
    status: str = "unknown"  # success | rate_limited | error | timeout | interrupted | policy_violation
    error_kind: str | None = None
    message: str = ""
    text: str = ""
    structured: dict[str, Any] | None = None
    usage: list[UsageRecord] = field(default_factory=list)
    reset_at: float | None = None
    files_changed: list[str] = field(default_factory=list)
    tool_calls: int = 0
    quota_utilization: float | None = None


@dataclass(frozen=True)
class ToolInfo:
    name: str
    installed: bool
    version: str | None = None
    path: str | None = None


@dataclass(frozen=True)
class AuthStatus:
    authenticated: bool | None  # None = não verificável
    detail: str = ""


@dataclass(frozen=True)
class Capabilities:
    verified: bool
    structured_output: bool = False
    native_hooks: bool = False
    mcp: bool = False
    skills: bool = False
    images: bool = False
    remote_execution: bool = False
    usage_unit: str = "tokens"
