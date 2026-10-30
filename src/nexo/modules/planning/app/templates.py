"""Templates de artefatos: resolução em cascata (baseline → cérebro da org → squad → projeto), contextos a
partir dos modelos, documentos da feature, texto para o tracker e checagem de DoR por seções obrigatórias."""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nexo.kernel.backlog import Backlog, BacklogTask, Story
from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.platform import config
from nexo.kernel.runtime.resources import read_text
from nexo.kernel.spec.model import Decision, Readiness, ReadinessItem, Spec
from nexo.modules.planning.domain.templates.model import Template, override_problems, parse, sections

FILES = {
    "epic": "epic.md",
    "feature": "feature.md",
    "sdd": "sdd.md",
    "functional-requirements": "requisitos.md",
    "technical-design": "design-tecnico.md",  # nome do arquivo de template (o cérebro sobrescreve por ele)
    "story": "story.md",
    "task": "task.md",
    "bug": "bug.md",
    "adr": "adr.md",
}
DOC_NAMES = {
    "sdd": "Spec (SDD)",
    "feature": "Feature",
    "epic": "Épico",
    "functional-requirements": "Requisitos funcionais",
    "technical-design": "Requisito técnico",
}
DOC_PATHS = {
    "sdd": "spec.md",
    "feature": "feature.md",
    "epic": "epic.md",
    "functional-requirements": "requisitos.md",
    "technical-design": "requisito-tecnico.md",
}


def _squad(ctx: AppContext) -> str | None:
    value = config.resolved(ctx).values.get("squad")
    return str(value) if value else None


def _layers(ctx: AppContext, root: Path | None) -> list[tuple[str, Path]]:
    layers: list[tuple[str, Path]] = []
    if ctx.brain_dir.is_dir():
        layers.append(("org", ctx.brain_dir / "templates"))
        squad = _squad(ctx)
        if squad:
            layers.append((f"squad:{squad}", ctx.brain_dir / "squads" / squad / "templates"))
    base = root or ctx.project_root
    if base is not None:
        layers.append(("projeto", base / ".nexo" / "templates"))
    return layers


def resolve(ctx: AppContext, kind: str, root: Path | None = None) -> Template:
    """Template efetivo: cada nível acima substitui o anterior, desde que seja válido e mantenha as seções
    travadas; senão é ignorado (auditado) e vale o anterior."""
    if kind not in FILES:
        raise NexoError(f"tipo de template desconhecido: {kind} ({', '.join(FILES)})")
    current = parse(read_text(f"templates/{FILES[kind]}"), "embutido", kind)
    for origin, folder in _layers(ctx, root):
        path = folder / FILES[kind]
        if not path.is_file():
            continue
        candidate = parse(path.read_text(encoding="utf-8"), origin, kind)
        problems = candidate.problems + override_problems(current, candidate)
        if problems:
            ctx.audit("template.rejected", {"kind": kind, "origin": origin, "problems": problems[:5]})
            continue
        current = candidate
    return current


def listing(ctx: AppContext) -> list[dict[str, Any]]:
    out = []
    for kind in FILES:
        tpl = resolve(ctx, kind)
        out.append(
            {
                "type": kind,
                "origin": tpl.origin,
                "version": tpl.header.get("version"),
                "required": tpl.required,
                "locked": tpl.locked,
            }
        )
    return out


def validate_file(ctx: AppContext, path: Path, kind: str | None = None) -> list[str]:
    tpl = parse(path.read_text(encoding="utf-8"), str(path), kind)
    problems = list(tpl.problems)
    if tpl.type in FILES:
        base = parse(read_text(f"templates/{FILES[tpl.type]}"), "embutido", tpl.type)
        problems += override_problems(base, tpl)
    return problems


# ---------------------------------------------------------------- contextos


def _join(parts: list[str]) -> str:
    return " E ".join(p for p in parts if p)


