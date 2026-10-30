"""Propostas e problemas: quem muda a seção de outro propõe; o dono decide. Só o dono altera a própria seção.

Inclui o problema descoberto na construção: a triagem indica o dono por uma tabela fixa, o impacto diz o que pausar,
a premissa provisória só vale pelo tech lead e segura o merge, e a retomada refaz apenas o afetado.
"""

from __future__ import annotations

import re
from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError, PolicyViolation
from nexo.kernel.runtime import yamlfile
from nexo.kernel.workspace import tasks as workspace_tasks
from nexo.modules.demands.app import core, flow, sections
from nexo.modules.demands.domain import impact as impact_mod
from nexo.modules.demands.domain import model as m

AC_ID = re.compile(r"\bAC-\d+\b")
KINDS = ("task", "technical", "functional", "test", "other")
ROUTE = {"task": "dev", "technical": "tl", "functional": "pm", "test": "qa"}


def _next_id(demands: dict[str, m.Demand]) -> str:
    numbers = [int(p.id[2:]) for d in demands.values() for p in d.proposals if p.id[2:].isdigit()]
    return f"P-{max(numbers, default=0) + 1}"


def _find(demands: dict[str, m.Demand], pid: str) -> tuple[m.Demand, m.Proposal]:
    for d in demands.values():
        for p in d.proposals:
            if p.id == pid:
                return d, p
    raise NexoError(f"proposta desconhecida: {pid} (veja `nexo proposals`)")


def suggest(ctx: AppContext, did: str, section: str, text: str, blocking: bool = False) -> dict[str, Any]:
    if section not in m.SECTION_OWNER:
        raise NexoError(f"seção inválida: {section} ({', '.join(m.SECTION_OWNER)})")
    if not text.strip():
        raise NexoError("descreva a mudança proposta")
    d, demands = core.get(ctx, did)
    pid = _next_id(demands)
    core.record(
        ctx,
        "proposal.opened",
        d.id,
        {"id": pid, "section": section, "text": core.clean(text)[:800], "blocking": blocking},
    )
    return {"id": pid, "demand": d.id, "owner": m.SECTION_OWNER[section], "blocking": blocking}


def listing(ctx: AppContext, mine: bool = False) -> list[dict[str, Any]]:
    demands = core.load(ctx)
    me = core._actor(ctx)
    t = core.team(ctx)
    roles = set(t.roles_of(me)) if t.declared else set(m.ROLE_LABEL)
    rows = []
    for d in demands.values():
        for p in d.proposals:
            if not p.open or (mine and p.owner_role not in roles):
                continue
            rows.append(
                {
                    "id": p.id,
                    "demand": d.id,
                    "section": p.section,
                    "owner": p.owner_role,
                    "status": p.status,
                    "blocking": p.blocking,
                    "text": p.text,
                    "by": p.by,
                }
            )
    return rows


def _part_for(section: str) -> str:
    return m.SECTION_PART[section]


def _target(d: m.Demand, section: str, demands: dict[str, m.Demand]) -> m.Demand:
    """Funcional e técnica são da feature; testes e implementação, da história."""
    if section in ("funcional", "tecnica"):
        feature = sections._feature_of(d, demands)
        return feature or d
    return d


def _invalidate(ctx: AppContext, d: m.Demand, section: str, demands: dict[str, m.Demand]) -> list[str]:
    """Aceitar muda a seção: só a aprovação dela fica desatualizada, e o QA é avisado se o critério mudou."""
    target = _target(d, section, demands)
    part = _part_for(section)
    touched = []
    if part in target.approvals:
        core.record(ctx, "demand.invalidated", target.id, {"part": part, "section": section}, push=False)
        touched.append(f"{target.id}:{part}")
    if section == "funcional":
        for story in [demands[c] for c in target.children if c in demands and demands[c].type == "story"]:
            if "testes" in story.approvals:
                core.record(ctx, "demand.invalidated", story.id, {"part": "testes", "section": section}, push=False)
                touched.append(f"{story.id}:testes")
    return touched


