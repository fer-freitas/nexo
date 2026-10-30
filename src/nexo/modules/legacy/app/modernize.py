"""Modernização de legado em ondas (F27): `nexo modernize playbooks|plan|status`.

O plano é determinístico (arquivos afetados → módulos → ordem pelos imports → histórias e tarefas de até 8
arquivos); cada história tem o critério "não resta ocorrência do padrão antigo no módulo", que o QA transforma em
teste antes da migração. `status` reconta as ocorrências e mostra o progresso por módulo, sem IA.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.platform import config, flags, identity
from nexo.kernel.runtime import yamlfile
from nexo.kernel.runtime.resources import load_yaml
from nexo.kernel.workspace import codemap, specs
from nexo.modules.legacy.domain.modernize import (
    Playbook,
    chunks,
    matches_include,
    modules,
    occurrences,
    progress,
    waves,
)

STATE_FILE = "modernize.yaml"


def _root(ctx: AppContext) -> Path:
    if ctx.project_root is None:
        raise NexoError("rode dentro do repositório (nexo init)")
    return ctx.project_root


def _brain(ctx: AppContext) -> Path | None:
    return ctx.brain_dir if ctx.brain_dir.is_dir() else None


def playbooks(ctx: AppContext) -> list[dict[str, str]]:
    from nexo.kernel.runtime.resources import embedded_dir

    names = {p.stem for p in embedded_dir("playbooks").glob("*.yaml")}
    brain = _brain(ctx)
    if brain is not None and (brain / "playbooks").is_dir():
        names |= {p.stem for p in (brain / "playbooks").glob("*.yaml")}
    out = []
    for name in sorted(names):
        pb = load_playbook(ctx, name)
        out.append({"name": pb.name, "title": pb.title, "detect": pb.detect})
    return out


def load_playbook(ctx: AppContext, name: str) -> Playbook:
    try:
        data = load_yaml(f"playbooks/{name}.yaml", _brain(ctx))
    except (FileNotFoundError, ValueError) as exc:
        raise NexoError(f"roteiro desconhecido: {name} (veja nexo modernize playbooks)") from exc
    try:
        return Playbook.from_mapping(name, dict(data or {}))
    except Exception as exc:  # regex inválida ou campo faltando
        raise NexoError(str(exc)) from exc


def _affected(root: Path, pb: Playbook) -> dict[str, int]:
    pattern = pb.pattern
    out: dict[str, int] = {}
    for rel in codemap.list_files(root):
        if rel.startswith(".nexo/") or not matches_include(rel, pb.include):
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        n = occurrences(text, pattern)
        if n:
            out[rel] = n
    return out


def plan(
    ctx: AppContext, playbook: str | None = None, find: str | None = None, goal: str = "", include: str = ""
) -> dict[str, Any]:
    flags.require(ctx, "modernize")
    root = _root(ctx)
    if playbook:
        pb = load_playbook(ctx, playbook)
    elif find:
        pb = Playbook.from_mapping(
            "custom",
            {
                "title": goal or f"Remover {find}",
                "detect": find,
                "include": [g.strip() for g in include.split(",") if g.strip()] or ["**/*"],
                "instructions": goal,
            },
        )
    else:
        raise NexoError("informe --playbook ou --find")
    affected = _affected(root, pb)
    if not affected:
        return {"feature": None, "message": f"nenhuma ocorrência de {pb.detect} encontrada"}
    imports = {f.path: f.imports for f in codemap.scan(root) if f.path in affected}
    mods = modules(affected, imports)
    order = waves(mods)

    from nexo.kernel import services

    Planner = services.get("planning.plan", "Planner")
    from nexo.kernel.backlog import Backlog, BacklogTask, Story
    from nexo.kernel.spec.model import Risk, Scenario, Spec

    planner = Planner(ctx)
    fid = specs.next_feature_id(root)
    folder = specs.create_feature_dir(root, fid, f"Modernização {pb.name}")
    n = fid.split("-")[1]
    stacks = list(config.resolved(ctx).values.get("stacks") or [])
    kind = "frontend" if stacks and stacks[0] in ("react", "angular", "flutter") else "backend"
    stories: list[Story] = []
    tasks: list[BacklogTask] = []
    acceptance: list[Scenario] = []
    task_ids: dict[str, list[str]] = {}
    index = 0
    for wave_no, wave in enumerate(order, start=1):
        for name in wave:
            index += 1
            m = mods[name]
            sid, ac = f"US-{n}-{index}", f"AC-{index}"
            acceptance.append(
                Scenario(
                    id=ac,
                    title=f"{name} modernizado",
                    given=[f"o módulo {name} com {m.count} ocorrência(s) de `{pb.detect}`"],
                    when=["a modernização é aplicada ao módulo"],
                    then=[
                        f"não resta ocorrência de `{pb.detect}` em {name}",
                        "os testes existentes e de caracterização continuam passando",
                    ],
                )
            )
            stories.append(
                Story(
                    id=sid,
                    title=f"Modernizar {name} (onda {wave_no})",
                    ac_refs=[ac],
                    points=pb.points,
                    as_a="time de engenharia",
                    i_want=f"{pb.title} em {name}",
                    so_that="o sistema legado evolua sem mudar o comportamento",
                )
            )
            deps = [t for dep in sorted(m.depends_on) for t in task_ids.get(dep, [])]
            ids = []
            for j, files in enumerate(chunks(m.files), start=1):
                tid = f"T-{n}-{index}" if len(m.files) <= 8 else f"T-{n}-{index}-{j}"
                ids.append(tid)
                tasks.append(
                    BacklogTask(
                        id=tid,
                        story=sid,
                        kind=kind,
                        title=f"{pb.title}: {name}" + (f" (parte {j})" if len(m.files) > 8 else ""),
                        description=(
                            f"{pb.instructions}\n\nArquivos com `{pb.detect}`: {', '.join(files)}.\n"
                            "Não mude o comportamento: os testes existentes e de caracterização"
                            " precisam continuar passando."
                        ),
                        acceptance=[f"nenhuma ocorrência de `{pb.detect}` nos arquivos da tarefa"],
                        files_hint=files,
                        depends_on=deps,
                        size="M",
                    )
                )
            task_ids[name] = ids
    total = sum(m.count for m in mods.values())
    spec = Spec(
        id=fid,
        title=f"Modernização: {pb.title}"[:160],
        objective=pb.title,
        problem=f"{total} ocorrência(s) de `{pb.detect}` em {len(mods)} módulo(s)",
        personas=["time de engenharia"],
        scope_in=[f"{name} ({mods[name].count})" for wave in order for name in wave],
        scope_out=["mudança de comportamento", "novas funcionalidades"],
        acceptance=acceptance,
        data_class=str(config.resolved(ctx).values.get("data_class") or "confidential"),
        dependencies_none=True,
        risks=[Risk(area="other", description="mudança ampla em código legado; caracterização antes de mudar")],
    )
    specs.save_spec(folder, spec)
    specs.save_questions(folder, [])
    yamlfile.dump(
        folder / STATE_FILE,
        {
            "playbook": pb.name,
            "title": pb.title,
            "detect": pb.detect,
            "include": list(pb.include),
            "planned_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "baseline": {name: m.count for name, m in mods.items()},
            "waves": order,
        },
    )
    planner.store.upsert_item(fid, "feature", spec.title, planner.project, "local", planner.workflow.initial)
    planner._transition(fid, "plan")
    who = identity.current(ctx)
    ctx.audit(
        "gate.decision",
        {
            "gate": "modernize",
            "feature": fid,
            "decision": "approved",
            "approver": who.id,
            "approver_verified": who.verified,
        },
        ref=fid,
    )
    ctx.audit(
        "modernize.planned",
        {"feature": fid, "playbook": pb.name, "modules": len(mods), "occurrences": total, "waves": len(order)},
        ref=fid,
    )
    report = planner.materialize(folder, spec, Backlog(feature=fid, stories=stories, tasks=tasks))
    return {
        "feature": fid,
        "playbook": pb.name,
        "occurrences": total,
        "waves": order,
        "stories": [{"id": s.id, "title": s.title} for s in stories],
        "plan": report.__dict__,
    }


def status(ctx: AppContext, fid: str) -> dict[str, Any]:
    root = _root(ctx)
    folder = specs.feature_dir(root, fid)
    state = yamlfile.load(folder / STATE_FILE)
    if not state:
        raise NexoError(f"{fid} não é uma modernização")
    pb = Playbook.from_mapping(str(state["playbook"]), {**state, "instructions": ""})
    now_counts: dict[str, int] = {}
    for path, count in _affected(root, pb).items():
        from nexo.modules.legacy.domain.modernize import module_of

        now_counts[module_of(path)] = now_counts.get(module_of(path), 0) + count
    baseline = {str(k): int(v) for k, v in dict(state.get("baseline") or {}).items()}
    rows = [
        {
            "module": name,
            "before": before,
            "now": now_counts.get(name, 0),
            "progress": progress(before, now_counts.get(name, 0)),
        }
        for name, before in baseline.items()
    ]
    total_before, total_now = sum(baseline.values()), sum(now_counts.get(m, 0) for m in baseline)
    return {
        "feature": fid,
        "playbook": state["playbook"],
        "title": state.get("title"),
        "before": total_before,
        "now": total_now,
        "progress": progress(total_before, total_now),
        "modules": rows,
        "new_outside_plan": sorted(set(now_counts) - set(baseline)),
    }
