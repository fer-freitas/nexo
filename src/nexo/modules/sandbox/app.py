"""`nexo try`: agente real em área descartável, sem demanda, sem PR e sem aprovação.

Nada aqui fura o piso: política, redação, ambiente mínimo e auditoria continuam valendo. Para o resultado ir adiante,
só com `promote`, que cria uma ideia e segue o caminho normal, com todos os portões.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.platform import config
from nexo.kernel.runtime import yamlfile
from nexo.kernel.security.redaction import redact

KEEP_SECONDS = 7 * 86400
DEFAULT_MAX_BRL = 5.0


def _folder(ctx: AppContext) -> Path:
    path = ctx.home / "sandbox"
    path.mkdir(parents=True, exist_ok=True)
    return path


def purge(ctx: AppContext, now: float | None = None) -> list[str]:
    removed = []
    moment = time.time() if now is None else now
    for meta in _folder(ctx).glob("TRY-*.yaml"):
        data = yamlfile.load(meta)
        if moment - float(data.get("at", moment)) > KEEP_SECONDS:
            discard(ctx, meta.stem)
            removed.append(meta.stem)
    return removed


def max_brl(ctx: AppContext) -> float:
    cfg = dict(config.resolved(ctx).values.get("sandbox") or {})
    return float(cfg.get("try_max_brl", DEFAULT_MAX_BRL))


def _next_id(ctx: AppContext) -> str:
    numbers = [int(p.stem[4:]) for p in _folder(ctx).glob("TRY-*.yaml") if p.stem[4:].isdigit()]
    return f"TRY-{max(numbers, default=0) + 1}"


def _git(root: Path, *args: str, check: bool = True) -> str:
    done = subprocess.run(
        ["git", "-c", "core.longpaths=true", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if check and done.returncode != 0:
        raise NexoError(f"git {args[0]} falhou: {done.stderr.strip()[:300]}")
    return done.stdout


def _task_file(ctx: AppContext, tid: str) -> Path:
    from nexo.kernel.workspace.tasks import sandbox_dir

    return sandbox_dir(Path(str(ctx.project_root))) / f"{tid}.yaml"


def create(ctx: AppContext, request: str, agent: str = "", skill: str = "") -> dict[str, Any]:
    """Registra o experimento como tarefa local. Não vai para o repositório do time nem vira demanda."""
    if ctx.project_root is None:
        raise NexoError("rode dentro de um repositório do projeto (`nexo init`)")
    if not request.strip():
        raise NexoError("descreva o que quer experimentar")
    purge(ctx)
    tid = _next_id(ctx)
    clean = redact(request)[:2000]
    path = _task_file(ctx, tid)
    path.parent.mkdir(parents=True, exist_ok=True)
    yamlfile.dump(path, {"title": clean[:80], "description": clean, "kind": "backend", "acceptance": []})
    data = {
        "id": tid,
        "request": clean,
        "agent": agent,
        "skill": skill,
        "base": _git(ctx.project_root, "rev-parse", "HEAD").strip(),
        "at": time.time(),
        "project": str(ctx.project_root),
        "state": "criado",
        "max_brl": max_brl(ctx),
    }
    yamlfile.dump(_folder(ctx) / f"{tid}.yaml", data)
    ctx.audit("sandbox.created", {"id": tid, "agent": agent, "skill": skill}, ref=tid)
    return data


def run_agent(ctx: AppContext, data: dict[str, Any], ui: Any = None) -> dict[str, Any]:
    """Roda pelo mesmo caminho das tarefas: política, ambiente mínimo, roteamento, custo e auditoria valem igual."""
    from nexo.kernel import services

    run = services.load("execution.run")
    Orchestrator, RunOptions = run.Orchestrator, run.RunOptions  # noqa: N806
    extra = f"Use a skill {data['skill']}." if data.get("skill") else ""
    extra += " É um experimento: não faça push, não abra PR e não altere nada fora desta pasta."
    options = RunOptions(tool=str(data.get("agent") or "") or None, extra_prompt=extra)
    report = Orchestrator(ctx, options, ui).run_task(str(data["id"]))
    data.update(
        state=report.state,
        reason=redact(report.reason or "")[:500],
        branch=report.branch or f"nexo/task/{data['id']}",
        worktree=report.worktree or "",
        routes=report.routes,
    )
    yamlfile.dump(_folder(ctx) / f"{data['id']}.yaml", data)
    ctx.audit("sandbox.finished", {"id": data["id"], "state": report.state, "routes": report.routes}, ref=str(data["id"]))
    return data


def listing(ctx: AppContext) -> list[dict[str, Any]]:
    return [dict(yamlfile.load(p)) for p in sorted(_folder(ctx).glob("TRY-*.yaml"))]


def show(ctx: AppContext, tid: str) -> dict[str, Any]:
    meta = _folder(ctx) / f"{tid}.yaml"
    if not meta.is_file():
        raise NexoError(f"experimento desconhecido: {tid}")
    data = dict(yamlfile.load(meta))
    data["diff"] = _diff(data, "--stat")
    return data


def _diff(data: dict[str, Any], *args: str) -> str:
    project = Path(str(data.get("project", "")))
    branch = str(data.get("branch") or "")
    if not branch or not project.is_dir():
        return ""
    return _git(project, "diff", *args, f"{data.get('base')}..{branch}", check=False)


def _task_file_at(project: Path, tid: str) -> Path:
    from nexo.kernel.workspace.tasks import sandbox_dir

    return sandbox_dir(project) / f"{tid}.yaml"


def discard(ctx: AppContext, tid: str) -> dict[str, Any]:
    meta = _folder(ctx) / f"{tid}.yaml"
    if not meta.is_file():
        raise NexoError(f"experimento desconhecido: {tid}")
    data = yamlfile.load(meta)
    project = Path(str(data.get("project", "")))
    worktree = str(data.get("worktree") or "")
    if project.is_dir():
        if worktree:
            _git(project, "worktree", "remove", "--force", worktree, check=False)
            with contextlib.suppress(OSError):
                shutil.rmtree(worktree, ignore_errors=True)
        _git(project, "worktree", "prune", check=False)
        if data.get("branch"):
            _git(project, "branch", "-D", str(data["branch"]), check=False)
        _task_file_at(project, tid).unlink(missing_ok=True)
    meta.unlink()
    ctx.audit("sandbox.discarded", {"id": tid}, ref=tid)
    return {"id": tid, "discarded": True}


def promote(ctx: AppContext, tid: str) -> dict[str, Any]:
    """Vira ideia, com o diff anexado como insumo. Não é entrega: segue o fluxo inteiro."""
    from nexo.kernel import services

    core, flow = services.load("demands.core"), services.load("demands.flow")
    data = show(ctx, tid)
    idea = flow.idea(ctx, str(data["request"]), source=tid)
    diff = _diff(data)
    if diff.strip():
        core.store(ctx).path("ideas", idea["id"], "experimento.diff").write_text(redact(diff), "utf-8", newline="\n")
        core.store(ctx).push(f"nexo: experimento {tid} anexado a {idea['id']}")
    ctx.audit("sandbox.promoted", {"id": tid, "idea": idea["id"]}, ref=tid)
    return {"id": tid, "idea": idea["id"]}