def decide(ctx: AppContext, pid: str, accepted: bool, reason: str = "") -> dict[str, Any]:
    demands = core.load(ctx)
    d, p = _find(demands, pid)
    if not p.open:
        raise NexoError(f"{pid} já foi decidida ({p.status})")
    actor = core.require_role(ctx, p.owner_role, f"Decidir {pid}")
    if not accepted and not reason.strip():
        raise NexoError("recusar exige --reason, que volta para quem propôs")
    touched: list[str] = []
    if accepted:
        target = _target(d, p.section, demands)
        sections.add_change(
            ctx, p.section, target.id, {"proposal": pid, "text": p.text, "by": p.by, "accepted_by": actor}
        )
        touched = _invalidate(ctx, d, p.section, demands)
    core.record(
        ctx,
        "proposal.decided",
        d.id,
        {"id": pid, "status": "accepted" if accepted else "declined", "reason": core.clean(reason)},
    )
    return {"id": pid, "status": "accepted" if accepted else "declined", "invalidated": touched}


# ------------------------------------------------------------------ problema na construção
def _task_refs(ctx: AppContext, story: str, demands: dict[str, m.Demand]) -> list[impact_mod.TaskRef]:
    refs = []
    root = core.root_of(ctx)
    declared = {t.id: t for t in workspace_tasks.list_tasks(root) if t.story == story}
    for x in demands.values():
        if x.parent == story and x.type == "task":
            t = declared.get(x.id)
            refs.append(
                impact_mod.TaskRef(
                    id=x.id,
                    ac_refs=tuple(sorted({a for text in t.acceptance for a in AC_ID.findall(text)})) if t else (),
                    section_refs=tuple(getattr(t, "section_refs", ()) or ()) if t else (),
                    files=tuple(t.files_hint) if t else (),
                    done=x.state == "Concluido",
                )
            )
    return refs


def compute_impact(
    ctx: AppContext, story: str, acs: list[str] | None = None, refs: list[str] | None = None, files: list[str] | None = None
) -> impact_mod.Impact:
    demands = core.load(ctx, sync=False)
    return impact_mod.compute(_task_refs(ctx, story, demands), acs or [], refs or [], files or [])


def problem(
    ctx: AppContext,
    story: str,
    kind: str,
    text: str,
    evidence: str = "",
    acs: list[str] | None = None,
    refs: list[str] | None = None,
    files: list[str] | None = None,
    origin: str = "person",
) -> dict[str, Any]:
    """Abre o problema e indica o dono por tabela. A classificação do agente é só uma sugestão."""
    if kind not in KINDS:
        raise NexoError(f"tipo inválido: {kind} ({', '.join(KINDS)})")
    if not text.strip():
        raise NexoError("descreva o problema")
    d, demands = core.get(ctx, story)
    if kind == "other":
        bug = flow.new_bug(ctx, d.id, text, evidence, origin="followup" if origin == "agent" else "manual")
        if origin == "agent":
            core.record(ctx, "demand.triaged", bug["id"], {"accepted": True, "note": "aceita por padrão: sem IA"})
        return {"kind": kind, "demand": bug["id"], "owner": "tl", "blocks": False}
    pid = _next_id(demands)
    if kind == "task":
        core.record(
            ctx,
            "proposal.opened",
            d.id,
            {"id": pid, "section": "implementacao", "text": core.clean(text)[:800], "kind": kind, "blocking": False},
        )
        core.record(ctx, "problem.opened", d.id, {"id": pid, "kind": kind})
        return {"kind": kind, "proposal": pid, "owner": "dev", "blocks": False, "affected": [], "uncertain": False}
    effect = compute_impact(ctx, d.id, acs, refs, files)
    section = m.KIND_SECTION[kind]
    core.record(
        ctx,
        "proposal.opened",
        d.id,
        {
            "id": pid,
            "section": section,
            "text": core.clean(text)[:800],
            "kind": kind,
            "blocking": True,
            "affected": effect.affected,
        },
        push=False,
    )
    core.record(
        ctx,
        "problem.opened",
        d.id,
        {"id": pid, "kind": kind, "evidence": core.clean(evidence), "origin": origin, "uncertain": effect.uncertain},
    )
    return {
        "kind": kind,
        "proposal": pid,
        "owner": ROUTE[kind],
        "blocks": True,
        "affected": effect.affected,
        "continue": effect.unaffected,
        "kept": effect.done_kept,
        "uncertain": effect.uncertain,
        "reasons": effect.reasons,
    }


