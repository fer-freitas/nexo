"""Revisão automática da quebra (achado no vídeo do dia de trabalho): cada tarefa precisa dar para implementar só
com a própria descrição.

Logo depois da quebra, uma chamada de IA (papel `breakdown-reviewer`) lê todas as tarefas com a spec, as premissas e
as decisões. Quando a resposta está nesses dados, o texto que falta é acrescentado à descrição da tarefa, sem refazer
a quebra. Quando não está, vira dúvida da feature para o tech lead (`source: review`); a resposta entra na descrição
da tarefa e não replaneja a feature.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nexo.kernel.backlog import Backlog
from nexo.kernel.context import AppContext
from nexo.kernel.runtime import yamlfile
from nexo.kernel.spec import questions as q
from nexo.kernel.spec.model import Spec
from nexo.kernel.workspace import specs
from nexo.kernel.workspace import tasks as workspace_tasks

SOURCE = "review"
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "problem": {"type": "string"},
                    "fix": {"type": "string"},
                    "question": {"type": "string"},
                },
                "required": ["task", "problem"],
            },
        }
    },
    "required": ["issues"],
}


def _decided(folder: Path, spec: Spec) -> str:
    path = folder / "premissas.yaml"
    premises = (
        [str(p.get("text", "")) for p in yamlfile.load(path).get("premises") or [] if isinstance(p, dict)]
        if path.is_file()
        else list(spec.assumptions)
    )
    lines = [f"- Premissa: {x}" for x in premises]
    lines += [f"- Decisão: {d.title}: {d.decision}" for d in spec.decisions if d.decision]
    if spec.design and spec.design.solution:
        lines.insert(0, f"- Solução: {spec.design.solution}")
    return "\n".join(lines) or "- nenhuma"


def review(planner: Any, folder: Path, spec: Spec, backlog: Backlog) -> Backlog:
    """Completa as tarefas com o que já foi decidido e transforma o resto em dúvida para o tech lead."""
    import os

    from nexo.kernel.platform import config
    from nexo.modules.execution.app import llm
    from nexo.modules.knowledge.app import content

    ctx: AppContext = planner.ctx
    if os.environ.get("NX_REVIEW_BREAKDOWN") == "0":  # testes com cenário fixo de respostas
        return backlog
    if (config.resolved(ctx).values.get("planning") or {}).get("review_breakdown") is False:
        return backlog
    prompt = content.prompt(ctx, "breakdown-reviewer", spec.id).format(
        spec=yamlfile_dump(spec.model_dump(mode="json", exclude_defaults=True)),
        decided=_decided(folder, spec),
        backlog=yamlfile_dump(backlog.model_dump(mode="json", exclude_defaults=True)),
    )
    try:
        result = llm.call_structured(
            ctx,
            "breakdown-reviewer",
            spec.id,
            prompt,
            SCHEMA,
            folder,
            facts={"kind": "planning"},
            data_class=spec.data_class or "confidential",
            ui=planner.ui,
            **planner._route_options(),
        )
    except Exception as exc:  # revisão é uma rede de proteção: nunca impede aprovar o plano
        ctx.audit("plan.breakdown_review_failed", {"feature": spec.id, "error": str(exc)[:200]}, ref=spec.id)
        return backlog
    known = {t.id for t in backlog.tasks}
    issues = [i for i in result.data.get("issues") or [] if str(i.get("task")) in known]
    fixes = {str(i["task"]): str(i["fix"]).strip() for i in issues if str(i.get("fix") or "").strip()}
    tasks = [
        t.model_copy(update={"description": f"{t.description}\n\n{fixes[t.id]}".strip()}) if t.id in fixes else t
        for t in backlog.tasks
    ]
    asks = [
        {"text": f"[{i['task']}] {i['question']}", "severity": "important", "section": "tech_lead", "source": SOURCE}
        for i in issues
        if str(i["task"]) not in fixes and str(i.get("question") or "").strip()
    ]
    if asks:
        specs.save_questions(folder, q.merge_new(specs.load_questions(folder), asks))
    ctx.audit(
        "plan.breakdown_reviewed",
        {"feature": spec.id, "issues": len(issues), "completed": sorted(fixes), "questions": len(asks)},
        ref=spec.id,
    )
    if fixes and planner.ui is not None:
        planner.ui(
            "info", {"message": f"quebra revisada: {', '.join(sorted(fixes))} completadas com o que a spec define"}
        )
    if asks and planner.ui is not None:
        planner.ui("warning", {"message": f"{len(asks)} dúvida(s) da quebra para o tech lead (plan answer {spec.id})"})
    return backlog.model_copy(update={"tasks": tasks})


def apply_answers(ctx: AppContext, folder: Path, answered: list[q.Question]) -> None:
    """Resposta do tech lead a uma dúvida da revisão vai para a descrição da tarefa (backlog e arquivo da tarefa)."""
    import re

    mine = [x for x in answered if x.source == SOURCE]
    if not mine:
        return
    backlog = specs.load_backlog(folder)
    root = folder.parents[2] if folder.parent.name == "specs" else None
    for item in mine:
        found = re.match(r"\[(T-[\w-]+)\]", item.text)
        if not found:
            continue
        task_id = found.group(1)
        note = f"Decidido pelo tech lead ({item.id}): {item.answer}"
        if backlog is not None:
            backlog = backlog.model_copy(
                update={
                    "tasks": [
                        t.model_copy(update={"description": f"{t.description}\n\n{note}".strip()})
                        if t.id == task_id
                        else t
                        for t in backlog.tasks
                    ]
                }
            )
        if root is not None:
            path = workspace_tasks.tasks_dir(root) / f"{task_id}.yaml"
            if path.is_file():
                data = yamlfile.load(path)
                data["description"] = f"{data.get('description') or ''}\n\n{note}".strip()
                yamlfile.dump(path, data)
        ctx.audit("plan.breakdown_answer", {"task": task_id, "question": item.id}, ref=task_id)
    if backlog is not None:
        specs.save_backlog(folder, backlog)


def yamlfile_dump(data: dict[str, Any]) -> str:
    import io

    from ruamel.yaml import YAML

    out = io.StringIO()
    YAML().dump(data, out)
    return out.getvalue()