def _ac(spec: Spec) -> list[dict[str, Any]]:
    by_ac: dict[str, list[str]] = {}
    for r in spec.requirements:
        for a in r.acceptance:
            by_ac.setdefault(a, []).append(r.id)
    return [
        {
            "id": a.id,
            "title": a.title,
            "given": _join(a.given),
            "when": _join(a.when),
            "then": _join(a.then),
            "requirements": ", ".join(by_ac.get(a.id, [])),  # texto: seção sobre lista repetiria a linha
        }
        for a in spec.acceptance
    ]


def sdd_context(spec: Spec, score: int, questions: list[Any], as_built: bool = False) -> dict[str, Any]:
    data = spec.model_dump(mode="json")
    data.update(
        {
            "readiness": score,
            "as_built": as_built,
            "acceptance": _ac(spec),
            "nfr": [{"category": k, "requirement": v} for k, v in spec.nfr.items()],
            "test_strategy": [{"kind": k, "approach": v} for k, v in spec.test_strategy.items()],
            "questions": [{"id": x.id, "status": x.status, "text": x.text} for x in questions],
        }
    )
    return data


def requirements_context(spec: Spec, backlog: Backlog | None) -> dict[str, Any]:
    stories_by_ac: dict[str, list[str]] = {}
    for st in backlog.stories if backlog else []:
        for a in st.ac_refs:
            stories_by_ac.setdefault(a, []).append(st.id)
    trace = [
        {
            "rf": r.id,
            "acs": ", ".join(r.acceptance) or "—",
            "stories": ", ".join(sorted({s for a in r.acceptance for s in stories_by_ac.get(a, [])})),
        }
        for r in spec.requirements
    ]
    return {
        "feature": f"{spec.id} — {spec.title}",
        "requirements": [
            {**r.model_dump(mode="json"), "rules": ", ".join(r.rules), "acceptance": ", ".join(r.acceptance)}
            for r in spec.requirements
        ],
        "rules": [r.model_dump(mode="json") for r in spec.rules],
        "trace": trace,
    }


