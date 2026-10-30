"""o tech lead analisa o requisito funcional e escreve o requisito técnico com premissas.

`tech analyze F-n`: a IA (papel `analyst`) aponta lacunas, riscos, o que é afetado e as perguntas para o PM; as
perguntas viram dúvidas da feature (o PM responde no fluxo normal) e a análise fica em `analise-tecnica.md`.

`tech spec F-n`: a IA (papel `architect`) escreve premissas, design, decisões, NFR, riscos, dependências e testes;
o design vai para a spec (a mesma que o dev e os agentes usam) e o documento fica em `requisito-tecnico.md`.
"""

from __future__ import annotations

from typing import Any

from nexo.kernel.errors import NexoError
from nexo.kernel.roles import DESIGN_FIELDS
from nexo.kernel.runtime import yamlfile
from nexo.kernel.spec import questions as q
from nexo.kernel.spec.model import Spec, apply_patch
from nexo.kernel.workspace import specs
from nexo.modules.execution.app import llm
from nexo.modules.knowledge.app import content
from nexo.modules.planning.app.plan import Planner
from nexo.modules.planning.app.schemas import _schema_without
from nexo.modules.planning.domain import techlead as model

ANALYSIS_FILE = "analise-tecnica.yaml"
ANALYSIS_DOC = "analise-tecnica.md"
PREMISES_FILE = "premissas.yaml"
TECH_DOC = "requisito-tecnico.md"
TECH_FIELDS = (*DESIGN_FIELDS, "assumptions")


def _spec_yaml(spec: Spec) -> str:
    import io

    from ruamel.yaml import YAML

    out = io.StringIO()
    YAML().dump(spec.model_dump(mode="json", exclude_defaults=True), out)
    return out.getvalue()


def _answers(qs: list[q.Question]) -> str:
    done = [x for x in qs if x.status == "answered"]
    return "\n".join(f"- {x.text}\n  Resposta: {x.answer}" for x in done) or "- nenhuma"


def analyze(planner: Planner, fid: str) -> dict[str, Any]:
    folder = planner._folder(fid)
    spec = specs.load_spec(folder)
    if not spec.objective and not spec.acceptance:
        raise NexoError(f"{fid} ainda não tem requisito para analisar: refine antes (plan answer {fid})")
    qs = specs.load_questions(folder)
    prompt = content.prompt(planner.ctx, "analyst", fid).format(
        spec=_spec_yaml(spec), answers=_answers(qs), repo_map=planner._repo_overview(spec)
    )
    result = llm.call_structured(
        planner.ctx,
        "analyst",
        fid,
        prompt,
        model.Analysis.model_json_schema(),
        folder,
        facts={"kind": "planning"},
        data_class=spec.data_class or str(planner.project_cfg.get("data_class", "confidential")),
        read_files=True,
        validate=lambda data: _valid(model.Analysis, data),
        ui=planner.ui,
        **planner._route_options(),
    )
    analysis = model.Analysis.model_validate(result.data)
    yamlfile.dump(folder / ANALYSIS_FILE, analysis.model_dump(mode="json"))
    (folder / ANALYSIS_DOC).write_text(model.render_analysis(spec, analysis), encoding="utf-8")
    planner.ctx.audit(
        "tech.analyzed",
        {
            "feature": fid,
            "gaps": len(analysis.gaps),
            "functional_gaps": len(analysis.functional_gaps),
            "ready": analysis.ready_for_tech,
        },
        ref=fid,
    )
    return {
        "feature": fid,
        "analysis": analysis.model_dump(mode="json"),
        "document": str(folder / ANALYSIS_DOC),
        "run_id": result.run_id,
    }


def return_to_pm(planner: Planner, fid: str, reason: str = "") -> dict[str, Any]:
    """Decisão do tech lead: as lacunas funcionais da análise voltam ao PM como dúvidas da feature."""
    folder = planner._folder(fid)
    path = folder / ANALYSIS_FILE
    if not path.is_file():
        raise NexoError(f"{fid} ainda não foi analisada (tech analyze {fid})")
    analysis = model.Analysis.model_validate(yamlfile.load(path))
    if not analysis.functional_gaps:
        raise NexoError(f"a análise de {fid} não tem lacuna funcional para devolver")
    qs = specs.load_questions(folder)
    before = {x.id for x in qs}
    proposed = [
        {"text": g.text, "severity": g.severity, "section": "tech_lead", "source": "tech_lead"}
        for g in analysis.functional_gaps
    ]
    merged = q.merge_new(qs, proposed)
    added = [x for x in merged if x.id not in before]
    specs.save_questions(folder, merged)
    state = planner.store.item_state(fid)
    if added and state == "Pronto" and planner.workflow.can(state, "spec_ambiguity"):
        planner._transition(fid, "spec_ambiguity")  # requisito aprovado com lacuna: volta ao refinamento
    planner.ctx.audit("tech.returned", {"feature": fid, "questions": len(added), "reason": reason}, ref=fid)
    return {"feature": fid, "questions": [x.model_dump() for x in added]}


