"""Feature inteira desassistida: `nexo run feature` e o runner remoto por fila.

- Ordem por dependência entre histórias (derivada das dependências das tarefas), em ondas; dentro da onda até
  `--parallel` histórias ao mesmo tempo (processos separados, cada uma no seu worktree).
- Antes de cada onda, o orçamento do mês: esgotado → para e informa (retoma no `nexo watch`/próxima execução).
- História que não termina (humano, escalada, bloqueio) segura só as dependentes; as independentes seguem.
- Runner remoto: uma VM/agente de CI da empresa consome uma pasta-fila (`*.json` com projeto e feature/história)
  com as mesmas políticas e a identidade de serviço (GitHub App/Service Principal, commits assinados).
"""

from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from nexo.kernel.backlog import Backlog
from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.workspace import specs

FINAL = ("Concluido",)


def story_waves(backlog: Backlog) -> list[list[str]]:
    """Ondas de histórias: B depende de A se alguma tarefa de B depende de uma tarefa de A."""
    owner = {t.id: t.story for t in backlog.tasks}
    deps: dict[str, set[str]] = {s.id: set() for s in backlog.stories}
    for t in backlog.tasks:
        for d in t.depends_on:
            other = owner.get(d)
            if other and other != t.story and t.story in deps:
                deps[t.story].add(other)
    waves: list[list[str]] = []
    done: set[str] = set()
    remaining = [s.id for s in backlog.stories]
    while remaining:
        wave = [s for s in remaining if deps[s] <= done]
        if not wave:
            raise NexoError(f"dependência circular entre histórias: {', '.join(remaining)}")
        waves.append(wave)
        done |= set(wave)
        remaining = [s for s in remaining if s not in wave]
    return waves


def _state(ctx: AppContext, story_id: str) -> str:
    from nexo.store.runs import RunStore, project_key

    if ctx.project_root is None:
        raise NexoError("nenhum projeto Nexo encontrado")
    return RunStore(ctx.conn, project_key(ctx.project_root)).item_state(story_id) or "Pronto"


def _base_for(ctx: AppContext, backlog: Backlog, story_id: str) -> str | None:
    """Base da história: o branch da dependência que ainda não está na principal; várias pendentes → espera."""
    from nexo.kernel.workspace import git

    root = ctx.project_root
    if root is None:
        return None
    pending = [
        d
        for d in sorted(_deps_of(backlog, story_id))
        if git.branch_exists(root, f"nexo/story/{d}") and not git.is_ancestor(root, f"nexo/story/{d}")
    ]
    if len(pending) > 1:
        raise NexoError(f"dependências ainda não mergeadas: {', '.join(pending)}")
    return f"nexo/story/{pending[0]}" if pending else None


def _run_one(
    ctx: AppContext, story_id: str, parallel: int, state: str | None = None, base: str | None = None
) -> dict[str, Any]:
    """`state` já lido na thread principal (a conexão SQLite não é compartilhada entre threads)."""
    from nexo.modules.execution.app.run import RunOptions
    from nexo.modules.execution.app.story import StoryRunner

    state = state or _state(ctx, story_id)
    root = ctx.project_root
    if root is None:
        raise NexoError("nenhum projeto Nexo encontrado")
    if state == "AguardandoHumano":
        return {"story": story_id, "state": state, "reason": "aguardando humano"}
    if parallel > 1:  # processo separado: cada história com seu worktree e sua conexão
        from nexo.modules.execution.app.agents import nexo_command

        verb = "resume" if state not in ("Pronto",) else "run"
        args = (
            [*nexo_command(), "--json", "resume", story_id]
            if verb == "resume"
            else [*nexo_command(), "--json", "run", "story", story_id]
        )
        proc = subprocess.run(args, cwd=root, capture_output=True, text=True, encoding="utf-8", check=False)
        try:
            data = dict(json.loads(proc.stdout))
        except ValueError:
            return {"story": story_id, "state": "Erro", "reason": (proc.stderr or proc.stdout)[-300:]}
        return {"story": story_id, "state": data.get("state"), "reason": data.get("reason", "")}
    runner = StoryRunner(ctx, RunOptions(base_ref=base))
    report = runner.run(story_id) if state == "Pronto" else runner.resume(story_id)
    return {"story": story_id, "state": report.state, "reason": report.reason}


