"""Trabalho do dev: o que está pendente, o que a tarefa pede e se a história está pronta de verdade.

- `tasks`: tarefas por história, com estado, tamanho e o que falta (sem IA).
- `explain <T-n|US-n>`: a IA (papel `briefing`) explica o quê, por quê, onde mexer, cuidados e como validar.
- `check <US-n>`: conferência determinística da história — DoR, critérios cobertos, tarefas, testes travados,
  pipeline, portão, PR e custo — com o que fazer em cada item que falta.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexo.kernel import services
from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.workspace import specs
from nexo.kernel.workspace import tasks as workspace_tasks
from nexo.modules.execution.app import llm
from nexo.store.runs import RunStore, project_key

content = services.load("knowledge.content")
feature_for_story = services.get("planning.clarify", "feature_for_story")

DONE = "Concluido"
BRIEFING_SCHEMA = {
    "type": "object",
    "properties": {
        "what": {"type": "string"},
        "why": {"type": "string"},
        "where": {"type": "array", "items": {"type": "string"}},
        "watch_out": {"type": "array", "items": {"type": "string"}},
        "how_to_check": {"type": "array", "items": {"type": "string"}},
        "unclear": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["what", "why", "where", "how_to_check"],
}


def _root(ctx: AppContext) -> Path:
    if ctx.project_root is None:
        raise NexoError("nenhum projeto encontrado; rode `nexo init`")
    return ctx.project_root


def _store(ctx: AppContext) -> RunStore:
    return RunStore(ctx.conn, project_key(_root(ctx)))


def my_tasks(ctx: AppContext, include_done: bool = False) -> list[dict[str, Any]]:
    """Tarefas agrupadas por história (a ordem das histórias segue o backlog)."""
    root = _root(ctx)
    store = _store(ctx)
    by_story: dict[str, dict[str, Any]] = {}
    for task in workspace_tasks.list_tasks(root):
        state = store.item_state(task.id) or "Pronto"
        if state == DONE and not include_done:
            continue
        sid = task.story or "—"
        group = by_story.setdefault(
            sid, {"story": sid, "state": store.item_state(sid) or "—", "title": _story_title(root, sid), "tasks": []}
        )
        group["tasks"].append(
            {
                "id": task.id,
                "title": task.title,
                "state": state,
                "size": task.size or "—",
                "kind": task.kind or "—",
                "files": len(task.files_hint),
                "depends_on": task.depends_on,
            }
        )
    return sorted(by_story.values(), key=lambda g: str(g["story"]))


def _story_title(root: Path, sid: str) -> str:
    folder = feature_for_story(root, sid)
    backlog = specs.load_backlog(folder) if folder else None
    story = next((s for s in backlog.stories if s.id == sid), None) if backlog else None
    return story.title if story else ""


def _story(root: Path, sid: str) -> tuple[Path, Any, Any]:
    folder = feature_for_story(root, sid)
    backlog = specs.load_backlog(folder) if folder else None
    story = next((s for s in backlog.stories if s.id == sid), None) if backlog else None
    if folder is None or story is None:
        raise NexoError(f"{sid} não encontrada no backlog (a feature foi aprovada?)")
    return folder, story, specs.load_spec(folder)


def explain(ctx: AppContext, item: str, ui: Any = None) -> dict[str, Any]:
    """Explicação da tarefa (ou da história) para o dev, antes de começar."""
    from nexo.kernel import services

    discover = services.load("planning.discover")

    root = _root(ctx)
    item = item.upper()
    if item.startswith("T-"):
        task = next((t for t in workspace_tasks.list_tasks(root) if t.id == item), None)
        if task is None:
            raise NexoError(f"{item} não encontrada (nexo tasks lista as tarefas)")
        sid = task.story or ""
        hint = task.files_hint
        task_text = (
            f"{task.id} — {task.title}\nTipo: {task.kind or '—'} · tamanho {task.size or '—'}\n"
            f"{task.description or '(sem descrição)'}\nCritérios da tarefa:\n"
            + ("\n".join(f"- {a}" for a in task.acceptance) or "- (sem critérios explícitos)")
            + "\nArquivos sugeridos:\n"
            + ("\n".join(f"- {f}" for f in hint) or "- (não informados)")
        )
    else:
        sid, hint, task_text = item, [], f"{item} (a história inteira)"
    folder, story, spec = _story(root, sid)
    acs = [a for a in spec.acceptance if a.id in story.ac_refs]
    story_text = _story_block(story, spec) + "\n\n" + _decided(folder, spec, story, task_text)
    discover.ensure_map(ctx, root)
    budget = 3000
    repo_map = discover.context_for(root, hint, budget) if hint else ""
    if not repo_map:
        query = " ".join([story.title, *(a.title for a in acs)])
        repo_map = discover.repo_overview(root, query, budget) or "- (sem mapa: rode `nexo discover`)"
    prompt = content.prompt(ctx, "briefing", item).format(task=task_text, story=story_text, repo_map=repo_map)
    result = llm.call_structured(
        ctx,
        "briefing",
        item,
        prompt,
        BRIEFING_SCHEMA,
        root,
        facts={"kind": "planning"},
        data_class=spec.data_class or "confidential",
        read_files=True,
        ui=ui,
        forced_tool=ctx.options.tool,
        forced_model=ctx.options.model,
    )
    unclear = [str(x) for x in result.data.get("unclear") or [] if str(x).strip()]
    _save_unclear(folder, item, unclear)
    ctx.audit("dev.explained", {"item": item, "unclear": len(unclear)}, ref=item)
    return {"item": item, "story": sid, **result.data, "run_id": result.run_id}


UNCLEAR_DIR = "duvidas-do-dev"


def _save_unclear(folder: Path, item: str, unclear: list[str]) -> None:
    """Dúvida real que sobrou da explicação fica guardada para virar opção: levar ao tech lead."""
    from nexo.kernel.runtime import yamlfile

    path = folder / UNCLEAR_DIR / f"{item}.yaml"
    if not unclear:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(exist_ok=True)
    yamlfile.dump(path, {"item": item, "unclear": unclear, "sent": False})


def pending_unclear(root: Path) -> list[tuple[str, str, int]]:
    """(item, feature, quantidade) das explicações com dúvida ainda não levada ao tech lead."""
    from nexo.kernel.runtime import yamlfile

    out = []
    for path in sorted(specs.specs_root(root).glob(f"F-*/{UNCLEAR_DIR}/*.yaml")):
        data = yamlfile.load(path)
        if not data.get("sent") and data.get("unclear"):
            fid = "-".join(path.parents[1].name.split("-")[:2])
            out.append((str(data.get("item") or path.stem), fid, len(data["unclear"])))
    return out


def ask_tech_lead(ctx: AppContext, item: str) -> dict[str, Any]:
    """As dúvidas reais da explicação viram dúvidas da feature para o tech lead (não reabrem a spec)."""
    from nexo.kernel.runtime import yamlfile
    from nexo.kernel.spec import questions as q

    root = _root(ctx)
    item = item.upper()
    for path in specs.specs_root(root).glob(f"F-*/{UNCLEAR_DIR}/{item}.yaml"):
        data = yamlfile.load(path)
        folder = path.parents[1]
        qs = specs.load_questions(folder)
        before = {x.id for x in qs}
        proposed = [
            {"text": f"[{item}] {text}", "severity": "important", "section": "tech_lead", "source": "dev"}
            for text in data.get("unclear") or []
        ]
        merged = q.merge_new(qs, proposed)
        specs.save_questions(folder, merged)
        yamlfile.dump(path, {**data, "sent": True})
        added = [x for x in merged if x.id not in before]
        ctx.audit("dev.asked_tech_lead", {"item": item, "questions": [x.id for x in added]}, ref=item)
        return {
            "item": item,
            "feature": "-".join(folder.name.split("-")[:2]),
            "questions": [x.model_dump() for x in added],
        }
    raise NexoError(f"{item} não tem dúvida guardada (rode `nexo explain {item}` antes)")


def _story_block(story: Any, spec: Any) -> str:
    acs = [a for a in spec.acceptance if a.id in story.ac_refs]
    lines = [
        f"{story.id} — {story.title}",
        f"Como {story.as_a or '—'}, quero {story.i_want or '—'}, para {story.so_that or '—'}.",
    ]
    if story.context:
        lines.append(f"Contexto: {story.context}")
    lines += [
        f"- {a.id} {a.title}: dado {'; '.join(a.given)}; quando {'; '.join(a.when)}; então {'; '.join(a.then)}"
        for a in acs
    ]
    return "\n".join(lines)


def _decided(folder: Path, spec: Any, story: Any, task_text: str, limit: int = 6000) -> str:
    """Tudo o que já foi decidido e que a tarefa pode precisar: regras, requisitos, premissas, decisões técnicas e
    as outras histórias que a tarefa cita (achado na gravação: a explicação dizia "não está claro" sobre o que a
    spec e a outra história já definiam)."""
    import re

    from nexo.kernel.runtime import yamlfile

    parts: list[str] = []
    if spec.rules:
        parts.append(
            "Regras de negócio:\n"
            + "\n".join(f"- {r.id}: {r.text}" + (f" (ex.: {r.example})" if r.example else "") for r in spec.rules)
        )
    if spec.requirements:
        parts.append("Requisitos funcionais:\n" + "\n".join(f"- {r.id}: {r.statement}" for r in spec.requirements))
    premises_path = folder / "premissas.yaml"
    premises = (
        [str(p.get("text", "")) for p in (yamlfile.load(premises_path).get("premises") or []) if isinstance(p, dict)]
        if premises_path.is_file()
        else list(spec.assumptions)
    )
    if premises:
        parts.append("Premissas assumidas:\n" + "\n".join(f"- {x}" for x in premises))
    technical = [f"- {d.title}: {d.decision}" for d in spec.decisions if d.decision]
    if spec.design and spec.design.solution:
        technical.insert(0, f"- Solução: {spec.design.solution}")
    if technical:
        parts.append("Decisões técnicas:\n" + "\n".join(technical))
    backlog = specs.load_backlog(folder)
    cited = sorted(set(re.findall(r"US-\d+-\d+", task_text + " " + (story.context or ""))) - {story.id})
    for other in (s for s in (backlog.stories if backlog else []) if s.id in cited):
        parts.append("História citada:\n" + _story_block(other, spec))
    text = "\n\n".join(parts) or "- (nada além da história)"
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---------------------------------------------------------------------- conferência


@dataclass
class Check:
    label: str
    ok: bool | None  # None = não se aplica ainda
    detail: str = ""
    fix: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"label": self.label, "ok": self.ok, "detail": self.detail, "fix": self.fix}


def check(ctx: AppContext, sid: str) -> dict[str, Any]:
    """Está tudo certo com a história? Cada item diz o que falta e o comando que resolve."""
    from nexo.kernel import ready as ready_domain
    from nexo.kernel import services

    ready = services.load("planning.ready")

    root = _root(ctx)
    sid = sid.upper()
    folder, story, spec = _story(root, sid)
    store = _store(ctx)
    state = store.item_state(sid) or "Pronto"
    items: list[Check] = []

    # 1. Definition of Ready
    mode = ready.mode(ctx)
    if mode == "off":
        items.append(Check("Definition of Ready", None, "desligada nesta org"))
    else:
        path = ready.refinement_path(folder, sid)
        if not path.is_file():
            items.append(Check("Definition of Ready", False, "refinamento não criado", f"ready {sid} --init"))
        else:
            from nexo.kernel.runtime import yamlfile

            _, acs, kinds = ready._story(root, sid)
            rep = ready_domain.check(dict(yamlfile.load(path)), sid, acs, kinds, ready.floors(ctx))
            if rep.ready:
                items.append(Check("Definition of Ready", True, "completa"))
            else:  # em `warn` é aviso (não impede a entrega); em `required` é falta
                problems = "; ".join(rep.problems[:3])
                blocking = mode == "required"
                detail = problems if blocking else f"aviso: {problems}"
                items.append(Check("Definition of Ready", False if blocking else None, detail, f"ready {sid}"))

    # 2. critérios de aceite da história existem na spec e têm cenário
    known = {a.id: a for a in spec.acceptance}
    missing = [ac for ac in story.ac_refs if ac not in known]
    vague = [ac for ac in story.ac_refs if ac in known and not (known[ac].then and known[ac].when)]
    if missing or vague or not story.ac_refs:
        problem = (
            f"sem critério: {', '.join(missing)}"
            if missing
            else (f"sem quando/então: {', '.join(vague)}" if vague else "história sem critérios")
        )
        items.append(Check("Critérios de aceite", False, problem, f"plan feature {spec.id}"))
    else:
        items.append(Check("Critérios de aceite", True, f"{len(story.ac_refs)} com cenário"))

    # 3. tarefas (o estado da execução vale: a tarefa de teste é feita pelo QA e fica concluída ali)
    tasks = [t for t in workspace_tasks.list_tasks(root) if t.story == sid]
    # a execução da história é a que tem estado de história (aprovar ou retomar podem criar outras execuções)
    runs = [r for r in store.runs(sid) if (root / ".nexo" / "runs" / r.id / "story.json").is_file()]
    run = runs[-1] if runs else None
    st: dict[str, Any] = {}
    if run is not None:
        st = json.loads((root / ".nexo" / "runs" / run.id / "story.json").read_text(encoding="utf-8"))
    in_run = dict(st.get("tasks") or {})
    open_tasks = [t.id for t in tasks if (in_run.get(t.id) or store.item_state(t.id) or "Pronto") != DONE]
    if not tasks:
        items.append(Check("Tarefas", False, "nenhuma tarefa", f"plan approve {spec.id}"))
    elif run is None:
        items.append(Check("Tarefas", None, f"{len(tasks)} prontas para executar", f"run story {sid}"))
    else:
        items.append(
            Check(
                "Tarefas",
                not open_tasks,
                f"abertas: {', '.join(open_tasks)}" if open_tasks else f"{len(tasks)} concluídas",
            )
        )

    # 4. testes de aceite travados (test-first)
    locked = list(st.get("locked") or [])
    items.append(
        Check(
            "Testes de aceite travados",
            bool(locked) if run else None,
            f"{len(locked)} arquivo(s)" if locked else "ainda não escritos",
        )
    )

    # 5. pipeline de qualidade
    quality = root / ".nexo" / "runs" / run.id / "quality-report.json" if run else None
    if quality is not None and quality.is_file():
        stages = json.loads(quality.read_text(encoding="utf-8"))["stages"]
        failed = [s["id"] for s in stages if s["status"] in ("failed", "error")]
        items.append(
            Check(
                "Pipeline",
                not failed,
                f"falhou: {', '.join(failed)}" if failed else f"{len(stages)} etapas ok",
                f"resume {sid}" if failed else "",
            )
        )
    else:
        items.append(Check("Pipeline", None, "ainda não rodou"))

    # 6. portão
    gate = st.get("gate") or {}
    if state == DONE:
        items.append(Check("Portão", True, "aprovado"))
    elif state == "AguardandoHumano":
        score = f"confiança {gate.get('score', 0):.2f}".replace(".", ",") if gate else ""
        items.append(Check("Portão", False, f"aguardando decisão {score}".strip(), f"approve {sid}"))
    elif state in ("Escalado", "Bloqueado"):
        items.append(
            Check("Portão", False, f"história {state.lower()}: {run.reason if run else ''}".strip(), f"resume {sid}")
        )
    else:
        items.append(Check("Portão", None, "ainda não chegou"))

    # 7. PR
    pr = str(st.get("pr") or "")
    if pr:
        items.append(Check("PR", True, pr, "pr sync"))
    elif state == DONE and st.get("branch"):
        items.append(Check("PR", None, f"branch {st.get('branch')} sem PR (SCM não configurado?)"))
    else:
        items.append(Check("PR", None, "abre depois do portão"))

    # 8. custo
    cap = st.get("cap_brl")
    projection = st.get("projection_brl")
    if cap:
        over = bool(projection and projection > cap)
        items.append(
            Check(
                "Custo",
                not over,
                "projeção acima do teto" if over else "dentro do teto",
                f"approve {sid} --raise 50" if over else "",
            )
        )

    decided = [i for i in items if i.ok is not None]
    ok = bool(decided) and all(i.ok for i in decided) and state == DONE
    ctx.audit("dev.checked", {"story": sid, "ok": ok, "failed": [i.label for i in decided if not i.ok]}, ref=sid)
    return {"story": sid, "title": story.title, "state": state, "ok": ok, "items": [i.as_dict() for i in items]}
