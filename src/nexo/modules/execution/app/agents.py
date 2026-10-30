"""Registro de adaptadores de agente, disponibilidade das rotas e autoinvocação do Nexo."""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.models.agent import AuthStatus, ToolInfo
from nexo.kernel.runtime.resources import load_yaml
from nexo.kernel.runtime.system import shell_join
from nexo.modules.execution.adapters.agents.base import AgentAdapter
from nexo.modules.execution.adapters.agents.claude import ClaudeAdapter
from nexo.modules.execution.adapters.agents.fake import FakeAdapter, Scenario
from nexo.modules.execution.adapters.agents.generic import GenericTextAdapter
from nexo.modules.execution.domain.routing.catalog import Catalog
from nexo.modules.execution.domain.routing.router import RouteAvailability
from nexo.store.runs import RunStore

TOOLS = ("claude", "copilot", "devin")
TEXT_CLIS = ("copilot", "devin")  # adaptador genérico por contrato
API_SECRET = {
    "anthropic": "anthropic",
    "openai": "openai",
    "azure-openai": "azure_openai",
    "gateway": "llm_gateway",
    "bedrock": "bedrock",
    "gemini-api": "gemini_api",
}
API_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "azure-openai": "AZURE_OPENAI_API_KEY",
    "gateway": "NX_LLM_GATEWAY_KEY",
    "bedrock": "AWS_BEARER_TOKEN_BEDROCK",
    "gemini-api": "GEMINI_API_KEY",
}


def _env_secret(name: str) -> str | None:
    """Chave do ambiente; no Bedrock também as credenciais padrão da AWS (viram `ACCESS:SECRET[:TOKEN]`)."""
    value = os.environ.get(API_ENV[name])
    if value:
        return value
    if name == "bedrock" and os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
        parts = [os.environ["AWS_ACCESS_KEY_ID"], os.environ["AWS_SECRET_ACCESS_KEY"]]
        return ":".join([*parts, *([os.environ["AWS_SESSION_TOKEN"]] if os.environ.get("AWS_SESSION_TOKEN") else [])])
    if name == "gemini-api":
        return os.environ.get("GOOGLE_API_KEY") or None
    return None


def api_config(ctx: AppContext) -> dict[str, Any]:
    from nexo.kernel.platform import config

    return dict((config.resolved(ctx).values.get("agents") or {}).get("api") or {})


def api_mode(ctx: AppContext) -> str:
    """F37 · `agents.api.mode`: off (só CLIs) | fallback (API quando as CLIs não estão disponíveis) | on | only."""
    from nexo.kernel.models.llm_api import MODES

    mode = str(api_config(ctx).get("mode") or "off")
    return mode if mode in MODES else "off"


def _api_adapters(ctx: AppContext) -> dict[str, AgentAdapter]:
    from nexo.kernel.models.llm_api import PROVIDERS, price_table
    from nexo.kernel.secrets import credentials
    from nexo.modules.execution.adapters.agents.api import ApiAdapter, Hooks
    from nexo.modules.execution.app import hooks
    from nexo.modules.knowledge.app import brain as knowledge_brain

    cfg = api_config(ctx)
    out: dict[str, AgentAdapter] = {}
    for name in PROVIDERS:
        pcfg = dict((cfg.get("providers") or {}).get(name) or {})
        if not pcfg.get("enabled"):
            continue

        def secret(name: str = name) -> str | None:
            return credentials.get_secret(API_SECRET[name]) or _env_secret(name)

        def denied(run_id: str, tool: str, tool_input: dict[str, Any], profile: str) -> None:
            policy = hooks.load_policy(ctx)
            hooks.check_action(ctx, policy, run_id, ".", tool, tool_input, profile)

        def brain(query: str) -> str:
            found = knowledge_brain.search(ctx, query, limit=5)
            return "\n".join(f"- {r['path']}: {r['excerpt']}" for r in found) or "nada encontrado"

        out[name] = ApiAdapter(
            name,
            pcfg,
            Hooks(secret, lambda: hooks.load_policy(ctx), denied, brain, price_table(cfg.get("prices"))),
        )
    return out


def fake_scenario_path() -> Path | None:
    value = os.environ.get("NX_FAKE_SCENARIO")
    return Path(value) if value else None


def adapters(ctx: AppContext) -> dict[str, AgentAdapter]:
    scenario_path = fake_scenario_path()
    if scenario_path is not None:
        scenario = Scenario(scenario_path, ctx.home / "fake-state.json")
        return {name: FakeAdapter(name, scenario) for name in scenario.tools()}
    brain = ctx.brain_dir if ctx.brain_dir.is_dir() else None
    registry: dict[str, AgentAdapter] = {"claude": ClaudeAdapter(brain)}
    registry.update({name: GenericTextAdapter(name, brain) for name in TEXT_CLIS})
    if api_mode(ctx) != "off":  # conexão direta com as LLMs, quando a org ou o dev liga
        registry.update(_api_adapters(ctx))
    return registry


