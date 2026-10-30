"""Fluxo por papel: ideia, divisão em features, responsáveis, aprovações por história, devolução e aceite."""

from __future__ import annotations

from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError, PolicyViolation
from nexo.kernel.runtime import yamlfile
from nexo.kernel.workspace import specs, tasks as workspace_tasks
from nexo.modules.demands.app import core
from nexo.modules.demands.domain import model as m


def _next(demands: dict[str, m.Demand], prefix: str) -> str:
    return m.merge_conflict_free_id(list(demands), prefix)


# ------------------------------------------------------------------ ideia e features
def idea(ctx: AppContext, text: str, source: str = "") -> dict[str, Any]:
    if not text.strip():
        raise NexoError("descreva a ideia em uma frase")
    actor = core.require_role(ctx, "pm", "Registrar uma ideia")
    demands = core.load(ctx)
    did = _next(demands, "IDEA")
    title = core.clean(text.strip())[:200]
    core.record(
        ctx,
        "demand.created",
        did,
        {
            "type": "idea",
            "title": title,
            "project": core.store(ctx).project,
            "origin": {"kind": "manual", "source": source},
            "responsible": {"pm": actor},
        },
    )
    return {"id": did, "title": title, "stage": "idea"}


def decide(ctx: AppContext, did: str, decision: str, reason: str) -> dict[str, Any]:
    if decision not in ("build", "drop", "investigate"):
        raise NexoError("decisão: build, drop ou investigate")
    if not reason.strip():
        raise NexoError("informe o motivo da decisão (--reason)")
    core.require_role(ctx, "pm", "Decidir uma ideia")
    d, _ = core.get(ctx, did)
    core.record(ctx, "demand.decided", d.id, {"decision": decision, "reason": core.clean(reason)})
    return {"id": d.id, "decision": decision}


def split(ctx: AppContext, did: str, titles: list[str], single: bool = False) -> dict[str, Any]:
    """Divide a ideia em features. Os títulos vêm da pessoa; a sugestão por IA fica em `suggest_split`."""
    core.require_role(ctx, "pm", "Dividir uma ideia")
    d, _demands = core.get(ctx, did)
    if d.type != "idea":
        raise NexoError(f"{d.id} não é uma ideia")
    if not d.decided:
        raise NexoError(f"{d.id} ainda não foi decidida: rode `nexo pm decide {d.id} build --reason ...`")
    names = [d.title] if single else [t.strip() for t in titles if t.strip()]
    if not names:
        raise NexoError("informe as features com --feature, ou use --single para gerar uma só")
    root = core.root_of(ctx)
    created = []
    for name in names:
        fid = specs.next_feature_id(root)
        specs.create_feature_dir(root, fid, name)
        core.record(
            ctx,
            "demand.created",
            fid,
            {
                "type": "feature",
                "title": core.clean(name)[:200],
                "parent": d.id,
                "spawned_by": d.id,
                "project": d.project,
                "origin": {"kind": "planning"},
                "responsible": {"pm": d.responsible.get("pm", "")},
            },
        )
        created.append(fid)
    return {"id": d.id, "features": created}


def adopt_feature(ctx: AppContext, fid: str, title: str) -> None:
    """Feature criada direto pelo planejamento também vira demanda."""
    demands = core.load(ctx, sync=False)
    if fid in demands:
        return
    core.record(
        ctx,
        "demand.created",
        fid,
        {
            "type": "feature",
            "title": core.clean(title)[:200],
            "project": core.store(ctx).project,
            "origin": {"kind": "planning"},
            "responsible": {"pm": core._actor(ctx)},
        },
        push=False,
    )


def adopt_backlog(ctx: AppContext, fid: str, stories: list[dict[str, Any]], task_rows: list[dict[str, Any]]) -> None:
    """Histórias e tarefas viram demandas ligadas à feature, com o mesmo ID para sempre."""
    demands = core.load(ctx, sync=False)
    project = core.store(ctx).project
    for s in stories:
        if s["id"] not in demands:
            core.record(
                ctx,
                "demand.created",
                s["id"],
                {
                    "type": "story",
                    "title": core.clean(str(s.get("title", "")))[:200],
                    "parent": fid,
                    "spawned_by": fid,
                    "project": project,
                    "origin": {"kind": "planning"},
                },
                push=False,
            )
    for t in task_rows:
        if t["id"] not in demands:
            core.record(
                ctx,
                "demand.created",
                t["id"],
                {
                    "type": "task",
                    "title": core.clean(str(t.get("title", "")))[:200],
                    "parent": t.get("story") or fid,
                    "project": project,
                    "origin": {"kind": "planning"},
                },
                push=False,
            )