def reclassify(ctx: AppContext, pid: str, kind: str) -> dict[str, Any]:
    if kind not in m.KIND_SECTION:
        raise NexoError(f"reclassifique para {', '.join(m.KIND_SECTION)}")
    demands = core.load(ctx)
    d, p = _find(demands, pid)
    core.record(ctx, "proposal.decided", d.id, {"id": pid, "status": "declined", "reason": f"reclassificada: {kind}"})
    new_id = _next_id(demands)
    core.record(
        ctx,
        "proposal.opened",
        d.id,
        {
            "id": new_id,
            "section": m.KIND_SECTION[kind],
            "text": p.text,
            "kind": kind,
            "blocking": True,
            "affected": p.affected,
        },
    )
    return {"from": pid, "to": new_id, "owner": ROUTE[kind]}


def assume(ctx: AppContext, pid: str, reason: str) -> dict[str, Any]:
    """Premissa provisória: só o tech lead libera. A IA nunca assume, e o merge fica retido até o dono decidir."""
    if not reason.strip():
        raise NexoError("assumir exige --reason")
    actor = core.require_role(ctx, "tl", "Assumir uma premissa provisória")
    if actor.startswith("agent:"):
        raise PolicyViolation("a IA nunca assume uma premissa")
    demands = core.load(ctx)
    d, p = _find(demands, pid)
    if p.status != "open":
        raise NexoError(f"{pid} não está aberta ({p.status})")
    if _sensitive(ctx, d, demands):
        raise PolicyViolation(f"{d.id} é de área sensível ou dado restrito: premissa provisória não é permitida")
    core.record(ctx, "proposal.decided", d.id, {"id": pid, "status": "assumed", "reason": core.clean(reason)})
    return {"id": pid, "status": "assumed", "merge_held": True}


def _sensitive(ctx: AppContext, d: m.Demand, demands: dict[str, m.Demand]) -> bool:
    from nexo.kernel.workspace import specs

    feature = sections._feature_of(d, demands)
    if feature is None:
        return False
    try:
        spec = specs.load_spec(specs.feature_dir(core.root_of(ctx), feature.id))
    except NexoError:
        return False
    risky = {"auth", "payments", "personal_data", "migration", "infra"}
    return spec.data_class == "restricted" or any(r.area in risky for r in spec.risks)


def confirm(ctx: AppContext, pid: str, accepted: bool, reason: str = "") -> dict[str, Any]:
    """O dono decide a premissa. Recusar reabre o que dependia dela."""
    demands = core.load(ctx)
    d, p = _find(demands, pid)
    if p.status != "assumed":
        raise NexoError(f"{pid} não é uma premissa aberta ({p.status})")
    actor = core.require_role(ctx, p.owner_role, f"Decidir a premissa {pid}")
    if not accepted and not reason.strip():
        raise NexoError("recusar exige --reason")
    if accepted:
        target = _target(d, p.section, demands)
        sections.add_change(
            ctx, p.section, target.id, {"proposal": pid, "text": p.text, "by": p.by, "accepted_by": actor}
        )
        _invalidate(ctx, d, p.section, demands)
        reopened: list[str] = []
    else:
        reopened = list(p.affected)
    core.record(
        ctx,
        "proposal.decided",
        d.id,
        {"id": pid, "status": "accepted" if accepted else "declined", "reason": core.clean(reason)},
    )
    return {"id": pid, "status": "accepted" if accepted else "declined", "reopened": reopened}


# ------------------------------------------------------------------ spec congelada
def lock_path(ctx: AppContext, story: str, version: int) -> Any:
    return core.store(ctx).path("locks", story, f"v{version}.yaml")


def freeze(ctx: AppContext, story: str) -> dict[str, Any]:
    """Congela o que foi aprovado. Versão nova só quando algo mudou, sem apagar a anterior."""
    d, _ = core.get(ctx, story, sync=False)
    snap = sections.snapshot(ctx, d.id)
    if d.lock:
        previous = yamlfile.load(lock_path(ctx, d.id, d.lock))
        if previous.get("digests") == snap["digests"] and previous.get("provisional") == snap["provisional"]:
            return {"story": d.id, "version": d.lock, "changed": False}
    version = d.lock + 1
    yamlfile.dump(lock_path(ctx, d.id, version), {"version": version, **snap})
    core.record(ctx, "demand.locked", d.id, {"version": version})
    return {"story": d.id, "version": version, "changed": True}