def catalog(ctx: AppContext) -> Catalog:
    brain = ctx.brain_dir if ctx.brain_dir.is_dir() else None
    return Catalog.from_mapping(load_yaml("routing.yaml", brain))


@dataclass
class ToolState:
    info: ToolInfo
    auth: AuthStatus
    limited_until: float | None
    utilization: float | None


def tool_states(ctx: AppContext, registry: dict[str, AgentAdapter], check_auth: bool = True) -> dict[str, ToolState]:
    stored = RunStore(ctx.conn).route_states()
    out: dict[str, ToolState] = {}
    for name, adapter in registry.items():
        info = adapter.detect()
        auth = adapter.auth_status() if (check_auth and info.installed) else AuthStatus(None, "não verificado")
        row = stored.get(name)
        limited = float(row["limited_until"]) if row and row["limited_until"] else None
        if limited is not None and limited <= time.time():
            limited = None
        out[name] = ToolState(info, auth, limited, float(row["utilization"]) if row and row["utilization"] else None)
    return _apply_api_mode(api_mode(ctx), out)


def _apply_api_mode(mode: str, states: dict[str, ToolState]) -> dict[str, ToolState]:
    """`only` tira as CLIs; `fallback` só libera a API quando nenhuma CLI está pronta (sem cota, sem login)."""
    api = {n for n, s in states.items() if s.info.version == "api"}
    if not api or mode in ("on", "off"):
        return states

    def ready(s: ToolState) -> bool:
        return s.info.installed and s.auth.authenticated is not False and s.limited_until is None

    if mode == "only":
        hide = set(states) - api
    else:
        hide = api if any(ready(s) for n, s in states.items() if n not in api) else set()
    for name in hide:
        s = states[name]
        why = "reserva (CLIs disponíveis)" if mode == "fallback" else "desligada (agents.api.mode: only)"
        states[name] = ToolState(ToolInfo(name, False, why, s.info.path), s.auth, s.limited_until, s.utilization)
    return states


def availability(states: dict[str, ToolState], budget: Any = None) -> dict[str, RouteAvailability]:
    """`budget` (BudgetStatus) exclui ferramentas cujo orçamento do dev acabou."""
    return {
        name: RouteAvailability(
            installed=s.info.installed,
            authenticated=s.auth.authenticated,
            limited_until=s.limited_until,
            budget_ok=budget.tool_ok(name) if budget is not None else True,
        )
        for name, s in states.items()
    }


def nexo_command() -> list[str]:
    """Como reinvocar este mesmo Nexo (hooks nativos e servidor MCP)."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "nexo"]


def mcp_config(
    ctx: AppContext, run_id: str, role: str | None = None, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Servidor do próprio Nexo + MCPs oficiais ligados e liberados para esta execução."""
    cmd = nexo_command()
    return {
        "mcpServers": {
            **(extra or {}),
            "nexo": {
                "command": cmd[0],
                "args": [*cmd[1:], "mcp", "serve", "--run", run_id, *(["--role", role] if role else [])],
                "env": {"NX_HOME": str(ctx.home)},
            },
        }
    }


def hook_settings(ctx: AppContext, run_id: str) -> dict[str, Any]:
    """Hooks nativos do Claude Code: H3 (pre_tool) e H4."""
    command = shell_join([*nexo_command(), "hook", "run", "pre-tool-policy", "--run", run_id])
    return {
        "hooks": {
            "PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": command, "timeout": 20}]}],
        },
        "env": {"NX_HOME": str(ctx.home)},
    }


CLAUDE_LOGIN = ["claude", "auth", "login"]


def login(ctx: AppContext, tool: str) -> tuple[int, str]:
    """login de uma ferramenta de IA pelo comando oficial dela (abre o navegador na tela da pessoa); o Nexo
    não guarda credencial — só chama e confere o resultado (`auth_status`). (código de saída, situação depois)."""
    import subprocess

    from nexo.kernel.runtime.system import windows_command

    registry = adapters(ctx)
    adapter = registry.get(tool)
    if adapter is None:
        raise NexoError(f"ferramenta desconhecida: {tool} (use {', '.join(sorted(registry))})")
    info = adapter.detect()
    if not info.installed:
        raise NexoError(f"{tool} não está instalado (veja `nexo doctor`)")
    spec = getattr(adapter, "spec", {}) or {}
    command = list(spec.get("login_command") or (CLAUDE_LOGIN if tool == "claude" else []))
    if not command:
        raise NexoError(f"{tool} não tem login pela linha de comando: autentique na própria ferramenta")
    if info.path:
        command[0] = str(info.path)
    code = subprocess.run(windows_command(command), check=False).returncode  # interativo: herda o terminal
    status = adapter.auth_status()
    after = {True: "logado", False: "sem login", None: "não verificável"}[status.authenticated]
    ctx.audit("agent.login", {"tool": tool, "exit_code": code, "status": after})
    return code, after