def _valid(cls: Any, data: dict[str, Any]) -> list[str]:
    try:
        cls.model_validate(data)
    except ValueError as exc:
        return [str(exc).splitlines()[0]]
    return []


def tech_schema() -> dict[str, Any]:
    patch, defs = _schema_without(Spec.model_json_schema(), ())
    patch["properties"] = {k: v for k, v in patch["properties"].items() if k in TECH_FIELDS}
    return {
        "type": "object",
        "$defs": {**defs, "Premise": model.Premise.model_json_schema()},
        "properties": {
            "premises": {"type": "array", "items": {"$ref": "#/$defs/Premise"}},
            "spec_patch": patch,
        },
        "required": ["premises", "spec_patch"],
    }


def tech_spec(planner: Planner, fid: str, notes: str = "") -> dict[str, Any]:
    folder = planner._folder(fid)
    spec = specs.load_spec(folder)
    qs = specs.load_questions(folder)
    blocking = [x for x in qs if x.status == "open" and x.severity == "blocking"]
    if blocking:
        raise NexoError(
            f"{fid} tem {len(blocking)} dúvida(s) bloqueante(s) do requisito: responda antes (plan answer {fid})"
        )
    analysis_path = folder / ANALYSIS_DOC
    analysis = analysis_path.read_text(encoding="utf-8") if analysis_path.is_file() else "- (sem análise)"
    prompt = content.prompt(planner.ctx, "architect", fid).format(
        spec=_spec_yaml(spec),
        analysis=analysis,
        notes=planner._untrusted(folder, "notas do tech lead", notes) if notes else "- nenhuma",
        repo_map=planner._repo_overview(spec),
    )

    def check(data: dict[str, Any]) -> list[str]:
        patch = dict(data.get("spec_patch") or {})
        outside = sorted(k for k in patch if k not in TECH_FIELDS)
        if outside:
            return [f"o requisito técnico não muda o requisito funcional: tire {', '.join(outside)} do spec_patch"]
        try:
            apply_patch(spec, patch)
            [model.Premise.model_validate(p) for p in data.get("premises") or []]
        except ValueError as exc:
            return [str(exc).splitlines()[0]]
        return []

    result = llm.call_structured(
        planner.ctx,
        "architect",
        fid,
        prompt,
        tech_schema(),
        folder,
        facts={"kind": "planning"},
        data_class=spec.data_class or str(planner.project_cfg.get("data_class", "confidential")),
        read_files=True,
        validate=check,
        ui=planner.ui,
        **planner._route_options(),
    )
    premises = [model.Premise.model_validate(p) for p in result.data.get("premises") or []]
    new_spec, changed = apply_patch(spec, dict(result.data.get("spec_patch") or {}))
    stated = [p.text for p in premises if p.text not in new_spec.assumptions]
    if stated:  # premissas também viram suposições da spec: agentes e revisores leem
        new_spec = new_spec.model_copy(update={"assumptions": [*new_spec.assumptions, *stated]})
    specs.save_spec(folder, new_spec)
    yamlfile.dump(folder / PREMISES_FILE, {"premises": [p.model_dump(mode="json") for p in premises]})
    from nexo.modules.planning.app import templates

    rendered = templates.render(  # o mesmo template do requisito técnico que a org ajusta no cérebro
        planner.ctx, "technical-design", templates.design_context(new_spec, templates.premises(folder)), planner.root
    )
    (folder / TECH_DOC).write_text(rendered.text, encoding="utf-8")
    (folder / "design-tecnico.md").unlink(missing_ok=True)
    planner.ctx.audit("tech.spec", {"feature": fid, "premises": len(premises), "sections": sorted(changed)}, ref=fid)
    return {
        "feature": fid,
        "premises": [p.model_dump(mode="json") for p in premises],
        "sections": sorted(changed),
        "document": str(folder / TECH_DOC),
        "run_id": result.run_id,
    }
