"""Publicação: registro da entrega, PR da história, documentação viva pelo writer e as-built."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from nexo.kernel.errors import NexoError
from nexo.kernel.spec.model import Spec
from nexo.kernel.workspace import git
from nexo.modules.execution.app.story import tasks as story_tasks
from nexo.modules.execution.app.story.common import StoryReport

if TYPE_CHECKING:
    from nexo.modules.execution.app.story.runner import StoryRunner


DELIVERIES = "entregas"


def _state(r: StoryRunner, run_id: str) -> dict[str, Any]:
    current = getattr(r, "_current", None)
    return current[1] if current and current[0] == run_id else r._load_state(run_id)


def record(r: StoryRunner, story_id: str) -> Path | None:
    """`.nexo/entregas/<história>.yaml` na branch da história, no mesmo commit que vai para o PR.

    É o que o portal do produto lê de cada repositório (o estado local do Nexo não vai para o git): o que foi
    entregue, em qual feature e parte do produto, quem aprovou, o que mudou e quais decisões a feature tem.
    Um arquivo por história evita conflito quando vários devs entregam no mesmo dia. Falha aqui não bloqueia a
    entrega: fica na trilha."""
    from nexo.kernel.context import PROJECT_DIR
    from nexo.kernel.runtime import yamlfile
    from nexo.modules.planning.app import product_context as pc
    from nexo.store.runs import now_iso

    run = r.story_run(story_id)
    if run is None:
        return None
    st = _state(r, run.id)
    worktree = Path(str(st.get("worktree") or ""))
    if not st.get("worktree") or not worktree.is_dir():
        return None
    try:
        folder, spec, backlog, tasks = r._load(story_id)
        story = next(s for s in backlog.stories if s.id == story_id)
        changed = [f for f in git.changed_files(worktree, st["base"]) if not f.startswith(f"{PROJECT_DIR}/")]
        stat = git.git(["diff", "--shortstat", f"{st['base']}..HEAD"], worktree, check=False).strip()
        adr_dir = folder / "adr"
        gate = st.get("gate") or {}
        data: dict[str, Any] = {
            "story": story_id,
            "title": story.title,
            "feature": spec.id,
            "feature_title": spec.title,
            "part": pc.project_settings(r.ctx)[1],
            "delivered_at": now_iso()[:10],
            "approved_by": str(st.get("approved_by") or ""),
            "gate": {"decision": gate.get("decision"), "score": gate.get("score")},
            "tasks": [t.id for t in tasks],
            "changed": stat,
            "files": changed[:60],
            "decisions": sorted(a.name for a in adr_dir.glob("*.md")) if adr_dir.is_dir() else [],
            "technical_requirement": (folder / "requisito-tecnico.md").is_file(),
        }
        path = worktree / PROJECT_DIR / DELIVERIES / f"{story_id}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        yamlfile.dump(path, data)
        git.checkpoint(worktree, f"{story_id}: registro da entrega")
    except (NexoError, OSError, KeyError, StopIteration) as exc:
        r.ctx.audit("story.delivery_record_failed", {"story": story_id, "error": str(exc)}, ref=story_id)
        return None
    r.ctx.audit(
        "story.delivery_record", {"story": story_id, "path": path.relative_to(worktree).as_posix()}, ref=story_id
    )
    return path


def publish(r: StoryRunner, story_id: str) -> None:
    """História concluída: push da branch e PR (GitHub, GitLab, Bitbucket ou Azure Repos) com `AB#id`,
    evidências e decisão do portão (4.4)."""
    from nexo.modules.delivery.app.scm import PROVIDERS

    scm_cfg = dict(r.cfg.get("scm") or {})
    if scm_cfg.get("provider") not in PROVIDERS or not scm_cfg.get("open_pr", True):
        return
    run = r.story_run(story_id)
    if run is None:
        return
    st = _state(r, run.id)
    from nexo.kernel.platform import flags
    from nexo.kernel.workflow.autonomy import opens_pr

    if not flags.enabled(r.ctx, "pr_publish"):  # F17
        r.ctx.audit("scm.pr_skipped", {"story": story_id, "flag": "pr_publish"}, ref=story_id)
        return
    if not opens_pr(str(st.get("autonomy") or "L2")):  # F14 · L0: nada sai da máquina
        r.ctx.audit("scm.pr_skipped", {"story": story_id, "autonomy": st.get("autonomy")}, ref=story_id)
        return
    try:
        from nexo.modules.delivery.app import scm as delivery_scm

        scm = delivery_scm.build(r.ctx)
        scm.push(Path(st["worktree"]), st["branch"])
        link = r.sync.ref(story_id)
        ab = f"AB#{link['ref']} " if link and r.sync.kind == "azure" else ""
        gate = st.get("gate") or {}
        title_row = next((r["title"] for r in r.store.items() if r["id"] == story_id), story_id)
        body = "\n".join(
            [
                f"História **{story_id}** entregue pelo Nexo.",
                "",
                f"- Portão: {gate.get('decision')} · confiança {gate.get('score')}",
                f"- Bugs no ciclo: {', '.join(st.get('bugs') or []) or 'nenhum'}",
                f"- Tarefas: {', '.join(st.get('tasks') or {})}",
                "- Rastreabilidade: `.nexo/specs/*/traceability.yaml`",
                "",
                ab.strip(),
            ]
        )
        pr = scm.open_pr(st["branch"], f"{ab}{story_id}: {title_row}", body)
        st["pr"] = pr.get("html_url")
        r._save(run.id, st)  # sem isto o `nexo pr sync` não achava o PR (achado no teste real com o GitHub)
        r.ctx.audit("scm.pr", {"story": story_id, "pr": pr.get("html_url"), "branch": st["branch"]}, ref=story_id)
        r.sync.comment(story_id, f"PR aberto: {pr.get('html_url')}")
    except (NexoError, OSError, KeyError) as exc:
        message = exc.message if isinstance(exc, NexoError) else str(exc)
        r.ctx.audit("scm.error", {"story": story_id, "error": message}, ref=story_id)


def docs(
    r: StoryRunner, story_id: str, run_id: str, st: dict[str, Any], spec: Spec, worktree: Path
) -> StoryReport | None:
    """F35 (docs.writer: auto): o agente writer atualiza a documentação antes das revisões.

    Falha do writer não bloqueia a entrega: fica registrada e a documentação pode ser feita depois."""
    from nexo.kernel.platform import flags
    from nexo.modules.knowledge.app import writer

    if st.get("docs_done") or writer.mode(r.ctx) != "auto" or not flags.enabled(r.ctx, "writer"):
        return None
    changed = git.changed_files(worktree, st["base"])
    tid = f"{story_id}-DOC"
    story_tasks.write(
        r,
        tid,
        f"Documentar {story_id}",
        "docs",
        writer.context(r.ctx, worktree, changed, story_id),
        ["documentos afetados atualizados com as fontes citadas", "docs/historico.md com a entrega"],
        writer.doc_targets(worktree),
        story_id,
        spec,
    )
    rep = story_tasks.run_one(r, tid, st, role="writer", policy="doc-writer")
    st["docs_done"] = True
    if rep.state != "Concluido":
        st["docs"] = {"state": rep.state, "reason": rep.reason}
        r.ctx.audit("story.docs_skipped", {"story": story_id, "reason": rep.reason}, ref=story_id)
        r._save(run_id, st)
        return None
    problem = story_tasks.merge(r, story_id, run_id, st, worktree, [tid])
    if problem is not None:
        return problem
    st["docs"] = {"state": "Concluido", "task": tid}
    r.ctx.audit("story.docs", {"story": story_id, "task": tid}, ref=story_id)
    r._save(run_id, st)
    return None


def as_built(r: StoryRunner, story_id: str) -> None:
    """Última história da feature concluída → documentos as-built e publicação."""
    from nexo.modules.knowledge.app import docs

    try:
        docs.as_built(r.ctx, r.root, story_id)
    except (NexoError, OSError) as exc:
        r.ctx.audit("docs.as_built_failed", {"story": story_id, "error": str(exc)}, ref=story_id)
