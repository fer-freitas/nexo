"""Classificação da tarefa e escolha da rota (ferramenta × modelo), inclusive sem rota disponível e simulação."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from nexo.kernel.finops.money import brl
from nexo.kernel.models.agent import AgentRequest
from nexo.kernel.models.work import Task
from nexo.kernel.security.redaction import find, redact
from nexo.modules.execution.app import agents as agents_app
from nexo.modules.execution.app import hooks
from nexo.modules.execution.app.run import context as run_context
from nexo.modules.execution.app.run import steps as run_steps
from nexo.modules.execution.app.run.common import CLASSIFIER_SCHEMA, RunReport, _fmt, _now
from nexo.modules.execution.domain.routing.classify import Classification, classify
from nexo.modules.execution.domain.routing.router import RouteDecision, RoutingRequest, choose
from nexo.modules.knowledge.app import content

if TYPE_CHECKING:
    from nexo.modules.execution.app.run.runner import Orchestrator


def classify_task(o: Orchestrator, task: Task, states: dict[str, agents_app.ToolState]) -> Classification:
    result = classify(task)
    if result.resolved or o.opts.dry_run:
        return result if result.resolved else Classification("backend", result.size, result.risk_tags, "default")
    llm = llm_classify(o, task, states)
    return llm or Classification("backend", result.size, result.risk_tags, "default")


def llm_classify(o: Orchestrator, task: Task, states: dict[str, agents_app.ToolState]) -> Classification | None:
    from nexo.kernel.platform import killswitch

    decision = choose(
        o.catalog,
        RoutingRequest(
            "classifier",
            {"size": "S"},
            "economico",
            now=time.time(),
            allowed_vendors=allowed_vendors(o, task),
            unguarded=agents_app.unguarded(o.ctx),
            blocked=killswitch.blocked_routes(o.ctx, o.catalog.routes),
        ),
        agents_app.availability(states),
    )
    if decision.route is None:
        return None
    activity = f"{task.title}\n{task.description}\n" + "\n".join(task.acceptance)
    prompt = redact(content.prompt(o.ctx, "classifier", task.id).format(activity=activity))
    req = AgentRequest(
        "classifier",
        prompt,
        str(o.root),
        decision.route.model,
        allowed_tools=(),
        output_schema=CLASSIFIER_SCHEMA,
        timeout_s=120,
        env_allow=hooks.agent_env_allow(o.ctx, o.policy, decision.route.tool, None),
    )
    outcome = run_steps.consume(o, o.registry[decision.route.tool], req, "read-only", None, None)
    data = outcome.structured or {}
    if outcome.status != "success" or "kind" not in data:
        return None
    base = classify(task)
    tags = tuple(dict.fromkeys([*base.risk_tags, *[str(t) for t in data.get("risk_tags", [])]]))
    o.ctx.audit("route.classified", {"task": task.id, "by": decision.route.id, **data})
    return Classification(str(data["kind"]), str(data.get("size", base.size)), tags, "llm")


def allowed_vendors(o: Orchestrator, task: Task) -> tuple[str, ...] | None:
    data_class = task.data_class or str(o.cfg.get("data_class", "confidential"))
    allowed = (o.cfg.get("data_policy") or {}).get(data_class)
    if allowed is None or "*" in allowed:
        return None
    return tuple(str(v) for v in allowed)


def facts(o: Orchestrator, task: Task, c: Classification) -> dict[str, Any]:
    stacks = o.cfg.get("stacks") or []
    return {
        "kind": c.kind,
        "size": c.size,
        "stack": task.stack or (stacks[0] if stacks else None),
        "risk_tags": list(c.risk_tags),
    }


def choose_route(
    o: Orchestrator, task: Task, c: Classification, states: dict[str, agents_app.ToolState], exclude: frozenset[str]
) -> RouteDecision:
    from nexo.kernel.platform import killswitch

    killswitch.check(o.ctx)  # S-05: checado no início de cada passo
    req = RoutingRequest(
        role=o.role,
        facts=facts(o, task, c),
        profile=o.profile,
        data_class=task.data_class or str(o.cfg.get("data_class", "confidential")),
        allowed_vendors=allowed_vendors(o, task),
        unguarded=agents_app.unguarded(o.ctx),
        exclude=exclude,
        forced_tool=o.opts.tool,
        forced_model=o.opts.model,
        now=time.time(),
        observed=observed(o, c.kind),
        blocked=killswitch.blocked_routes(o.ctx, o.catalog.routes),
    )
    from nexo.modules.finops.app import costs

    costs.sync_limits(o.ctx)  # o limite pode ter mudado fora do Nexo
    budget = costs.budget_status(o.ctx)
    decision = choose(o.catalog, req, agents_app.availability(states, budget))
    o.ctx.audit(
        "route.decision",
        {
            "task": task.id,
            "role": o.role,
            "route": decision.route.id if decision.route else None,
            "reason": decision.reason,
            "rejected": decision.rejected,
            "forced": bool(o.opts.tool or o.opts.model),
        },
        ref=task.id,
    )
    return decision


def observed(o: Orchestrator, kind: str | None) -> dict[str, tuple[int, int]]:
    from nexo.modules.knowledge.app.learning import observed_for

    return observed_for(o.ctx, kind)


def no_route(o: Orchestrator, task: Task, run_id: str, decision: RouteDecision, routes: list[str]) -> RunReport:
    reasons = set(decision.rejected.values())
    if "orçamento esgotado" in reasons and not decision.earliest_reset:
        from nexo.modules.finops.app import costs
        from nexo.modules.notify.app import notify

        until = costs.next_month_epoch()
        reason = "orçamento esgotado em todas as rotas — retoma quando o gestor aumentar o limite ou o mês virar"
        from nexo.modules.delivery.app import approval

        if approval.mode(o.ctx, "budget") == "external":
            pending = approval.find_pending(o.ctx, "budget", dev=o.ctx.actor)
            status = costs.budget_status(o.ctx)
            request_id = pending or approval.request(
                o.ctx,
                "budget",
                task.id,
                f"orçamento de {o.ctx.actor} esgotado: {brl(status.spent_brl)} de {brl(status.limit_brl or 0)}",
                {"spent_brl": status.spent_brl, "limit_brl": status.limit_brl},
            )
            reason = f"orçamento esgotado — aguardando aprovação externa {request_id} ou novo limite"
        o._transition(task.id, "block", run_id, reason)
        o.store.update_run(run_id, blocked_until=until, ended_at=_now())
        o.ctx.audit("run.blocked", {"task": task.id, "kind": "budget", "reason": "budget", "until": until}, ref=run_id)
        notify(o.ctx, "budget", f"{task.id} bloqueada por orçamento", reason, ref=task.id)
        return RunReport(
            task.id, "Bloqueado", reason, run_id, routes=routes, blocked_until=until, usage=run_steps.usage(o, run_id)
        )
    if decision.earliest_reset:
        reason = f"todas as rotas sem cota; retomada prevista para {_fmt(decision.earliest_reset)}"
        o._transition(task.id, "block", run_id, reason)
        o.store.update_run(run_id, blocked_until=decision.earliest_reset, ended_at=_now())
        o.ctx.audit(
            "run.blocked",
            {"task": task.id, "kind": "quota", "reason": reason, "until": decision.earliest_reset},
            ref=run_id,
        )
        return RunReport(
            task.id,
            "Bloqueado",
            reason,
            run_id,
            routes=routes,
            blocked_until=decision.earliest_reset,
            usage=run_steps.usage(o, run_id),
        )
    allowed = allowed_vendors(o, task)
    if allowed is not None and not any(r.vendors <= set(allowed) for r in o.catalog.routes.values()):
        return o._escalate(task, run_id, "classe de dados não permite nenhuma rota — execução humana", routes)
    reason = "nenhuma rota disponível: " + "; ".join(sorted(reasons))
    o._transition(task.id, "block", run_id, reason)
    o.store.update_run(run_id, ended_at=_now())
    return RunReport(task.id, "Bloqueado", reason, run_id, routes=routes, usage=run_steps.usage(o, run_id))


def dry_run_report(o: Orchestrator, task: Task, c: Classification, d: RouteDecision) -> RunReport:
    prompt = run_context.prompt(o, task, None)
    return RunReport(
        task.id,
        o.store.item_state(task.id) or o.workflow.initial,
        "dry-run",
        classification={"kind": c.kind, "size": c.size, "risk_tags": list(c.risk_tags), "source": c.source},
        decision={
            "route": d.route.id if d.route else None,
            "reason": d.reason,
            "human_gate": d.human_gate,
            "candidates": [
                {
                    "route": x.route.id,
                    "p_success": round(x.p_success, 3),
                    "expected_cost": round(x.expected_cost, 2),
                    "score": round(x.score, 4),
                }
                for x in d.candidates
            ],
            "rejected": d.rejected,
            "prompt_chars": len(prompt),
            "redactions": len(find(prompt)),
        },
    )