# ------------------------------------------------------------------ responsáveis
def assign(ctx: AppContext, target: str, dev: str = "", qa: str = "", queue: bool = False) -> dict[str, Any]:
    core.require_role(ctx, "tl", "Indicar responsáveis")
    d, demands = core.get(ctx, target)
    stories = [demands[c] for c in d.children if demands[c].type == "story"] if d.type == "feature" else [d]
    if d.type == "feature" and not stories:
        raise NexoError(f"{d.id} ainda não tem histórias: aprove o design antes (`nexo tl approve {d.id}`)")
    t = core.team(ctx)
    for role, person in (("dev", dev), ("qa", qa)):
        if person and t.declared and role not in t.roles_of(person):
            raise PolicyViolation(f"{person} não tem o papel {role} em team.yaml")
    if t.declared and not t.solo and dev and qa and dev == qa:
        raise PolicyViolation("dev e QA de uma história precisam ser pessoas diferentes")
    changed = []
    for story in stories:
        for role, person in (("dev", dev), ("qa", qa)):
            if person:
                core.record(ctx, "demand.assigned", story.id, {"role": role, "who": person}, push=False)
            elif queue and not story.responsible.get(role):
                core.record(ctx, "demand.assigned", story.id, {"role": role, "who": ""}, push=False)
        changed.append(story.id)
    core.store(ctx).push(f"nexo: responsáveis de {target}")
    return {"stories": changed, "dev": dev or ("fila" if queue else ""), "qa": qa or ("fila" if queue else "")}


def reassign(ctx: AppContext, story: str, role: str, person: str, reason: str) -> dict[str, Any]:
    if not reason.strip():
        raise NexoError("trocar o responsável exige --reason")
    core.require_role(ctx, "tl", "Trocar o responsável")
    t = core.team(ctx)
    if t.declared and role not in t.roles_of(person):
        raise PolicyViolation(f"{person} não tem o papel {role} em team.yaml")
    d, _ = core.get(ctx, story)
    previous = d.responsible.get(role, "")
    core.record(ctx, "demand.assigned", d.id, {"role": role, "who": person, "reason": core.clean(reason)})
    return {"story": d.id, "role": role, "from": previous, "to": person}


def take(ctx: AppContext, story: str, role: str = "dev", force: bool = False) -> dict[str, Any]:
    actor = core.require_role(ctx, role, "Assumir a história")
    d, _ = core.get(ctx, story)
    current = d.responsible.get(role, "")
    if current and current != actor and not force:
        raise PolicyViolation(f"{d.id} está com {current}; use --force, que avisa o tech lead e fica na trilha")
    core.record(ctx, "demand.assigned", d.id, {"role": role, "who": actor, "forced": bool(current)})
    return {"story": d.id, "role": role, "who": actor}


def suggest_assignees(ctx: AppContext, target: str) -> list[dict[str, Any]]:
    """Sugere por dono do código e carga atual, sem IA. A decisão é do tech lead."""
    d, demands = core.get(ctx, target)
    stories = [demands[c] for c in d.children if demands[c].type == "story"] if d.type == "feature" else [d]
    t = core.team(ctx)
    load: dict[str, int] = {}
    for x in demands.values():
        if x.type == "story" and m.stage_of(x, demands) != "done":
            for person in x.responsible.values():
                load[person] = load.get(person, 0) + 1
    least = lambda people: min(people, key=lambda p: load.get(p, 0)) if people else ""  # noqa: E731
    return [
        {
            "story": s.id,
            "title": s.title,
            "dev": s.responsible.get("dev") or least(t.people("dev")),
            "qa": s.responsible.get("qa") or least(t.people("qa")),
            "load": {p: load.get(p, 0) for p in t.people("dev")},
        }
        for s in stories
    ]


# ------------------------------------------------------------------ aprovações por história
def testes_path(ctx: AppContext, story: str) -> Any:
    return core.store(ctx).path("sections", story, "testes.yaml")


