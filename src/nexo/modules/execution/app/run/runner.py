"""Ciclo de vida da tarefa: preparar, retomar, transições de estado, conclusão, escalonamento e espera por dúvidas."""

from __future__ import annotations

import time
from pathlib import Path

from nexo import __version__
from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.models.work import Task
from nexo.kernel.platform import config
from nexo.kernel.workspace import git, tasks
from nexo.modules.execution.app import agents as agents_app
from nexo.modules.execution.app import hooks
from nexo.modules.execution.app.envprep import prepare_worktree
from nexo.modules.execution.app.run import context as run_context
from nexo.modules.execution.app.run import routing as run_routing
from nexo.modules.execution.app.run import steps as run_steps
from nexo.modules.execution.app.run.common import (
    GIT_LOCK,
    UI,
    RunOptions,
    RunReport,
    _fmt,
    _noop,
    _now,
    configure_git,
    responsible,
    task_workflow,
)
from nexo.modules.execution.domain.routing.classify import Classification
from nexo.modules.knowledge.app import content
from nexo.modules.setup.app.banner import brain_version
from nexo.store.runs import RunRow, RunStore, project_key


class Orchestrator:
    def __init__(self, ctx: AppContext, opts: RunOptions, ui: UI | None = None) -> None:
        self.ctx = ctx
        self.opts = opts
        self.ui = ui or _noop
        root = ctx.project_root
        if root is None:
            raise NexoError("nenhum projeto Nexo encontrado; rode `nexo init`")
        self.root = root
        if not git.is_repo(root):
            raise NexoError(f"{root} não é um repositório git; o Nexo precisa de git para checkpoints")
        self.cfg = config.resolved(ctx).values
        configure_git(ctx, self.cfg)  # S-09: commits do Nexo assinados pela identidade de serviço
        self.store = RunStore(ctx.conn, project_key(root))
        self.workflow = task_workflow(ctx)
        self.registry = agents_app.adapters(ctx)
        self.catalog = agents_app.catalog(ctx)
        self.policy = hooks.load_policy(ctx).with_locked(opts.locked)
        self.role = opts.role
        self.policy_profile = opts.profile_policy
        self.project = str(self.cfg.get("project") or root.name)
        from nexo.modules.demands.app.tracker import Sync

        self.sync = Sync(ctx, root)
        self.profile = opts.profile or str(self.cfg.get("profile", "balanceado"))
        agents_cfg = self.cfg.get("agents") or {}
        self.timeout_s = float(agents_cfg.get("timeout_minutes", 30)) * 60
        self.max_steps = int(agents_cfg.get("max_steps", 8))
        self.max_task_tokens = float(agents_cfg.get("max_task_tokens", 3_000_000) or 0)  # 0 = sem teto
        self.max_denials = int(agents_cfg.get("max_policy_denials", 3))
        self.max_handoffs = int((self.cfg.get("handoff") or {}).get("max_per_task", 3))
        if not content.hooks_cfg(ctx)["handoff"]:  # org/hooks.yaml › handoff: false
            self.max_handoffs = 0
        from nexo.kernel.platform import flags

        if not flags.enabled(ctx, "handoff"):
            self.max_handoffs = 0
        self._simulated: set[str] = set()

    # ------------------------------------------------------------------ entrada

    def run_task(self, task_id: str) -> RunReport:
        from nexo.kernel.platform import observability

        with observability.span(self.ctx, "nexo.run.task", task=task_id, role=self.role) as sp:
            report = self._run_task(task_id)
            sp.set(state=report.state, routes=len(report.routes))
            return report

    def _run_task(self, task_id: str) -> RunReport:
        from nexo.kernel.platform import telemetry

        telemetry.check_delay(self.ctx)  # RN-101: telemetria muito atrasada bloqueia novas execuções
        self.sync.reconcile()
        task = tasks.load_task(self.root, task_id)
        state = self.store.upsert_item(task.id, "task", task.title, self.project, "local", self.workflow.initial)
        if state == "Concluido":
            raise NexoError(f"tarefa {task.id} já está concluída")
        latest = self.store.latest_run(task.id)
        if latest and state in ("EmDev", "Bloqueado", "Escalado") and not self.opts.dry_run:
            self.ui("info", {"message": f"tarefa em {state}; retomando a execução {latest.id}"})
            return self._resume(task, latest)
        states = self._session_start(task.id)
        classification = run_routing.classify_task(self, task, states)
        if self.opts.dry_run:
            decision = run_routing.choose_route(self, task, classification, states, frozenset())
            return run_routing.dry_run_report(self, task, classification, decision)
        run_id, worktree = self._prepare(task, classification)
        return run_steps.execute(self, task, run_id, worktree, classification, packet=None)

    def resume(self, target: str) -> RunReport:
        run = self.store.get_run(target) or self.store.latest_run(target)
        if run is None:
            raise NexoError(f"nenhuma execução encontrada para {target}")
        task = tasks.load_task(self.root, run.target_id)
        return self._resume(task, run)

    # ------------------------------------------------------------------ etapas

    def _session_start(self, ref: str) -> dict[str, agents_app.ToolState]:
        states = agents_app.tool_states(self.ctx, self.registry)
        summary = {
            n: {"installed": s.info.installed, "auth": s.auth.authenticated, "limited_until": s.limited_until}
            for n, s in states.items()
        }
        hooks.fire(self.ctx, hooks.SESSION_START, {"tools": summary, "brain": brain_version(self.ctx.brain_dir)}, ref)
        return states

    def _prepare(self, task: Task, c: Classification) -> tuple[str, Path]:
        base = git.resolve(self.root, self.opts.base_ref) if self.opts.base_ref else git.head(self.root)
        if base is None:
            raise NexoError("o repositório não tem commits; faça o primeiro commit antes de executar tarefas")
        run_id = self.store.create_run(
            task.id, self.project, self.ctx.actor, self.profile, brain_version(self.ctx.brain_dir)
        )
        branch = f"nexo/task/{task.id}"
        with GIT_LOCK:
            worktree = git.add_worktree(self.root, self.root / ".nexo" / "worktrees" / task.id, branch, base)
        prepare_worktree(self.ctx, self.cfg, worktree, self.ctx.brain_dir)
        run_context.write_policy(self, run_id)
        self.store.update_run(
            run_id,
            branch=branch,
            worktree=str(worktree),
            base_sha=base,
            classification={"kind": c.kind, "size": c.size, "risk_tags": list(c.risk_tags), "source": c.source},
        )
        self._transition(task.id, "start", run_id)
        self.ctx.audit(
            "run.start",
            {
                "task": task.id,
                "run": run_id,
                "branch": branch,
                "base": base,
                "nexo": __version__,
                "profile": self.profile,
            },
            ref=run_id,
        )
        return run_id, worktree

    def _transition(self, task_id: str, event: str, run_id: str | None, reason: str = "") -> str:
        current = self.store.item_state(task_id) or self.workflow.initial
        target = self.workflow.next_state(current, event)
        self.store.set_item_state(task_id, target)
        if run_id:
            self.store.update_run(run_id, state=target, reason=reason or None)
        self.ctx.audit(
            "state.transition",
            {"task": task_id, "from": current, "to": target, "event": event, "reason": reason},
            ref=task_id,
        )
        self.sync.transition(task_id, target, reason)
        self.ui("state", {"from": current, "to": target, "reason": reason})
        return target

    def _resume(self, task: Task, run: RunRow) -> RunReport:
        state = self.store.item_state(task.id) or run.state
        if state == "Concluido":
            raise NexoError(f"tarefa {task.id} já está concluída")
        if run.blocked_until and run.reason and "orçamento" in run.reason:
            from nexo.modules.finops.app import costs

            status = costs.budget_status(self.ctx)
            if any(status.tool_ok(t) for t in self.registry):
                run.blocked_until = None  # o gestor aumentou o limite
        if run.blocked_until and run.blocked_until > time.time():
            if not self.opts.wait:
                return RunReport(
                    task.id,
                    state,
                    f"aguardando cota até {_fmt(run.blocked_until)}",
                    run.id,
                    blocked_until=run.blocked_until,
                )
            self.ui("info", {"message": f"aguardando a cota até {_fmt(run.blocked_until)}"})
            time.sleep(max(0.0, run.blocked_until - time.time()) + 1)
        if not run.worktree or not Path(run.worktree).exists():
            raise NexoError(f"worktree da execução {run.id} não existe mais; rode a tarefa novamente")
        self._session_start(task.id)
        run_context.write_policy(self, run.id)
        if state != "EmDev":
            self._transition(task.id, "start", run.id, "retomada")
        self.store.update_run(run.id, blocked_until=None, ended_at=None)
        c = run.classification or {}
        classification = Classification(
            c.get("kind"), c.get("size", "M"), tuple(c.get("risk_tags", [])), c.get("source", "rules")
        )
        worktree = Path(run.worktree)
        packet = run_context.packet(self, task, run.id, worktree, run.base_sha or "", "retomada após interrupção", "—")
        self.ctx.audit("run.resume", {"task": task.id, "run": run.id, "previous_state": state}, ref=run.id)
        return run_steps.execute(self, task, run.id, worktree, classification, packet)

    # ------------------------------------------------------------------ execução

    # ------------------------------------------------------------------ finais

    def _complete(
        self, task: Task, run_id: str, worktree: Path, base: str, routes: list[str], handoffs: int
    ) -> RunReport:
        trailers = (
            f"\n\nNexo-Run: {run_id}\nNexo-Routes: {', '.join(dict.fromkeys(routes))}"
            f"\nNexo-Responsible: {responsible(self.ctx)}"
        )
        from nexo.modules.packs.app import extensions

        for result in extensions.fire(
            self.ctx, "task_done", {"task": task.id, "run": run_id, "worktree": str(worktree)}, ref=run_id
        ):
            if result["decision"] == "deny":
                return self._escalate(
                    task, run_id, f"hook {result['hook']} reprovou a entrega: {result['reason']}", routes
                )
        commit = git.squash(worktree, base, f"feat({task.id}): {task.title}{trailers}")
        self._transition(task.id, "complete", run_id)
        self.store.update_run(run_id, ended_at=_now())
        self.ctx.audit(
            "run.complete", {"task": task.id, "commit": commit, "routes": routes, "handoffs": handoffs}, ref=run_id
        )
        return RunReport(
            task.id,
            "Concluido",
            "",
            run_id,
            commit,
            f"nexo/task/{task.id}",
            str(worktree),
            routes,
            handoffs,
            usage=run_steps.usage(self, run_id),
            model_vendors=sorted({self.catalog.routes[r].model_vendor for r in routes if r in self.catalog.routes}),
        )

    def _escalate(self, task: Task, run_id: str, reason: str, routes: list[str]) -> RunReport:
        self._transition(task.id, "escalate", run_id, reason)
        self.store.update_run(run_id, ended_at=_now())
        self.ctx.audit("run.escalated", {"task": task.id, "reason": reason}, ref=run_id)
        return RunReport(task.id, "Escalado", reason, run_id, routes=routes, usage=run_steps.usage(self, run_id))

    def _await_clarification(self, task: Task, run_id: str, items: list[str], routes: list[str]) -> RunReport:
        from nexo.modules.planning.app import clarify
        from nexo.modules.planning.app.plan import story_workflow

        reason = "aguardando " + "; ".join(items) + " — responda com `nexo plan answer` e rode `nexo resume`"
        self._transition(task.id, "block", run_id, reason)
        self.store.update_run(run_id, ended_at=_now())
        folder = clarify.feature_for_story(self.root, task.story)
        if folder is not None:
            workflow = story_workflow(self.ctx)
            for item_id in (task.story, folder.name.split("-")[0] + "-" + folder.name.split("-")[1]):
                state = self.store.item_state(item_id or "")
                if item_id and state and workflow.can(state, "spec_ambiguity"):
                    target = workflow.next_state(state, "spec_ambiguity")
                    self.store.set_item_state(item_id, target)
                    self.ctx.audit(
                        "state.transition",
                        {"item": item_id, "from": state, "to": target, "event": "spec_ambiguity"},
                        ref=item_id,
                    )
                    self.sync.transition(item_id, target, "dúvida do agente durante a execução")
        self.ctx.audit("run.awaiting_clarification", {"task": task.id, "items": items}, ref=run_id)
        return RunReport(task.id, "Bloqueado", reason, run_id, routes=routes, usage=run_steps.usage(self, run_id))
