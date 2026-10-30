"""Passos do agente.

laço de execução com troca de rota, chamada à ferramenta, consumo dos eventos com a política e verificações depois
do passo.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nexo.kernel.finops import tokens
from nexo.kernel.models.agent import (
    CLARIFICATION,
    DONE,
    ERR_AUTH,
    ERR_CONTEXT,
    ERR_MODEL,
    ERR_POLICY,
    FILE_CHANGE,
    MESSAGE,
    PROGRESS,
    QUOTA,
    TOOL_CALL,
    AgentOutcome,
    AgentRequest,
)
from nexo.kernel.models.work import Task
from nexo.kernel.security.policy import evaluate, normalize
from nexo.kernel.security.redaction import find, redact
from nexo.kernel.workflow.handoff import HandoffPacket, ProgressEntry
from nexo.kernel.workspace import git
from nexo.modules.execution.adapters.agents.base import AgentAdapter
from nexo.modules.execution.app import agents as agents_app
from nexo.modules.execution.app import hooks
from nexo.modules.execution.app.run import context as run_context
from nexo.modules.execution.app.run import routing as run_routing
from nexo.modules.execution.app.run.common import RunReport, _fmt, _now
from nexo.modules.execution.domain.routing.classify import Classification

if TYPE_CHECKING:
    from nexo.modules.execution.app.run.runner import Orchestrator


def execute(
    o: Orchestrator, task: Task, run_id: str, worktree: Path, c: Classification, packet: HandoffPacket | None
) -> RunReport:
    exclude: set[str] = {r for r, x in o.catalog.routes.items() if x.tool in o.opts.exclude_tools}
    failures: dict[str, int] = {}
    routes: list[str] = []
    handoffs = len(o.store.handoffs(run_id))
    base = o.store.get_run(run_id).base_sha or ""  # type: ignore[union-attr]
    from nexo.modules.packs.app import extensions

    for result in extensions.fire(
        o.ctx, "session_start", {"task": task.id, "run": run_id, "worktree": str(worktree)}, ref=run_id
    ):
        if result["decision"] == "deny":
            return o._escalate(task, run_id, f"hook {result['hook']} negou o início: {result['reason']}", routes)
    steps = 0
    while True:
        steps += 1
        if steps > o.max_steps:
            return o._escalate(task, run_id, f"limite de {o.max_steps} passos atingido (SEC-60)", routes)
        used = run_context.run_tokens(o, run_id)
        if tokens.over_cap(used, o.max_task_tokens):
            o.ctx.audit(
                "run.token_cap", {"task": task.id, "used": int(used), "cap": int(o.max_task_tokens)}, ref=run_id
            )
            return o._escalate(
                task, run_id, f"teto de {int(o.max_task_tokens)} tokens da tarefa atingido ({int(used)})", routes
            )
        states = agents_app.tool_states(o.ctx, o.registry)
        decision = run_routing.choose_route(o, task, c, states, frozenset(exclude))
        if decision.route is None:
            return run_routing.no_route(o, task, run_id, decision, routes)
        route = decision.route
        routes.append(route.id)
        o.ui("route", {"route": route.id, "reason": decision.reason, "step": steps})
        prompt = run_context.prompt(o, task, packet)
        if o.opts.extra_prompt:
            prompt += "\n\n## Padrões a seguir nesta tarefa\n" + o.opts.extra_prompt
        if route.tool != "claude":
            prompt += run_context.team_skills(o, route.tool, task)
        redactions = len(find(prompt))
        prompt = redact(prompt)
        hooks.fire(o.ctx, hooks.PRE_SEND, {"route": route.id, "chars": len(prompt), "redactions": redactions}, run_id)
        step_id = o.store.start_step(run_id, o.role, route.tool, route.model)
        adapter = o.registry[route.tool]
        from nexo.kernel.platform import observability

        try:
            with observability.span(
                o.ctx,
                "nexo.agent.step",
                tool=route.tool,
                model=route.model,
                role=o.role,
                step=step_id,
                run=run_id,
            ) as step_span:
                outcome = run_step(o, task, run_id, step_id, worktree, adapter, route.tool, route.model, prompt)
                step_span.set(
                    outcome=outcome.status,
                    error_kind=outcome.error_kind,
                    tool_calls=outcome.tool_calls,
                    files=len(outcome.files_changed),
                )
                if outcome.status not in ("success",):
                    step_span.error(outcome.status)
        except KeyboardInterrupt:
            sha = git.checkpoint(worktree, f"nexo: checkpoint (interrompido) {step_id}")
            o.store.end_step(step_id, "interrupted", None, "interrompido pelo usuário", sha)
            o._transition(task.id, "block", run_id, "interrompido pelo usuário")
            o.store.update_run(run_id, ended_at=_now())
            return RunReport(
                task.id,
                "Bloqueado",
                "interrompido pelo usuário — use `nexo resume`",
                run_id,
                branch=f"nexo/task/{task.id}",
                worktree=str(worktree),
                routes=routes,
            )
        leak = pre_commit_scan(o, worktree, base)
        if leak:
            o.store.end_step(step_id, "policy_violation", ERR_POLICY, leak, None)
            o.ctx.audit("policy.violation", {"rule": "SEC-12", "reason": leak, "hook": "pre_commit"}, ref=run_id)
            return o._escalate(task, run_id, leak, routes)
        sha = git.checkpoint(worktree, f"nexo: checkpoint {step_id}")
        o.store.add_usage(step_id, outcome.usage)
        from nexo.modules.finops.app import costs

        costs.check_alerts(o.ctx)
        o.store.end_step(step_id, outcome.status, outcome.error_kind, outcome.message, sha, outcome.tool_calls)
        from nexo.kernel.platform import telemetry

        telemetry.after_step(o.ctx, step_id)  # fato de consumo + envio em segundo plano
        extensions.fire(
            o.ctx,
            "post_step",
            {"task": task.id, "run": run_id, "checkpoint": sha, "files": outcome.files_changed},
            ref=run_id,
        )
        hooks.fire(
            o.ctx,
            hooks.POST_STEP,
            {
                "step": step_id,
                "route": route.id,
                "status": outcome.status,
                "checkpoint": sha,
                "files": outcome.files_changed,
            },
            run_id,
        )
        o.ui("step_end", {"status": outcome.status, "message": outcome.message, "checkpoint": sha})

        violation = post_step_violation(o, worktree, base)
        if violation or outcome.status == "policy_violation":
            return o._escalate(task, run_id, violation or outcome.message, routes)
        if hooks.denial_count(o.ctx, run_id) >= o.max_denials:
            return o._escalate(task, run_id, f"{o.max_denials} ações negadas pela política", routes)

        if outcome.status == "success":
            progress = o.store.progress(run_id)
            last = progress[-1] if progress else ProgressEntry()
            clarifications = [b for b in last.blockers if b.startswith("esclarecimento")]
            if clarifications and (last.percent or 0) < 100:
                return o._await_clarification(task, run_id, clarifications, routes)
            if last.blockers and (last.percent or 0) < 100:
                return o._escalate(task, run_id, "agente registrou bloqueio: " + "; ".join(last.blockers), routes)
            if not git.changed_files(worktree, base):
                outcome = AgentOutcome("error", "technical", "agente terminou sem alterações")
            else:
                return o._complete(task, run_id, worktree, base, routes, handoffs)

        reason = failure_reason(o, outcome)
        if outcome.status == "rate_limited":
            reset_at = outcome.reset_at or time.time() + 3600
            if outcome.error_kind == "simulated":
                # simulação vale só para esta execução: não altera o estado global da cota
                exclude.update(r for r, x in o.catalog.routes.items() if x.tool == route.tool)
            else:
                o.store.set_route_state(route.tool, reset_at, outcome.quota_utilization, "cota esgotada")
            hooks.fire(
                o.ctx,
                hooks.ON_LIMIT,
                {"tool": route.tool, "reset_at": reset_at, "simulated": outcome.error_kind == "simulated"},
                run_id,
            )
        elif outcome.error_kind == ERR_AUTH:
            exclude.update(r for r, x in o.catalog.routes.items() if x.tool == route.tool)
        elif outcome.error_kind == ERR_CONTEXT:
            exclude.add(route.id)
        elif outcome.error_kind == ERR_MODEL:  # o plano não libera o modelo: tentar de novo não adianta
            exclude.add(route.id)
            o.ctx.audit(
                "route.model_unavailable", {"route": route.id, "tool": route.tool, "model": route.model}, ref=run_id
            )
        else:
            failures[route.id] = failures.get(route.id, 0) + 1
            if failures[route.id] >= 2:
                exclude.add(route.id)
        switching = outcome.status == "rate_limited" or route.id in exclude
        packet = run_context.packet(o, task, run_id, worktree, base, reason, route.id)
        if switching:
            handoffs += 1
            if handoffs > o.max_handoffs:
                return o._escalate(task, run_id, f"limite de {o.max_handoffs} handoffs atingido", routes)
            path = run_context.write_packet(o, run_id, handoffs, packet)
            o.store.add_handoff(run_id, route.id, None, reason, str(path))
            o.ctx.audit(
                "run.handoff",
                {
                    "task": task.id,
                    "from": route.id,
                    "tool": route.tool,
                    "kind": "quota" if outcome.status == "rate_limited" else (outcome.error_kind or "error"),
                    "reason": reason,
                    "packet": str(path),
                    "checkpoint": sha,
                },
                ref=run_id,
            )
            o.ui("handoff", {"from": route.id, "reason": reason})
        else:
            o.ui("retry", {"route": route.id, "reason": reason})


def run_step(
    o: Orchestrator,
    task: Task,
    run_id: str,
    step_id: str,
    worktree: Path,
    adapter: AgentAdapter,
    tool: str,
    model: str,
    prompt: str,
) -> AgentOutcome:
    if tool in o.opts.simulate_rate_limit and tool not in o._simulated:
        o._simulated.add(tool)
        o.ui("info", {"message": f"simulando cota esgotada em {tool}"})
        return AgentOutcome("rate_limited", "simulated", "cota esgotada (simulada)", reset_at=time.time() + 3600)
    from nexo.kernel.platform import isolation

    data_class = task.data_class or str(o.cfg.get("data_class", "confidential"))
    container = isolation.spec(o.ctx, data_class)  # S-04: exigido pela org e desligado → erro
    native = adapter.capabilities().native_hooks and container is None  # hooks do host não entram no contêiner
    extra_mcp, mcp_env = external_mcp(o, task, adapter) if container is None else ({}, {})
    req = AgentRequest(
        role=o.role,
        prompt=prompt,
        workdir=str(worktree),
        model=model,
        timeout_s=o.timeout_s,
        mcp_config=agents_app.mcp_config(o.ctx, run_id, o.role, extra_mcp) if container is None else None,
        settings=agents_app.hook_settings(o.ctx, run_id) if native else None,
        plugin_dirs=(run_context.skills_plugin(o),) if tool == "claude" and native else (),
        container=container,
        data_class=data_class,
        env={"NX_HOME": str(o.ctx.home), "NX_RUN_ID": run_id, "NX_TASK_ID": task.id, **mcp_env},
        env_allow=hooks.agent_env_allow(o.ctx, o.policy, tool, run_id),
    )
    return consume(o, adapter, req, o.policy_profile, run_id, step_id)


def external_mcp(o: Orchestrator, task: Task, adapter: AgentAdapter) -> tuple[dict[str, Any], dict[str, str]]:
    """MCPs oficiais ligados e liberados para a classe de dados da tarefa (só ferramentas com MCP)."""
    if not adapter.capabilities().mcp:
        return {}, {}
    from nexo.modules.mcp.app import catalog as mcp_catalog

    servers, env, skipped = mcp_catalog.servers_for_run(o.ctx, run_routing.allowed_vendors(o, task))
    for server, reason in skipped.items():
        o.ui("info", {"message": f"MCP {server} fora desta execução: {reason}"})
    return servers, env


def consume(
    o: Orchestrator, adapter: AgentAdapter, req: AgentRequest, profile: str, run_id: str | None, step_id: str | None
) -> AgentOutcome:
    session = adapter.start(req, profile)
    native = adapter.capabilities().native_hooks and req.container is None  # no contêiner: checagem pelo Nexo
    outcome = AgentOutcome("error", "technical", "sessão terminou sem resultado")
    from nexo.kernel.platform import observability

    try:
        for event in session.events():
            observability.record(o.ctx, run_id, step_id, event.kind, event.data)  # O-02
            if event.kind == TOOL_CALL:
                observability.event(o.ctx, "tool_call", tool=str(event.data.get("name")))
            if event.kind == TOOL_CALL and run_id:
                action = normalize(str(event.data.get("name")), dict(event.data.get("input") or {}), o.policy)
                decision = evaluate(action, req.workdir, o.policy, profile)
                if decision.allow and not native:
                    from nexo.modules.packs.app import extensions

                    denied = [
                        r
                        for r in extensions.fire(
                            o.ctx,
                            "pre_tool",
                            {
                                "tool": action.tool,
                                "input": dict(event.data.get("input") or {}),
                                "worktree": req.workdir,
                            },
                            tool=adapter.name,
                            ref=run_id,
                        )
                        if r["decision"] == "deny"
                    ]
                    if denied:
                        session.terminate()
                        return AgentOutcome(
                            "policy_violation", ERR_POLICY, f"hook {denied[0]['hook']}: {denied[0]['reason']}"
                        )
                if not decision.allow and not native:
                    hooks.check_action(
                        o.ctx,
                        o.policy,
                        run_id,
                        req.workdir,
                        action.tool,
                        dict(event.data.get("input") or {}),
                        profile,
                    )
                    session.terminate()
                    outcome = AgentOutcome("policy_violation", ERR_POLICY, f"{decision.rule}: {decision.reason}")
                    return outcome
            elif event.kind == CLARIFICATION and run_id:
                from nexo.modules.planning.app import clarify

                clarify.register(
                    o.ctx,
                    run_id,
                    str(event.data.get("question", "")),
                    str(event.data.get("section", "")),
                    bool(event.data.get("blocking", True)),
                )
            elif event.kind == PROGRESS and run_id:
                entry = ProgressEntry(
                    **{k: event.data[k] for k in ProgressEntry.__dataclass_fields__ if k in event.data}
                )
                o.store.add_progress(run_id, step_id, entry)
                o.ctx.audit("agent.progress", entry.__dict__, ref=run_id)
            elif event.kind == QUOTA:
                o.ui("quota", event.data)
            elif event.kind == FILE_CHANGE:
                o.ui("file", event.data)
            elif event.kind == MESSAGE:
                o.ui("message", event.data)
            elif event.kind == DONE:
                outcome = event.data["outcome"]
    except KeyboardInterrupt:
        session.terminate()
        raise
    return outcome


def pre_commit_scan(o: Orchestrator, worktree: Path, base: str) -> str | None:
    """H6: nenhum segredo/PII entra em commit (gitleaks quando disponível + redação determinística)."""
    found: list[str] = []
    for rel in git.changed_files(worktree, base):
        path = worktree / rel
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        kinds = {f.kind for f in find(path.read_text(encoding="utf-8", errors="ignore"))}
        kinds -= {"email", "phone_br"}  # contatos em código/testes não bloqueiam; segredos e documentos sim
        if kinds:
            found.append(f"{rel} ({', '.join(sorted(kinds))})")
    hooks.fire(o.ctx, "pre_commit", {"files": len(git.changed_files(worktree, base)), "leaks": len(found)})
    return f"segredo ou dado sensível no código: {'; '.join(found)}" if found else None


def post_step_violation(o: Orchestrator, worktree: Path, base: str) -> str | None:
    """Defesa em profundidade: nenhuma rota pode alterar caminho proibido, mesmo sem hook nativo."""
    for rel in git.changed_files(worktree, base):
        action = normalize("Write", {"file_path": rel}, o.policy)
        decision = evaluate(action, str(worktree), o.policy, o.policy_profile)
        if not decision.allow:
            return f"alteração em caminho não permitido: {rel} ({decision.rule})"
    return None


def failure_reason(o: Orchestrator, outcome: AgentOutcome) -> str:
    if outcome.status == "rate_limited":
        when = f" até {_fmt(outcome.reset_at)}" if outcome.reset_at else ""
        return f"cota esgotada{when}"
    if outcome.status == "timeout":
        return "tempo limite excedido"
    return f"{outcome.error_kind or outcome.status}: {outcome.message[:200]}"


def usage(o: Orchestrator, run_id: str) -> list[dict[str, Any]]:
    return [dict(r) for r in o.store.usage_for_run(run_id)]