def lock_current(ctx: AppContext, story: str) -> list[str]:
    """Portão: o que foi aprovado ainda é o que está valendo? Retorna os problemas."""
    d, _ = core.get(ctx, story, sync=False)
    problems = []
    if not d.lock:
        problems.append(f"{d.id} não tem spec congelada")
    else:
        locked = yamlfile.load(lock_path(ctx, d.id, d.lock))
        if locked.get("digests") != sections.snapshot(ctx, d.id)["digests"]:
            problems.append(f"{d.id}: a spec mudou depois da versão v{d.lock}; reaprove e retome")
    if d.open_premises:
        problems.append(f"{d.id}: premissa provisória aberta: {', '.join(p.id for p in d.open_premises)}")
    if d.holding_proposals:
        problems.append(f"{d.id}: proposta bloqueante aberta: {', '.join(p.id for p in d.holding_proposals)}")
    return problems


def prepare_run(ctx: AppContext, story: str, force: bool = False) -> dict[str, Any]:
    """Antes de construir: dev indicado, sem bloqueio, spec congelada. Só vale para demandas conhecidas."""
    demands = core.load(ctx)
    d = demands.get(story)
    if d is None or d.type != "story":
        return {"known": False}
    me = core._actor(ctx)
    t = core.team(ctx)
    assigned = d.responsible.get("dev")
    if t.declared and not t.solo and assigned and assigned != me and not force:
        raise PolicyViolation(f"{d.id} está com {assigned}; use `nexo dev take {d.id} --force`, que avisa o tech lead")
    if d.holding_proposals:
        ids = ", ".join(p.id for p in d.holding_proposals)
        raise PolicyViolation(f"{d.id} aguarda decisão: {ids}. Veja `nexo demand why {d.id}`")
    if t.declared and not (d.approved("testes") and d.approved("plano")):
        raise PolicyViolation(f"{d.id} ainda não está pronta: faltam as aprovações de testes e plano")
    return {"known": True, **freeze(ctx, d.id)}


def resume_plan(ctx: AppContext, story: str) -> dict[str, Any]:
    """O que a retomada faria: da versão anterior da spec para a atual, só o afetado."""
    d, demands = core.get(ctx, story)
    decided = [p for p in d.proposals if p.status in ("accepted", "assumed") and p.affected]
    affected = sorted({t for p in decided for t in p.affected})
    tasks = [x for x in demands.values() if x.parent == d.id and x.type == "task"]
    return {
        "story": d.id,
        "blocked_by": [p.id for p in d.holding_proposals],
        "redo": affected,
        "done_kept": [t.id for t in tasks if t.state == "Concluido" and t.id not in affected],
        "from_lock": d.lock,
        "what_changed": [
            {"section": p.section, "text": p.text, "why": p.id, "status": p.status} for p in decided
        ],
        "do_not_touch": "testes bloqueados dos critérios que não mudaram",
    }


def reopen_affected(ctx: AppContext, tasks: list[str]) -> None:
    """Só o afetado volta a rodar. O restante continua concluído."""
    from nexo.store.runs import RunStore, project_key

    runs = RunStore(ctx.conn, project_key(core.root_of(ctx)))
    for task in tasks:
        if runs.item_state(task) == "Concluido":
            runs.set_item_state(task, "Pronto")
        core.record(ctx, "demand.state", task, {"state": "Pronto", "reason": "mudança na spec"}, push=False)
    core.store(ctx).push("nexo: tarefas reabertas pela mudança na spec")


def handoff_context(ctx: AppContext, story: str) -> str:
    """Texto para a sessão nova do agente: o que mudou, sem a conversa antiga."""
    plan = resume_plan(ctx, story)
    if not plan["what_changed"]:
        return ""
    lines = [f"A spec de {plan['story']} mudou. Siga a versão vigente, não decisões antigas.", "", "O que mudou:"]
    lines += [f"- [{c['section']}] {c['text']} ({c['why']}, {c['status']})" for c in plan["what_changed"]]
    if plan["redo"]:
        lines += ["", "Refazer: " + ", ".join(plan["redo"])]
    if plan["done_kept"]:
        lines += ["Já prontas, manter: " + ", ".join(plan["done_kept"])]
    lines += ["Não altere: " + str(plan["do_not_touch"])]
    return "\n".join(lines)