def _story_acs(ctx: AppContext, story: str) -> list[dict[str, Any]]:
    root = core.root_of(ctx)
    d, demands = core.get(ctx, story, sync=False)
    parent = demands.get(d.parent)
    if parent is None:
        return []
    folder = specs.feature_dir(root, parent.id)
    spec = specs.load_spec(folder)
    backlog = specs.load_backlog(folder)
    refs: list[str] = []
    if backlog is not None:
        for s in backlog.stories:
            if s.id == d.id:
                refs = list(s.ac_refs)
    return [
        {"id": a.id, "text": _scenario_text(a)}
        for a in spec.acceptance
        if not refs or a.id in refs
    ]


def _scenario_text(a: Any) -> str:
    return " ".join([str(a.title), *a.given, *a.when, *a.then]).strip()


def qa_plan(ctx: AppContext, story: str, scenarios: dict[str, str] | None = None) -> dict[str, Any]:
    """Matriz critério → cenário → tipo de teste. Começa pelos critérios; o QA ajusta."""
    core.require_role(ctx, "qa", "Montar o plano de teste")
    d, _ = core.get(ctx, story)
    acs = _story_acs(ctx, d.id)
    path = testes_path(ctx, d.id)
    data = yamlfile.load(path) if path.is_file() else {}
    rows = {str(r["ac"]): r for r in data.get("matriz") or []}
    for a in acs:
        rows.setdefault(a["id"], {"ac": a["id"], "cenario": a["text"], "tipo": "aceite", "dados": ""})
    for ac, text in (scenarios or {}).items():
        rows.setdefault(ac, {"ac": ac, "tipo": "aceite", "dados": ""})
        rows[ac]["cenario"] = core.clean(text)
    out = {"historia": d.id, "matriz": [rows[k] for k in sorted(rows)]}
    yamlfile.dump(path, out)
    missing = plan_gaps(out, acs)
    core.store(ctx).push(f"nexo: plano de teste de {d.id}")
    return {"story": d.id, "file": str(path), "rows": out["matriz"], "missing": missing}


def plan_gaps(plan: dict[str, Any], acs: list[dict[str, Any]]) -> list[str]:
    """Portão: todo critério da história precisa de um cenário escrito."""
    written = {str(r.get("ac")) for r in plan.get("matriz") or [] if str(r.get("cenario", "")).strip()}
    return [a["id"] for a in acs if a["id"] not in written]


def _digest_of(ctx: AppContext, d: m.Demand, part: str) -> str:
    from nexo.modules.demands.app import sections

    return sections.part_digest(ctx, d, part)


def approve_story(ctx: AppContext, story: str, part: str) -> dict[str, Any]:
    role = m.PART_OWNER[part]
    actor = core.require_role(ctx, role, f"Aprovar {part}")
    d, demands = core.get(ctx, story)
    if d.type != "story":
        raise NexoError(f"{d.id} não é uma história")
    responsible = d.responsible.get(role)
    t = core.team(ctx)
    if t.declared and not t.solo and responsible and responsible != actor:
        raise PolicyViolation(f"{part} de {d.id} é de {responsible}; {actor} não é o responsável")
    if not responsible:
        core.record(ctx, "demand.assigned", d.id, {"role": role, "who": actor}, push=False)
    if part == "testes":
        path = testes_path(ctx, d.id)
        plan = yamlfile.load(path) if path.is_file() else {}
        gaps = plan_gaps(plan, _story_acs(ctx, d.id))
        if not plan or gaps:
            raise PolicyViolation(
                f"plano de teste de {d.id} incompleto: {', '.join(gaps) or 'sem plano'}; rode `nexo qa plan {d.id}`"
            )
    if part == "plano":
        if not [x for x in demands.values() if x.parent == d.id and x.type == "task"]:
            raise PolicyViolation(f"{d.id} não tem tarefas; rode `nexo dev refine {d.id}`")
        holding = d.holding_proposals
        if holding:
            raise PolicyViolation(f"{d.id} tem proposta bloqueante aberta: {', '.join(p.id for p in holding)}")
    core.record(ctx, "demand.approved", d.id, {"part": part, "digest": _digest_of(ctx, d, part)}, push=False)
    result = {"story": d.id, "part": part, "by": actor, "ready": False, "problems": []}
    if part in ("testes", "plano"):
        d, _ = core.get(ctx, story, sync=False)
        if d.approved("testes") and d.approved("plano"):
            result.update(try_ready(ctx, d.id))
    core.store(ctx).push(f"nexo: {part} de {d.id} aprovado")
    return result


