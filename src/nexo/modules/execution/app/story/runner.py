"""Estado e fluxo da história: máquina de estados, condução das fases, retomada, avisos e relatório."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from nexo.kernel import services
from nexo.kernel.backlog import Backlog, BacklogTask
from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.platform import config, identity
from nexo.kernel.runtime.resources import load_yaml
from nexo.kernel.spec.model import Spec
from nexo.kernel.workspace import git, specs
from nexo.modules.execution.app.envprep import prepare_worktree
from nexo.modules.execution.app.run import GIT_LOCK, RunOptions
from nexo.modules.execution.app.story import bugs as story_bugs
from nexo.modules.execution.app.story import cost as story_cost
from nexo.modules.execution.app.story import decisions as story_decisions
from nexo.modules.execution.app.story import publish as story_publish
from nexo.modules.execution.app.story import qa as story_qa
from nexo.modules.execution.app.story import quality as story_quality
from nexo.modules.execution.app.story import tasks as story_tasks
from nexo.modules.execution.app.story.common import UI, StoryReport, _noop, is_test_task
from nexo.store.runs import RunStore, project_key

content = services.load("knowledge.content")
feature_for_story = services.get("planning.clarify", "feature_for_story")
story_workflow = services.get("planning.plan", "story_workflow")


class StoryRunner:
    def __init__(self, ctx: AppContext, opts: RunOptions | None = None, ui: UI | None = None) -> None:
        self.ctx = ctx
        self.opts = opts or RunOptions()
        self.ui = ui or _noop
        root = ctx.project_root
        if root is None:
            raise NexoError("nenhum projeto Nexo encontrado; rode `nexo init`")
        self.root = root
        self.cfg = config.resolved(ctx).values
        from nexo.modules.execution.app.run import configure_git

        configure_git(ctx, self.cfg)
        self.store = RunStore(ctx.conn, project_key(root))
        self.workflow = story_workflow(ctx)
        self.project = str(self.cfg.get("project") or root.name)
        brain = ctx.brain_dir if ctx.brain_dir.is_dir() else None
        risk = load_yaml("risk-map.yaml", brain)
        self.risk_map = {str(k): [str(p) for p in v] for k, v in (risk.get("areas") or {}).items()}
        self.always_human = [
            str(a) for a in (self.cfg.get("gates") or {}).get("always_human", risk.get("always_human", []))
        ]
        loop = self.cfg.get("bug_loop") or {}
        self.max_attempts = int(loop.get("max_attempts", 3))
        self.max_spec_returns = int(loop.get("max_spec_returns", 2))

        Sync = services.get("demands.tracker", "Sync")

        self.sync = Sync(ctx, root)

    # ------------------------------------------------------------------ carregamento

    def _load(self, story_id: str) -> tuple[Path, Spec, Backlog, list[BacklogTask]]:
        folder = feature_for_story(self.root, story_id)
        if folder is None:
            raise NexoError(f"história {story_id} não encontrada em nenhum backlog aprovado")
        backlog = specs.load_backlog(folder)
        if backlog is None:
            raise NexoError(f"backlog da história {story_id} não encontrado")
        tasks = [t for t in backlog.tasks if t.story == story_id]
        return folder, specs.load_spec(folder), backlog, tasks

    def story_run(self, story_id: str) -> Any:
        """Última execução da história (as chamadas de revisão também geram runs com o mesmo alvo)."""
        for run in reversed(self.store.runs(story_id)):
            if self._state_file(run.id).is_file():
                return run
        return None

    def _state_file(self, run_id: str) -> Path:
        return self.root / ".nexo" / "runs" / run_id / "story.json"

    def _save(self, run_id: str, st: dict[str, Any]) -> None:
        path = self._state_file(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_state(self, run_id: str) -> dict[str, Any]:
        return dict(json.loads(self._state_file(run_id).read_text(encoding="utf-8")))

    def _transition(self, item: str, event: str, reason: str = "") -> str:
        current = self.store.item_state(item) or self.workflow.initial
        if not self.workflow.can(current, event):
            # retomada após interrupção: a transição já pode ter acontecido
            targets = {t.target for t in self.workflow.transitions if t.event == event}
            if current in targets:
                return current
        target = self.workflow.next_state(current, event)
        self.store.set_item_state(item, target)
        self.ctx.audit(
            "state.transition",
            {"item": item, "from": current, "to": target, "event": event, "reason": reason},
            ref=item,
        )
        self.sync.transition(item, target, reason)
        if item.startswith("US-"):
            from nexo.kernel.platform import telemetry

            telemetry.enqueue_story(self.ctx, item)  # retrato da história para o central
        if target == "Concluido":
            story_publish.record(self, item)  # registro da entrega vai no mesmo PR que o código
            story_publish.publish(self, item)
        self.ui("state", {"from": current, "to": target, "reason": reason, "item": item})
        return cast("str", target)

    # ------------------------------------------------------------------ entradas

    def run(self, story_id: str) -> StoryReport:
        from nexo.kernel.platform import telemetry

        telemetry.check_delay(self.ctx)
        from nexo.kernel.platform import flags, killswitch

        killswitch.check(self.ctx)
        flags.require(self.ctx, "run_story")
        missing = services.get("quality.runner", "missing_required_tools")(self._stack(), self.root, self._brain())
        if missing:
            raise NexoError(
                f"falta instalar antes de executar: {', '.join(missing)}. "
                "Sem isso, o portão não consegue validar a entrega. Nada foi gasto."
            )
        self.sync.reconcile()
        folder, spec, _, tasks = self._load(story_id)
        story = next(s for s in specs.load_backlog(folder).stories if s.id == story_id)  # type: ignore[union-attr]
        state = self.store.upsert_item(story_id, "story", story.title, self.project, f"local:{spec.id}", "Pronto")
        if state == "Concluido":
            raise NexoError(f"história {story_id} já está concluída")
        latest = self.story_run(story_id)
        if latest and state != "Pronto":
            return self.resume(story_id)
        if state != "Pronto":
            raise NexoError(f"história {story_id} está em {state}; esperado Pronto")

        ready = services.load("planning.ready")

        blocked = ready.gate(self.ctx, story_id)  # Definition of Ready antes do desenvolvimento
        if blocked:
            raise NexoError(f"{story_id}: {blocked} — complete o refinamento e rode `nexo ready {story_id}`")
        # história dependente parte do branch da dependência ainda não mergeada (run feature)
        base = git.resolve(self.root, self.opts.base_ref) if self.opts.base_ref else git.head(self.root)
        if base is None:
            raise NexoError("o repositório não tem commits")
        author = identity.current(self.ctx)  # SEC-52: quem dispara precisa de identidade verificada
        run_id = self.store.create_run(story_id, self.project, self.ctx.actor, str(self.cfg.get("profile")), None)
        branch = f"nexo/story/{story_id}"
        with GIT_LOCK:
            worktree = git.add_worktree(self.root, git.worktree_path(self.root, story_id), branch, base)
        prepare_worktree(self.ctx, self.cfg, worktree, self.ctx.brain_dir)
        self.store.update_run(run_id, branch=branch, worktree=str(worktree), base_sha=base, state="EmDev")
        dependency_only = bool(tasks) and all(t.kind == "dependency" for t in tasks)
        st = {
            "phase": "tasks" if dependency_only else "qa",
            "dependency_only": dependency_only,
            "base": base,
            "branch": branch,
            "worktree": str(worktree),
            "locked": {},
            "attempts": 0,
            "spec_returns": 0,
            "tasks": {},
            "merged": [],
            "bugs": [],
            "vendors": [],
            "qa_attempts": 0,
            "spec_stable": True,
            "author": author.id,
            "author_verified": author.verified,
            **story_cost.plan(self, story_id),
        }
        self._save(run_id, st)
        self._transition(story_id, "start")
        self.ctx.audit("story.start", {"story": story_id, "run": run_id, "tasks": [t.id for t in tasks]}, ref=story_id)
        return self._drive(story_id, run_id)

    def resume(self, story_id: str) -> StoryReport:
        run = self.story_run(story_id)
        if run is None:
            raise NexoError(f"nenhuma execução de história encontrada para {story_id}")
        state = self.store.item_state(story_id)
        if state == "Concluido":
            raise NexoError(f"história {story_id} já está concluída")
        if state == "AguardandoHumano":
            gate = self._load_state(run.id).get("cost_gate")
            if gate:
                return self._report(story_id, run.id, story_cost.gate_reason(self, story_id, gate))
            return self._report(story_id, run.id, "aguardando aprovação humana (nexo approve/reject)")
        if state in ("Bloqueado", "Escalado"):
            st = self._load_state(run.id)
            if st.get("locked_dispute"):  # a pessoa conferiu (e talvez corrigiu) o teste travado: trava de novo
                from nexo.modules.execution.app.story.common import sha256_file

                worktree = Path(st["worktree"])
                changed = [
                    f for f, h in st["locked"].items() if (worktree / f).is_file() and sha256_file(worktree / f) != h
                ]
                st["locked"] = {f: sha256_file(worktree / f) for f in st["locked"] if (worktree / f).is_file()}
                st["locked_dispute"] = False
                st["last_locked_failures"] = []
                self._save(run.id, st)
                if changed:
                    git.checkpoint(worktree, f"nexo: teste de aceite corrigido pela pessoa ({', '.join(changed)})")
                self.ctx.audit("story.locked_tests_updated", {"story": story_id, "files": changed}, ref=story_id)
            self._transition(story_id, "start", "retomada")
        elif state == "Refinamento":
            from nexo.modules.execution.app.story import clarification

            ok, missing = clarification.resumable(feature_for_story(self.root, story_id), story_id)
            if not ok:
                raise NexoError(f"{story_id} está em Refinamento: {missing}")
            self._transition(story_id, "approve_spec", "pergunta do agente respondida")
            self._transition(story_id, "start", "retomada depois da resposta")
        self.ctx.audit("story.resume", {"story": story_id, "run": run.id, "state": state}, ref=story_id)
        return self._drive(story_id, run.id)

    # ------------------------------------------------------------------ máquina de fases

    def _drive(self, story_id: str, run_id: str) -> StoryReport:
        folder, spec, _, tasks = self._load(story_id)
        st = self._load_state(run_id)
        self._current = (run_id, st)
        worktree = Path(st["worktree"])
        for _ in range(50):  # proteção contra laço
            gated = story_cost.check(self, story_id, run_id, st, tasks)
            if gated is not None:
                return gated
            phase = st["phase"]
            self.ui("phase", {"phase": phase, "story": story_id})
            if phase == "qa":
                outcome = story_qa.run_qa(self, story_id, run_id, st, spec, tasks, worktree)
                if outcome is None and self.opts.stop_after_qa:
                    self._save(run_id, st)
                    return self._report(story_id, run_id)
            elif phase == "characterize":
                outcome = story_qa.characterize(self, story_id, run_id, st, spec, tasks, worktree)
            elif phase == "tasks":
                for t in tasks:  # tarefa de teste do plano foi feita pelo QA (test-first)
                    if is_test_task(t):
                        st["tasks"][t.id] = "Concluido"
                dev_tasks = [t for t in tasks if not is_test_task(t)]
                outcome = story_tasks.run_tasks(self, story_id, run_id, st, dev_tasks, worktree)
            elif phase == "quality":
                outcome = story_quality.run_quality(self, story_id, run_id, st, spec, folder, worktree)
            elif phase == "bugfix":
                outcome = story_bugs.run_bugfix(self, story_id, run_id, st, tasks, worktree)
            else:
                return self._report(story_id, run_id)
            self._save(run_id, st)
            if outcome is not None:
                return outcome
        return self._escalate(story_id, run_id, st, "limite de fases atingido (SEC-60)")

    # ---- QA test-first

    # ---- caracterização do código existente

    # ---- tarefas em ondas paralelas

    # ---- qualidade, revisões e portão

    # ---- ciclo de bug

    # ------------------------------------------------------------------ auxiliares

    def _escalate(self, story_id: str, run_id: str, st: dict[str, Any], reason: str) -> StoryReport:
        current = self.store.item_state(story_id)
        if current and self.workflow.can(current, "escalate"):
            self._transition(story_id, "escalate", reason)
        self._save(run_id, st)
        self.ctx.audit("story.escalated", {"story": story_id, "reason": reason}, ref=story_id)
        self._notify(story_id, "história escalada", reason)
        return self._report(story_id, run_id, reason)

    def _notify(self, story_id: str, title: str, message: str, link_override: str | None = None) -> None:

        notify = services.get("notify.main", "notify")

        if not content.hook_enabled(self.ctx, "story_notify"):  # org/hooks.yaml
            return

        link = self.sync.ref(story_id)
        url = link_override or (link["url"] if link else None)
        notify(self.ctx, "story", f"{story_id}: {title}", message, ref=story_id, link=url)
        self.sync.comment(story_id, f"{title}: {message}")

    def _story_acs(self, story_id: str, spec: Spec) -> list[str]:
        folder = feature_for_story(self.root, story_id)
        backlog = specs.load_backlog(folder) if folder else None
        story = next((s for s in backlog.stories if s.id == story_id), None) if backlog else None
        return [a.id for a in spec.acceptance if story and a.id in story.ac_refs]

    def _stack(self) -> str:
        stacks = self.cfg.get("stacks") or ["python"]
        return str(stacks[0])

    def _brain(self) -> Path | None:
        return self.ctx.brain_dir if self.ctx.brain_dir.is_dir() else None

    def _out(self, run_id: str, name: str) -> Path:
        return self.root / ".nexo" / "runs" / run_id / name

    def _history(self) -> float:

        history_success = services.get("finops.metrics", "history_success")

        return cast("float", history_success(self.ctx, self._stack()))

    # ------------------------------------------------------------------ custo ao vivo e teto

    # ------------------------------------------------------------------ aprovação externa

    def _report(self, story_id: str, run_id: str, reason: str = "") -> StoryReport:
        current = getattr(self, "_current", None)
        if current and current[0] == run_id:
            self._save(run_id, current[1])
        st = self._load_state(run_id)
        quality_path = self.root / ".nexo" / "runs" / run_id / "quality-report.json"
        quality = []
        if quality_path.is_file():
            data = json.loads(quality_path.read_text(encoding="utf-8"))
            quality = [{"id": s["id"], "status": s["status"], "summary": s["summary"]} for s in data["stages"]]
        return StoryReport(
            story=story_id,
            state=self.store.item_state(story_id) or "?",
            reason=reason,
            run_id=run_id,
            branch=st.get("branch"),
            worktree=st.get("worktree"),
            tasks=dict(st.get("tasks") or {}),
            quality=quality,
            gate=st.get("gate"),
            bugs=list(st.get("bugs") or []),
            attempts=int(st.get("attempts", 0)),
            cost_brl=story_cost.story_cost(self, story_id)[0] if "cap_brl" in st else None,
            cap_brl=st.get("cap_brl"),
            projection_brl=st.get("projection_brl"),
            estimate_brl=st.get("estimate_brl"),
            cost_gate=st.get("cost_gate"),
        )

    def approve(
        self,
        story_id: str,
        gate: str = "code",
        raise_pct: float | None = None,
        economic: bool = False,
        approver: identity.Identity | None = None,
    ) -> StoryReport:
        return story_decisions.approve(self, story_id, gate, raise_pct, economic, approver)

    def reject(self, story_id: str, reason: str, approver: identity.Identity | None = None) -> StoryReport:
        return story_decisions.reject(self, story_id, reason, approver)

    def pr_feedback(self, story_id: str, source: str, failures: list[str], actor: str) -> StoryReport:
        """CI vermelho ou pedido de mudança no PR ainda aberto → bug e ciclo de correção automático"""
        return story_decisions.pr_feedback(self, story_id, source, failures, actor)

    def apply_external(self, story_id: str, request_id: str, decision: str, by: str) -> StoryReport:
        """Aplica o sinal externo (`continue`/`stop`) ao portão que o pediu."""
        return story_decisions.apply_external(self, story_id, request_id, decision, by)