def run_feature(ctx: AppContext, fid: str, parallel: int = 1) -> dict[str, Any]:
    from nexo.kernel import services
    from nexo.kernel.platform import flags

    costs = services.load("finops.costs")
    from nexo.store.runs import RunStore, project_key

    flags.require(ctx, "run_feature")
    root = ctx.project_root
    if root is None:
        raise NexoError("nenhum projeto Nexo encontrado")
    backlog = specs.load_backlog(specs.feature_dir(root, fid))
    if backlog is None:
        raise NexoError(f"{fid} ainda não tem backlog aprovado (nexo plan approve)")
    store = RunStore(ctx.conn, project_key(root))
    waves = story_waves(backlog)
    ctx.audit("feature.run", {"feature": fid, "waves": waves, "parallel": parallel})
    results: dict[str, dict[str, Any]] = {}
    stopped = ""
    for wave in waves:
        budget = costs.budget_status(ctx)
        if budget.limit_brl is not None and budget.spent_brl >= budget.limit_brl:
            stopped = "orçamento do mês esgotado"
            break
        ready: list[tuple[str, list[str]]] = []
        held: list[tuple[str, list[str]]] = []
        for sid in wave:
            if (store.item_state(sid) or "") in FINAL:
                results[sid] = {"story": sid, "state": "Concluido", "reason": "já concluída"}
                continue
            blockers = [
                d
                for d in _deps_of(backlog, sid)
                if results.get(d, {}).get("state") not in FINAL and (store.item_state(d) or "") not in FINAL
            ]
            (held if blockers else ready).append((sid, blockers))
        for sid, blockers in held:
            results[sid] = {"story": sid, "state": "Aguardando", "reason": f"depende de {', '.join(blockers)}"}
        bases: dict[str, str | None] = {}
        for sid, _ in list(ready):
            try:
                bases[sid] = _base_for(ctx, backlog, sid)
            except NexoError as exc:
                results[sid] = {"story": sid, "state": "Aguardando", "reason": exc.message}
                ready.remove((sid, []))
        if parallel > 1 and len(ready) > 1:
            jobs = [(sid, _state(ctx, sid)) for sid, _ in ready]
            with ThreadPoolExecutor(max_workers=parallel) as pool:
                futures = [pool.submit(_run_one, ctx, sid, parallel, st, bases.get(sid)) for sid, st in jobs]
                for fut in futures:
                    res = fut.result()
                    results[res["story"]] = res
        else:
            for sid, _ in ready:
                results[sid] = _run_one(ctx, sid, 1, None, bases.get(sid))
    order = [s for w in waves for s in w]
    stories = [results.get(s, {"story": s, "state": "Pendente", "reason": stopped}) for s in order]
    done = sum(1 for s in stories if s["state"] in FINAL)
    ctx.audit("feature.run_end", {"feature": fid, "done": done, "total": len(stories), "stopped": stopped})
    return {"feature": fid, "waves": waves, "stories": stories, "done": done, "stopped": stopped}


def _deps_of(backlog: Backlog, story: str) -> set[str]:
    owner = {t.id: t.story for t in backlog.tasks}
    return {
        owner[d] for t in backlog.tasks if t.story == story for d in t.depends_on if d in owner and owner[d] != story
    }


# ---------------------------------------------------------------- runner remoto


def serve(ctx: AppContext, queue: Path, once: bool = False, interval: float = 30.0) -> list[dict[str, Any]]:
    """Consome `queue/*.json` ({"project": caminho, "feature": "F-1"} ou {"story": "US-1-1"}); resultado em
    `queue/done/` ou `queue/failed/`. Com `once`, processa o que houver e sai."""
    import time

    from nexo.kernel.platform import flags

    flags.require(ctx, "runner")
    processed: list[dict[str, Any]] = []
    for sub in ("done", "failed", "running"):
        (queue / sub).mkdir(parents=True, exist_ok=True)
    while True:
        for job_file in sorted(queue.glob("*.json")):
            running = queue / "running" / job_file.name
            try:
                job_file.replace(running)  # reivindica o job (várias instâncias não pegam o mesmo)
            except OSError:
                continue
            job = json.loads(running.read_text(encoding="utf-8"))
            project = AppContext(cwd=Path(str(job["project"])), home=ctx.home, options=ctx.options)
            try:
                if job.get("feature"):
                    result: dict[str, Any] = run_feature(project, str(job["feature"]), int(job.get("parallel", 1)))
                else:
                    result = _run_one(project, str(job["story"]), 1)
                ok = True
            except NexoError as exc:
                result, ok = {"error": exc.message}, False
            finally:
                project.close()
            target = queue / ("done" if ok else "failed") / job_file.name
            target.write_text(
                json.dumps({"job": job, "result": result}, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            running.unlink(missing_ok=True)
            ctx.audit("runner.job", {"job": job_file.name, "ok": ok})
            processed.append({"job": job_file.name, "ok": ok})
        if once:
            return processed
        time.sleep(interval)