def reject_story(ctx: AppContext, story: str, part: str, reason: str) -> dict[str, Any]:
    if not reason.strip():
        raise NexoError("reprovar exige --reason")
    core.require_role(ctx, m.PART_OWNER[part], f"Reprovar {part}")
    d, _ = core.get(ctx, story)
    core.record(ctx, "demand.rejected", d.id, {"part": part, "reason": core.clean(reason)})
    return {"story": d.id, "part": part, "rejected": True}


def try_ready(ctx: AppContext, story: str) -> dict[str, Any]:
    """Testes e plano aprovados: confere a DoR sozinho. Se faltar algo, a demanda volta ao dono do item."""
    from nexo.kernel.errors import NexoError as _NE
    from nexo.modules.planning.app import ready as ready_app

    try:
        ready_app.init(ctx, story)
    except _NE:
        pass  # já existe
    result = ready_app.check(ctx, story)
    return {"ready": bool(result.get("ready")), "problems": list(result.get("problems") or [])}


# ------------------------------------------------------------------ devolução, aceite, triagem
def return_to(ctx: AppContext, did: str, to: str, reason: str) -> dict[str, Any]:
    if to not in m.ROLE_LABEL:
        raise NexoError("devolva para pm, tl, qa ou dev")
    if not reason.strip():
        raise NexoError("devolver exige --reason, com a pergunta")
    d, _ = core.get(ctx, did)
    core.record(ctx, "demand.returned", d.id, {"to": to, "reason": core.clean(reason)})
    return {"id": d.id, "to": to}


def accept(ctx: AppContext, story: str) -> dict[str, Any]:
    core.require_role(ctx, "pm", "Dar o aceite")
    d, demands = core.get(ctx, story)
    stage = m.stage_of(d, demands)
    if stage != "accept":
        raise PolicyViolation(f"{d.id} está na etapa {stage}; o aceite vem depois do merge")
    core.record(ctx, "demand.accepted", d.id)
    return {"story": d.id, "accepted": True}


def triage(ctx: AppContext, did: str, accepted: bool, reason: str = "") -> dict[str, Any]:
    core.require_role(ctx, "tl", "Fazer a triagem")
    d, _ = core.get(ctx, did)
    core.record(ctx, "demand.triaged", d.id, {"accepted": accepted, "reason": core.clean(reason)})
    return {"id": d.id, "accepted": accepted}


def tech_demand(ctx: AppContext, text: str) -> dict[str, Any]:
    """Melhoria técnica sem impacto funcional: entra direto no tech lead, que dá o aceite."""
    actor = core.require_role(ctx, "tl", "Registrar uma melhoria técnica")
    demands = core.load(ctx)
    root = core.root_of(ctx)
    fid = specs.next_feature_id(root)
    specs.create_feature_dir(root, fid, text[:60])
    core.record(
        ctx,
        "demand.created",
        fid,
        {
            "type": "feature",
            "title": core.clean(text)[:200],
            "project": core.store(ctx).project,
            "origin": {"kind": "manual", "track": "tech"},
            "responsible": {"pm": actor, "tl": actor},
        },
    )
    core.record(ctx, "demand.approved", fid, {"part": "requisitos", "digest": "tech"})
    _ = demands
    return {"id": fid}


def new_bug(ctx: AppContext, story: str, text: str, evidence: str = "", origin: str = "qa") -> dict[str, Any]:
    d, demands = core.get(ctx, story)
    prefix = f"BUG-{d.id.removeprefix('US-')}"
    bug = _next(demands, prefix)
    core.record(
        ctx,
        "demand.created",
        bug,
        {
            "type": "bug",
            "title": core.clean(text)[:200],
            "parent": d.id,
            "spawned_by": d.id,
            "project": d.project,
            "state": "Pronto",
            "origin": {"kind": origin, "evidence": core.clean(evidence)},
        },
    )
    return {"id": bug, "story": d.id}


def task_rows(ctx: AppContext) -> list[dict[str, Any]]:
    return [{"id": t.id, "title": t.title, "story": t.story} for t in workspace_tasks.list_tasks(core.root_of(ctx))]
