"""Fase de tarefas: execução em paralelo isolada por worktree, merge, rebase e registro das tarefas."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nexo.kernel.backlog import Backlog, BacklogTask, waves
from nexo.kernel.context import AppContext
from nexo.kernel.runtime import yamlfile
from nexo.kernel.spec.model import Spec
from nexo.kernel.workspace import git, specs
from nexo.kernel.workspace import tasks as workspace_tasks
from nexo.modules.execution.app.run import Orchestrator, RunOptions, RunReport
from nexo.modules.execution.app.story import cost as story_cost
from nexo.modules.execution.app.story.common import StoryReport
from nexo.modules.knowledge.app import content
from nexo.modules.planning.app.clarify import feature_for_story

if TYPE_CHECKING:
    from nexo.modules.execution.app.story.runner import StoryRunner


def run_tasks(
    r: StoryRunner, story_id: str, run_id: str, st: dict[str, Any], tasks: list[BacklogTask], worktree: Path
) -> StoryReport | None:
    pending_backlog = Backlog(feature="x", stories=[], tasks=tasks)
    max_parallel = int(r.cfg.get("max_parallel", 3))
    for wave in waves(pending_backlog):
        todo = [t for t in wave if st["tasks"].get(t) != "Concluido" or t not in st["merged"]]
        to_run = [t for t in todo if st["tasks"].get(t) != "Concluido"]
        if to_run:
            r.ui("wave", {"tasks": to_run})
            with ThreadPoolExecutor(max_workers=max(1, min(max_parallel, len(to_run)))) as pool:
                results = dict(zip(to_run, pool.map(lambda tid: run_isolated(r, tid, st), to_run), strict=True))
            for tid, rep in results.items():
                st["tasks"][tid] = rep.state
                st["vendors"] = sorted(set(st["vendors"]) | set(rep.model_vendors))
            r._save(run_id, st)
            for tid, rep in results.items():
                if rep.state != "Concluido":
                    return problem(r, story_id, run_id, st, tid, rep)
            gated = story_cost.check(r, story_id, run_id, st, tasks)
            if gated is not None:
                return gated
        merge_problem = merge(r, story_id, run_id, st, worktree, [t for t in wave if t not in st["merged"]])
        if merge_problem is not None:
            return merge_problem
    r._transition(story_id, "implemented")
    st["phase"] = "quality"
    return None


def run_isolated(r: StoryRunner, task_id: str, st: dict[str, Any]) -> RunReport:
    """Cada tarefa paralela usa seu próprio contexto (conexão SQLite própria)."""
    ctx = AppContext(cwd=r.root, home=r.ctx.home, options=r.ctx.options)
    try:
        opts = RunOptions(
            profile=st.get("profile_override") or r.opts.profile,
            tool=r.opts.tool,
            model=r.opts.model,
            base_ref=st["branch"],
            locked=frozenset(st["locked"]),
        )

        def ui(kind: str, data: dict[str, Any]) -> None:
            r.ui(kind, {**data, "task": task_id})

        orch = Orchestrator(ctx, opts, ui)
        state = orch.store.item_state(task_id)
        return orch.resume(task_id) if state in ("EmDev", "Bloqueado", "Escalado") else orch.run_task(task_id)
    finally:
        ctx.close()


def run_one(
    r: StoryRunner,
    task_id: str,
    st: dict[str, Any],
    role: str = "developer",
    policy: str = "code-writer",
    profile: str | None = None,
    exclude_tools: frozenset[str] = frozenset(),
) -> RunReport:
    opts = RunOptions(
        profile=profile or st.get("profile_override") or r.opts.profile,
        tool=r.opts.tool,
        model=r.opts.model,
        role=role,
        profile_policy=policy,
        base_ref=st["branch"],
        locked=frozenset(st["locked"]) if role != "qa-author" else frozenset(),
        exclude_tools=exclude_tools,
    )
    orch = Orchestrator(r.ctx, opts, r.ui)
    state = orch.store.item_state(task_id)
    rep = orch.resume(task_id) if state in ("EmDev", "Bloqueado", "Escalado") else orch.run_task(task_id)
    st["vendors"] = sorted(set(st["vendors"]) | set(rep.model_vendors))
    return rep


def merge(
    r: StoryRunner, story_id: str, run_id: str, st: dict[str, Any], worktree: Path, task_ids: list[str]
) -> StoryReport | None:
    for tid in task_ids:
        if tid in st["merged"]:
            continue
        # a worktree da história é exclusiva do Nexo: artefatos do pipeline não podem bloquear o merge
        git.discard_local_changes(worktree)
        conflicts = git.merge(worktree, f"nexo/task/{tid}", f"merge({story_id}): {tid}")
        if conflicts:
            git.abort_merge(worktree)
            r.ctx.audit("story.conflict", {"story": story_id, "task": tid, "files": conflicts}, ref=story_id)
            rework = rebase(r, story_id, st, tid, conflicts)
            if rework is None:
                return r._escalate(story_id, run_id, st, f"conflito não resolvido em {tid}: {', '.join(conflicts)}")
            if git.merge(worktree, f"nexo/task/{rework}", f"merge({story_id}): {rework}"):
                git.abort_merge(worktree)
                return r._escalate(story_id, run_id, st, f"conflito persistente em {tid}")
            st["merged"].append(rework)
        st["merged"].append(tid)
        r._save(run_id, st)
    return None


def rebase(r: StoryRunner, story_id: str, st: dict[str, Any], task_id: str, conflicts: list[str]) -> str | None:
    """RN-32: reaplica a tarefa em conflito sobre a história já integrada (papel integrator)."""
    original = workspace_tasks.load_task(r.root, task_id)
    task_diff = git.git(["diff", f"{st['branch']}...nexo/task/{task_id}"], r.root, check=False)[:12000]
    rid = f"{task_id}-INT"
    desc = (
        content.prompt(r.ctx, "integrator", story_id).format(
            story=story_id,
            conflicts="\n".join(f"- {c}" for c in conflicts),
            tasks=f"- {task_id}: {original.title}\n{original.description}",
        )
        + f"\n\n## Mudança original da tarefa (reaplique sobre o código atual)\n```diff\n{task_diff}\n```\n"
    )
    spec = specs.load_spec(feature_for_story(r.root, story_id))  # type: ignore[arg-type]
    write(
        r,
        rid,
        f"Integrar {task_id}",
        original.kind or "integration",
        desc,
        original.acceptance,
        original.files_hint,
        story_id,
        spec,
    )
    rep = run_one(r, rid, st, role="integrator")
    return rid if rep.state == "Concluido" else None


def write(
    r: StoryRunner,
    task_id: str,
    title: str,
    kind: str,
    description: str,
    acceptance: list[str],
    files: list[str],
    story_id: str,
    spec: Spec,
) -> None:
    stacks = r.cfg.get("stacks") or []
    data: dict[str, Any] = {
        "title": title,
        "kind": kind,
        "description": description,
        "acceptance": acceptance,
        "files_hint": files[:8],
        "story": story_id,
    }
    if len(stacks) == 1:
        data["stack"] = stacks[0]
    if spec.data_class:
        data["data_class"] = spec.data_class
    yamlfile.dump(workspace_tasks.tasks_dir(r.root) / f"{task_id}.yaml", data)


def problem(
    r: StoryRunner, story_id: str, run_id: str, st: dict[str, Any], task_id: str, rep: RunReport
) -> StoryReport:
    if rep.state == "Bloqueado":
        current = r.store.item_state(story_id)
        r._save(run_id, st)
        if current == "Refinamento":  # o agente pediu esclarecimento: a spec voltou para refinamento
            return r._report(story_id, run_id, f"aguardando esclarecimento ({task_id}): {rep.reason}")
        if current and r.workflow.can(current, "block"):
            r._transition(story_id, "block", f"{task_id}: {rep.reason}")
        return r._report(story_id, run_id, f"{task_id} bloqueada: {rep.reason}")
    return r._escalate(story_id, run_id, st, f"{task_id} {rep.state.lower()}: {rep.reason}")
