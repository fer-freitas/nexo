"""Dúvidas levantadas por agentes durante a execução."""

from __future__ import annotations

from pathlib import Path

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.spec import questions as q
from nexo.kernel.workflow.handoff import ProgressEntry
from nexo.kernel.workspace import specs, tasks
from nexo.store.runs import RunStore


def project_root_for(worktree: Path) -> Path:
    return worktree.parents[2] if worktree.parent.name == "worktrees" else worktree


def feature_for_story(project_root: Path, story: str | None) -> Path | None:
    if not story:
        return None
    for folder in sorted(specs.specs_root(project_root).glob("F-*")):
        backlog = specs.load_backlog(folder) if folder.is_dir() else None
        if backlog and any(s.id == story for s in backlog.stories):
            return folder
    return None


def register(ctx: AppContext, run_id: str, question: str, section: str, blocking: bool) -> str:
    store = RunStore(ctx.conn)
    run = store.get_run(run_id)
    if run is None or not run.worktree:
        raise NexoError(f"execução {run_id} não encontrada")
    root = project_root_for(Path(run.worktree))
    task = tasks.load_task(root, run.target_id)
    folder = feature_for_story(root, task.story)
    qid = "—"
    if folder is not None:
        qs = specs.load_questions(folder)
        before = {x.id for x in qs}
        severity = "blocking" if blocking else "important"
        qs = q.merge_new(
            qs, [{"text": f"[{task.id}] {question}", "severity": severity, "section": section, "source": "agent"}]
        )
        new = [x for x in qs if x.id not in before]
        qid = new[0].id if new else "—"
        specs.save_questions(folder, qs)
    entry = ProgressEntry(
        blockers=[f"esclarecimento {qid}: {question}"] if blocking else [],
        decisions=[] if blocking else [f"dúvida não bloqueante {qid}: {question}"],
    )
    store.add_progress(run_id, None, entry)
    if blocking and folder is not None and qid != "—":  # pergunta no Teams, resposta pelo central
        from contextlib import suppress

        with suppress(NexoError, OSError):
            ask_remote(ctx, root, folder.name, qid, question, task.story or task.id)
    ctx.audit(
        "agent.clarification",
        {
            "task": task.id,
            "question": question,
            "id": qid,
            "blocking": blocking,
            "feature": folder.name if folder else None,
        },
        ref=run_id,
    )
    return qid


def ask_remote(ctx: AppContext, root: Path, feature_dir: str, qid: str, question: str, item: str) -> str | None:
    """Registra o id no central (sem o texto) e manda o cartão ao Teams com o link do formulário de resposta."""
    import json

    from nexo.kernel.platform import flags, telemetry
    from nexo.kernel.runtime import http
    from nexo.modules.notify.app import notify

    if not telemetry.enabled(ctx) or not flags.enabled(ctx, "clarification_teams"):
        return None
    fid = "-".join(feature_dir.split("-")[:2])
    gid = f"{telemetry.device_id(ctx)[:8]}-{fid}-{qid}"
    url = telemetry._url(ctx)
    http.request("POST", f"{url}/v2/clarifications", {"Authorization": f"Bearer {telemetry._bearer(ctx)}"}, {"id": gid})
    path = ctx.home / "clarifications.json"
    pending = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    pending[gid] = {"root": str(root), "feature": fid, "qid": qid, "item": item}
    path.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")
    notify(
        ctx,
        "clarification",
        f"Nexo precisa de uma resposta ({fid} · {qid})",
        question,
        ref=item,
        link=f"{url}/v2/clarifications/{gid}/form",
    )
    ctx.audit("clarification.remote", {"id": gid, "item": item})
    return gid


def apply_remote_answers(ctx: AppContext) -> list[str]:
    """`nexo watch`: respostas chegadas pelo central viram resposta da dúvida e a execução é retomada."""
    import json

    from nexo.kernel.platform import telemetry
    from nexo.kernel.runtime import http

    path = ctx.home / "clarifications.json"
    if not path.is_file() or not telemetry.enabled(ctx):
        return []
    pending = json.loads(path.read_text(encoding="utf-8"))
    body = http.request(
        "GET",
        f"{telemetry._url(ctx)}/v2/clarifications/answers",
        {"Authorization": f"Bearer {telemetry._bearer(ctx)}"},
    ).body
    resumed = []
    for ans in dict(body or {}).get("answers") or []:
        info = pending.pop(str(ans["id"]), None)
        if info is None:
            continue
        project = AppContext(cwd=Path(info["root"]), home=ctx.home, options=ctx.options)
        from nexo.modules.planning.app.plan import Planner

        Planner(project, None, lambda *_: None).answer(info["feature"], {info["qid"]: str(ans["answer"])}, refine=False)
        project.audit("clarification.answered", {"id": ans["id"], "by": ans.get("by")}, ref=info["item"])
        resumed.append(str(info["item"]))
        project.close()
    path.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")
    return resumed