def design_context(spec: Spec, premises: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    design = spec.design.model_dump(mode="json") if spec.design else {}
    return {
        **design,
        "premises": premises if premises is not None else [{"text": a} for a in spec.assumptions],
        "feature": f"{spec.id} — {spec.title}",
        "mode": "to-be",
        "data_class": spec.data_class,
        "contracts": [c.model_dump(mode="json") for c in spec.contracts],
    }


def premises(folder: Path) -> list[dict[str, Any]] | None:
    """Premissas do requisito técnico (`premissas.yaml`, com por quê e o que muda se não se confirmar)."""
    from nexo.kernel.runtime import yamlfile

    path = folder / "premissas.yaml"
    if not path.is_file():
        return None
    return [dict(p) for p in yamlfile.load(path).get("premises") or [] if isinstance(p, dict)]


def story_context(story: Story, spec: Spec, backlog: Backlog) -> dict[str, Any]:
    by_ac = {a["id"]: a for a in _ac(spec)}
    return {
        **story.model_dump(mode="json"),
        "feature": spec.id,
        "acceptance": [by_ac[a] for a in story.ac_refs if a in by_ac],
        "tasks": [t.model_dump(mode="json") for t in backlog.tasks if t.story == story.id],
    }


def task_context(task: BacklogTask) -> dict[str, Any]:
    return task.model_dump(mode="json")


def adr_context(decision: Decision, number: int, spec: Spec, status: str = "aceita") -> dict[str, Any]:
    return {
        **decision.model_dump(mode="json"),
        "number": f"{number:04d}",
        "status": status,
        "date": _dt.date.today().isoformat(),
        "feature": spec.id,
    }


# ---------------------------------------------------------------- renderização


@dataclass
class Rendered:
    kind: str
    text: str
    missing: list[str]
    template: Template

    def section(self, sid: str) -> str:
        return sections(self.text).get(sid, "")

    def tracker_description(self) -> str:
        """Seções escolhidas pelo template para a descrição do item no tracker (com os títulos)."""
        wanted = list((self.template.header.get("tracker") or {}).get("description") or [])
        found = sections(self.text)
        titles = _titles(self.text)
        parts = [f"## {titles.get(s, s)}\n{found[s]}" for s in wanted if found.get(s)]
        return "\n\n".join(parts) if parts else self.text

    def tracker_acceptance(self) -> str | None:
        sid = (self.template.header.get("tracker") or {}).get("acceptance")
        return self.section(str(sid)) if sid else None


def _titles(markdown: str) -> dict[str, str]:
    import re

    from nexo.modules.planning.domain.templates.model import slug

    return {slug(m.group(1)): m.group(1) for m in re.finditer(r"(?m)^##\s+(.+?)\s*$", markdown)}


def render(ctx: AppContext, kind: str, data: dict[str, Any], root: Path | None = None) -> Rendered:
    tpl = resolve(ctx, kind, root)
    text = tpl.render(data)
    return Rendered(kind, text, tpl.missing(text), tpl)


# ---------------------------------------------------------------- documentos da feature


@dataclass
class FeatureDocs:
    paths: dict[str, str] = field(default_factory=dict)
    missing: dict[str, list[str]] = field(default_factory=dict)
    sdd: Rendered | None = None


def required_documents(ctx: AppContext) -> list[str]:
    planning = dict(config.resolved(ctx).values.get("planning") or {})
    return [str(d) for d in planning.get("required_documents") or ["sdd"]]


def write_feature_docs(
    ctx: AppContext,
    folder: Path,
    spec: Spec,
    questions: list[Any],
    score: int,
    backlog: Backlog | None = None,
    states: dict[str, str] | None = None,
    as_built: bool = False,
    root: Path | None = None,
) -> FeatureDocs:
    """Gera SDD (`spec.md`), feature, requisitos, design técnico, épico e ADRs pelos templates efetivos."""
    docs = FeatureDocs()
    sdd = render(ctx, "sdd", sdd_context(spec, score, questions, as_built), root)
    docs.sdd = sdd
    outputs: dict[str, Rendered] = {"sdd": sdd}
    if spec.requirements or "functional-requirements" in required_documents(ctx):
        outputs["functional-requirements"] = render(
            ctx, "functional-requirements", requirements_context(spec, backlog), root
        )
    if spec.design or spec.needs_design or "technical-design" in required_documents(ctx):
        outputs["technical-design"] = render(ctx, "technical-design", design_context(spec, premises(folder)), root)
        (folder / "design-tecnico.md").unlink(missing_ok=True)  # 0.54: o requisito técnico substitui o design técnico
    if spec.epic:
        epic = spec.epic.model_dump(mode="json")
        epic["id"] = epic["id"] or f"E-{spec.id.split('-')[-1]}"  # id determinístico quando o LLM não informa
        epic["features"] = [{"id": spec.id, "title": spec.title, "state": (states or {}).get(spec.id, "Refinamento")}]
        outputs["epic"] = render(ctx, "epic", epic, root)
    adr_paths = []
    for i, decision in enumerate(spec.decisions, start=1):
        from nexo.kernel.workspace.specs import slug as path_slug

        rel = f"adr/{i:04d}-{path_slug(decision.title)}.md"
        rendered = render(ctx, "adr", adr_context(decision, i, spec), root)
        (folder / "adr").mkdir(exist_ok=True)
        (folder / rel).write_text(rendered.text, encoding="utf-8")
        adr_paths.append(rel)
    documents = [{"name": DOC_NAMES[k], "path": DOC_PATHS[k]} for k in outputs]
    documents += [{"name": "Decisão", "path": p} for p in adr_paths]
    feature_data = spec.model_dump(mode="json")
    feature_data.update(
        {
            "readiness": score,
            "as_built": as_built,
            "documents": documents,
            "stories": [
                {**s.model_dump(mode="json"), "state": (states or {}).get(s.id, "Pronto")}
                for s in (backlog.stories if backlog else [])
            ],
        }
    )
    outputs["feature"] = render(ctx, "feature", feature_data, root)
    for kind, rendered in outputs.items():
        (folder / DOC_PATHS[kind]).write_text(rendered.text, encoding="utf-8")
        docs.paths[kind] = DOC_PATHS[kind]
        if rendered.missing:
            docs.missing[kind] = rendered.missing
    docs.paths.update({f"adr:{i}": p for i, p in enumerate(adr_paths, start=1)})
    return docs


def with_template_items(ctx: AppContext, base: Readiness, spec: Spec, docs: FeatureDocs) -> Readiness:
    """DoR dos templates entra na prontidão: seção obrigatória vazia nos documentos exigidos pela org e
    requisito must/should sem critério de aceite."""
    items = list(base.items)
    required = set(required_documents(ctx))
    for kind, missing in docs.missing.items():
        if kind in required or kind == "sdd":
            for sid in missing:
                items.append(
                    ReadinessItem(
                        f"template:{kind}:{sid}",
                        f"{DOC_NAMES.get(kind, kind)}: seção obrigatória vazia ({sid})",
                        0,  # bloqueia o "pronto" sem mudar a nota calculada pelo planejamento
                        False,
                        f"preencha a seção {sid} (template {kind})",
                    )
                )
    for kind in (required & set(DOC_PATHS)) - set(docs.paths):  # história/tarefa são checadas no breakdown
        items.append(
            ReadinessItem(f"template:{kind}", f"{DOC_NAMES.get(kind, kind)} obrigatório", 0, False, f"gere {kind}")
        )
    acs = {a.id for a in spec.acceptance}
    loose = [r.id for r in spec.requirements if r.priority in ("must", "should") and not set(r.acceptance) & acs]
    if spec.requirements:
        items.append(
            ReadinessItem(
                "requirements_trace",
                "Requisitos must/should ligados a critérios de aceite",
                0,
                not loose,
                "ligue " + ", ".join(loose) + " a pelo menos um AC-n" if loose else "",
            )
        )
    return Readiness(tuple(items), base.blocking_questions)


def backlog_problems(ctx: AppContext, backlog: Backlog, spec: Spec) -> list[str]:
    """DoR de histórias e tarefas pelos templates (quando a org exige `story`/`task` em
    `planning.required_documents`): devolvidas ao LLM do breakdown para corrigir."""
    required = set(required_documents(ctx))
    problems: list[str] = []
    if not required & {"story", "task"}:
        return problems
    for story in backlog.stories if "story" in required else []:
        missing = render(ctx, "story", story_context(story, spec, backlog)).missing
        problems += [f"história {story.id}: seção obrigatória vazia ({s})" for s in missing]
    for task in backlog.tasks if "task" in required else []:
        missing = render(ctx, "task", task_context(task)).missing
        problems += [f"tarefa {task.id}: seção obrigatória vazia ({s})" for s in missing]
    return problems


def preview(ctx: AppContext, kind: str, feature: str, item: str | None = None) -> Rendered:
    from nexo.kernel.workspace import specs

    if ctx.project_root is None:
        raise NexoError("nenhum projeto Nexo encontrado")
    folder = specs.feature_dir(ctx.project_root, feature)
    spec = specs.load_spec(folder)
    backlog = specs.load_backlog(folder)
    qs = specs.load_questions(folder)
    if kind in ("story", "task"):
        if backlog is None or item is None:
            raise NexoError(f"{kind} exige feature aprovada e --item")
        if kind == "story":
            story = next((s for s in backlog.stories if s.id == item), None)
            if story is None:
                raise NexoError(f"história {item} não encontrada")
            return render(ctx, kind, story_context(story, spec, backlog))
        task = next((t for t in backlog.tasks if t.id == item), None)
        if task is None:
            raise NexoError(f"tarefa {item} não encontrada")
        return render(ctx, kind, task_context(task))
    data = {
        "sdd": lambda: sdd_context(spec, 0, qs),
        "functional-requirements": lambda: requirements_context(spec, backlog),
        "technical-design": lambda: design_context(spec),
        "epic": lambda: spec.epic.model_dump(mode="json") if spec.epic else {},
        "feature": lambda: spec.model_dump(mode="json"),
    }.get(kind)
    if data is None:
        raise NexoError(
            f"preview de {kind} não disponível"
            " (use sdd, functional-requirements, technical-design, epic, feature, story, task)"
        )
    return render(ctx, kind, data())
