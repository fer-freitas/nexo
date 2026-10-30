"""Chamadas estruturadas a papéis internos (planner, breakdown, classifier…) com roteamento e fallback.

Cada chamada vira uma execução (`runs`) com passos (`steps`) e consumo (`usage`) — base do FinOps.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.models.agent import DONE, ERR_AUTH, AgentOutcome, AgentRequest
from nexo.kernel.platform import config
from nexo.kernel.security.redaction import find, redact
from nexo.modules.execution.adapters.agents.base import AgentAdapter
from nexo.modules.execution.app import agents as agents_app
from nexo.modules.execution.app import hooks
from nexo.modules.execution.domain.routing.router import RoutingRequest, choose
from nexo.modules.setup.app.banner import brain_version
from nexo.store.runs import RunStore, now_iso, project_key

UI = Callable[[str, dict[str, Any]], None]


@dataclass
class StructuredResult:
    data: dict[str, Any]
    run_id: str
    routes: list[str] = field(default_factory=list)


def _noop(_: str, __: dict[str, Any]) -> None:
    return None


def call_structured(*args: Any, **kwargs: Any) -> StructuredResult:
    """Chamada estruturada a um papel de LLM, com span de observabilidade."""
    from nexo.kernel.platform import observability

    role = kwargs.get("role", args[1] if len(args) > 1 else "")
    with observability.span(args[0] if args else kwargs["ctx"], "nexo.llm.call", role=str(role)) as sp:
        result = _call_structured(*args, **kwargs)
        sp.set(run=result.run_id)
        return result


def _call_structured(
    ctx: AppContext,
    role: str,
    target_id: str,
    prompt: str,
    schema: dict[str, Any],
    workdir: Path,
    *,
    facts: dict[str, Any] | None = None,
    data_class: str = "confidential",
    read_files: bool = False,
    validate: Callable[[dict[str, Any]], list[str]] | None = None,
    profile: str | None = None,
    forced_tool: str | None = None,
    forced_model: str | None = None,
    ui: UI | None = None,
    max_attempts: int = 4,
    scope: str | None = None,
    author_vendors: tuple[str, ...] = (),
) -> StructuredResult:
    from nexo.kernel.feedback import FEEDBACK

    ui = ui or (FEEDBACK[0] if FEEDBACK else _noop)
    from nexo.modules.planning.app import product_context

    prompt += product_context.section(ctx, role)  # o produto e o repositório, com orçamento
    from nexo.kernel.platform import telemetry

    telemetry.check_delay(ctx)
    from nexo.kernel.platform import isolation, killswitch

    cfg = config.resolved(ctx).values
    root = ctx.project_root
    store = RunStore(ctx.conn, scope if scope is not None else (project_key(root) if root else ""))
    registry = agents_app.adapters(ctx)
    catalog = agents_app.catalog(ctx)
    allowed = (cfg.get("data_policy") or {}).get(data_class)
    vendors = None if allowed is None or "*" in allowed else tuple(str(v) for v in allowed)
    if vendors == ():  # nenhuma ferramenta pode receber esta classe: explica e diz como sair (achado no uso real)
        raise NexoError(
            f"{target_id} está classificada como '{data_class}', e a política da org não deixa nenhuma ferramenta de "
            f"IA trabalhar com essa classe (data_policy.{data_class}). Se a classificação estiver errada, corrija com "
            f"`plan classify {target_id} <public|internal|confidential>`; se estiver certa, esta parte é feita sem IA."
        )
    run_id = store.create_run(
        target_id,
        str(cfg.get("project") or ""),
        ctx.actor,
        profile or str(cfg.get("profile")),
        brain_version(ctx.brain_dir),
    )
    store.update_run(run_id, state=f"llm:{role}")
    from jsonschema import Draft202012Validator  # import caro (~0,7 s); só quando há validação

    validator = Draft202012Validator(schema)
    exclude: set[str] = set()
    routes: list[str] = []
    repair = ""
    repaired: set[str] = set()
    # resposta estruturada: ferramenta com saída estruturada primeiro; CLI de texto só se não houver outra
    # (achado no teste real: o planejador no Copilot `auto` devolvia JSON fora do esquema)
    text_only = {
        rid
        for rid, r in catalog.routes.items()
        if r.tool in registry and not registry[r.tool].capabilities().structured_output
    }
    try:
        for _ in range(max_attempts):
            killswitch.check(ctx)  # S-05: início de cada passo
            states = agents_app.tool_states(ctx, registry)

            def route_for(skip: set[str], states: Any = states) -> Any:
                return choose(
                    catalog,
                    RoutingRequest(
                        role,
                        facts or {},
                        profile or str(cfg.get("profile", "balanceado")),
                        data_class,
                        vendors,
                        unguarded=agents_app.unguarded(ctx),
                        author_vendors=author_vendors,
                        exclude=frozenset(skip),
                        forced_tool=forced_tool,
                        forced_model=forced_model,
                        now=time.time(),
                        blocked=killswitch.blocked_routes(ctx, catalog.routes),
                    ),
                    agents_app.availability(states, _budget(ctx)),
                )

            decision = route_for(exclude | text_only) if not forced_tool else route_for(exclude)
            if decision.route is None:
                decision = route_for(exclude)
            ctx.audit(
                "route.decision",
                {
                    "role": role,
                    "target": target_id,
                    "route": decision.route.id if decision.route else None,
                    "reason": decision.reason,
                    "rejected": decision.rejected,
                },
                ref=run_id,
            )
            if decision.route is None:
                raise NexoError(
                    f"nenhuma rota disponível para o papel {role}: "
                    + "; ".join(sorted(set(decision.rejected.values())))
                )
            route = decision.route
            routes.append(route.id)
            ui("llm", {"role": role, "route": route.id})
            text = redact(prompt + repair)
            hooks.fire(
                ctx,
                hooks.PRE_SEND,
                {"route": route.id, "role": role, "chars": len(text), "redactions": len(find(prompt))},
                run_id,
            )
            step = store.start_step(run_id, role, route.tool, route.model)
            req = AgentRequest(
                role,
                text,
                str(workdir),
                route.model,
                allowed_tools=("Read", "Glob", "Grep") if read_files else (),
                output_schema=schema,
                timeout_s=float((cfg.get("agents") or {}).get("timeout_minutes", 30)) * 60,
                env={"NX_HOME": str(ctx.home)},
                env_allow=hooks.agent_env_allow(ctx, hooks.load_policy(ctx), route.tool, run_id),
                container=isolation.spec(ctx, data_class),  # S-04
            )
            outcome = _run(registry[route.tool], req)
            ui("llm_end", {"role": role, "route": route.id, "status": outcome.status})
            store.add_usage(step, outcome.usage)
            store.end_step(step, outcome.status, outcome.error_kind, outcome.message, None, outcome.tool_calls)
            from nexo.kernel.platform import telemetry

            telemetry.after_step(ctx, step)
            if outcome.status == "rate_limited":
                store.set_route_state(
                    route.tool, outcome.reset_at or time.time() + 3600, outcome.quota_utilization, "cota esgotada"
                )
                continue
            if outcome.status != "success":
                if outcome.error_kind == ERR_AUTH:
                    exclude.update(r for r, x in catalog.routes.items() if x.tool == route.tool)
                else:
                    exclude.add(route.id)
                continue
            data = outcome.structured if outcome.structured is not None else _parse_json(outcome.text)
            problems = [e.message for e in validator.iter_errors(data)] if data is not None else ["resposta sem JSON"]
            if not problems and validate is not None and data is not None:
                problems = validate(data)
            if not problems and data is not None:
                store.update_run(run_id, state="llm:ok", ended_at=now_iso())
                ctx.audit("llm.result", {"role": role, "route": route.id, "target": target_id}, ref=run_id)
                return StructuredResult(data, run_id, routes)
            # uma tentativa de reparo por rota (com os erros no pedido), depois troca de rota
            ctx.audit("llm.invalid", {"role": role, "route": route.id, "problems": problems[:5]}, ref=run_id)
            if route.id in repaired:
                exclude.add(route.id)
            repaired.add(route.id)
            repair = (
                "\n\n## Correção necessária\nSua resposta anterior foi rejeitada pelos motivos abaixo. "
                "Responda novamente corrigindo-os:\n" + "\n".join(f"- {p}" for p in problems[:10])
            )
        raise NexoError(f"o papel {role} não produziu resposta válida após {max_attempts} tentativas")
    except Exception:
        store.update_run(run_id, state="llm:failed", ended_at=now_iso())
        raise


def _run(adapter: AgentAdapter, req: AgentRequest) -> AgentOutcome:
    outcome = AgentOutcome("error", "technical", "sem resultado")
    for event in adapter.start(req, "read-only").events():
        if event.kind == DONE:
            outcome = event.data["outcome"]
    return outcome


def _parse_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{") :]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _budget(ctx: AppContext) -> object:
    from nexo.modules.finops.app import costs

    return costs.budget_status(ctx)
