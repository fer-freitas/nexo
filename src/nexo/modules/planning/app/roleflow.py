"""Fluxo por papéis e discovery: sem trabalho manual entre as etapas.

Discovery do PM → decisão de construir (o discovery vira insumo do refinamento, sem cópia) → sessão conjunta
PM + tech lead + dev com a IA (exigida por risco e tamanho) → pacote único (requisitos, design/SDD e plano) →
cada papel aprova a sua parte → backlog, tracker e documentos gerados, e o dev avisado com o SDD.

Papéis (`roles.pm|tech_lead|dev`) e política (`planning.role_flow`) só valem da org ou do squad no cérebro.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexo.kernel import roles as r
from nexo.kernel.backlog import Backlog
from nexo.kernel.config.cascade import resolve
from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError, PolicyViolation
from nexo.kernel.platform import config, flags, identity
from nexo.kernel.runtime import yamlfile
from nexo.kernel.spec.model import Spec
from nexo.kernel.workspace import specs
from nexo.modules.execution.app import llm
from nexo.modules.knowledge.app import content
from nexo.modules.notify.app import notify

TRUSTED_LEVELS = ("default", "org", "squad")
ROLES_FILE = "roles.yaml"
DRAFT_FILE = "plan.draft.yaml"
DISCOVERY_FILE = "discovery.yaml"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


# ------------------------------------------------------------------ política e papéis


def _trusted(ctx: AppContext) -> dict[str, Any]:
    """Valores só de padrão, org e squad: projeto e config pessoal não mudam papéis nem política."""
    layers = [layer for layer in config.load_layers(ctx) if layer.level in TRUSTED_LEVELS]
    return resolve(layers).values


def _team(ctx: AppContext) -> Any:
    from nexo.kernel import team

    return team.load(ctx.project_root)


def policy(ctx: AppContext) -> dict[str, Any]:
    return dict((_trusted(ctx).get("planning") or {}).get("role_flow") or {})


def active(ctx: AppContext) -> bool:
    return (bool(policy(ctx).get("enabled")) or _team(ctx).declared) and flags.enabled(ctx, "role_flow")


def roster(ctx: AppContext) -> dict[str, list[str]]:
    raw = dict(_trusted(ctx).get("roles") or {})
    out = {role: [str(x) for x in raw.get(role) or []] for role in r.ROLES}
    team = _team(ctx)
    for role in r.ROLES:  # `team.yaml` do repositório do time soma ao que a org declarou
        out[role] += [p for p in team.people("tl" if role == "tech_lead" else role) if p not in out[role]]
    return out


def roles_of(ctx: AppContext, who: str) -> list[str]:
    return [role for role, people in roster(ctx).items() if who in people]


def _require_role(ctx: AppContext, role: str, who: identity.Identity, action: str) -> None:
    people = roster(ctx)[role]
    if not people:
        raise NexoError(f"nenhuma pessoa com o papel {r.ROLE_LABEL[role]} em roles.{role} (config da org ou do squad)")
    if who.id not in people:
        ctx.audit("policy.violation", {"rule": "role_gate", "reason": f"{action} exige papel {role}", "who": who.id})
        raise PolicyViolation(f"{action} é do {r.ROLE_LABEL[role]} ({', '.join(people)}); você é {who.id}")


# ------------------------------------------------------------------ arquivos da feature


def _record(folder: Path) -> r.RoleRecord:
    return r.RoleRecord.model_validate(yamlfile.load(folder / ROLES_FILE))


def _save_record(folder: Path, record: r.RoleRecord) -> None:
    yamlfile.dump(folder / ROLES_FILE, record.model_dump(mode="json"))


def _draft(folder: Path) -> Backlog | None:
    path = folder / DRAFT_FILE
    return Backlog.model_validate(yamlfile.load(path)) if path.is_file() else None


def load_discovery(folder: Path) -> r.Discovery | None:
    path = folder / DISCOVERY_FILE
    return r.Discovery.model_validate(yamlfile.load(path)) if path.is_file() else None


def _save_discovery(folder: Path, d: r.Discovery) -> None:
    yamlfile.dump(folder / DISCOVERY_FILE, d.model_dump(mode="json"))
    (folder / "discovery.md").write_text(r.discovery_brief(d), encoding="utf-8")


def _announce(planner: Any, fid: str, title: str, message: str) -> None:
    """Passagem automática: aviso ao próximo papel (Teams/e-mail) e comentário no item do tracker."""
    notify(planner.ctx, "planning", title, message, ref=fid)
    planner.sync.comment(fid, f"{title}: {message}")


# ------------------------------------------------------------------ discovery


def discovery_schema() -> dict[str, Any]:
    full = r.Discovery.model_json_schema()
    drop = ("decision", "decision_reason", "decided_by")
    props = {k: v for k, v in full["properties"].items() if k not in drop}
    props["recommendation"] = {"type": "string", "enum": list(r.DECISIONS)}
    return {
        "type": "object",
        "$defs": full.get("$defs", {}),
        "properties": props,
        "required": ["title", "problem", "opportunities", "hypotheses", "recommendation"],
    }


def discovery(planner: Any, inputs: list[str], fid: str | None = None, title: str | None = None) -> dict[str, Any]:
    """Cria (ou enriquece) o discovery de uma feature a partir dos insumos do PM."""
    flags.require(planner.ctx, "discovery")
    if not inputs:
        raise NexoError("informe ao menos um insumo: pesquisa, dados, entrevistas, texto entre aspas")
    if fid is None:
        fid = specs.next_feature_id(planner.root)
        folder = specs.create_feature_dir(planner.root, fid, title or "Discovery")
        spec = Spec(id=fid, title=title or "Discovery")
        specs.save_spec(folder, spec)
        specs.save_questions(folder, [])
        planner.store.upsert_item(fid, "feature", spec.title, planner.project, "local", planner.workflow.initial)
        planner.ctx.audit("discovery.created", {"feature": fid}, ref=fid)
    else:
        folder = planner._folder(fid)
        if planner.store.item_state(fid) != "Ideia":
            raise NexoError(f"{fid} já saiu do discovery (estado {planner.store.item_state(fid)})")
    current = load_discovery(folder) or r.Discovery(title=title or "")
    ingested = planner._ingest(folder, inputs)
    prompt = content.prompt(planner.ctx, "discovery", fid).format(
        discovery=_yaml(current.model_dump(mode="json")),
        missing="\n".join(f"- {m}" for m in r.discovery_missing(current)) or "- nenhuma",
        inputs=planner._inputs_text(folder, ingested) or "- nenhum",
    )

    def check(data: dict[str, Any]) -> list[str]:
        try:
            r.Discovery.model_validate(data)
        except ValueError as exc:
            return [str(exc).splitlines()[0]]
        return []

    result = llm.call_structured(
        planner.ctx,
        "discovery",
        fid,
        prompt,
        discovery_schema(),
        folder,
        facts={"kind": "planning"},
        data_class=str(planner.project_cfg.get("data_class", "confidential")),
        validate=check,
        ui=planner.ui,
        **planner._route_options(),
    )
    found = r.Discovery.model_validate(result.data)
    _save_discovery(folder, found)
    if found.title and planner.store.item_state(fid) == "Ideia":
        spec = specs.load_spec(folder).model_copy(update={"title": found.title})
        specs.save_spec(folder, spec)
        folder = planner._maybe_rename(folder, spec)
        planner.store.upsert_item(fid, "feature", spec.title, planner.project, "local", "Ideia")
    planner.ctx.audit(
        "discovery.refined",
        {"feature": fid, "hypotheses": len(found.hypotheses), "recommendation": found.recommendation},
        ref=fid,
    )
    return discovery_status(planner, fid) | {"run_id": result.run_id}


def discovery_status(planner: Any, fid: str) -> dict[str, Any]:
    folder = specs.feature_dir(planner.root, fid)
    d = load_discovery(folder)
    if d is None:
        raise NexoError(f"{fid} não tem discovery (nexo plan discovery <insumos>)")
    return {
        "feature": fid,
        "folder": str(folder),
        "title": d.title,
        "state": planner.store.item_state(fid),
        "missing": r.discovery_missing(d),
        "recommendation": d.recommendation,
        "decision": d.decision,
        "hypotheses": [h.model_dump() for h in d.hypotheses],
        "open_questions": d.open_questions,
    }


def decide(planner: Any, fid: str, decision: str, reason: str, force: bool = False) -> dict[str, Any]:
    """Decisão do PM. `build` leva o discovery direto ao refinamento com o agente (sem reescrever nada)."""
    flags.require(planner.ctx, "discovery")
    if decision not in r.DECISIONS:
        raise NexoError(f"decisão inválida: {decision} (build | drop | investigate)")
    if len(reason.strip()) < 10:
        raise NexoError("--reason é obrigatório (mínimo 10 caracteres): a decisão fica na trilha de auditoria")
    folder = planner._folder(fid)
    d = load_discovery(folder)
    if d is None or planner.store.item_state(fid) != "Ideia":
        raise NexoError(f"{fid} não está em discovery")
    who = identity.current(planner.ctx)
    if active(planner.ctx):
        _require_role(planner.ctx, "pm", who, "decidir o discovery")
    missing = r.discovery_missing(d)
    if decision == "build" and missing and not force:
        raise NexoError(f"discovery sem base para construir — falta: {', '.join(missing)} (ou --force)")
    d = d.model_copy(update={"decision": decision, "decision_reason": reason, "decided_by": who.id})
    _save_discovery(folder, d)
    planner.ctx.audit(
        "gate.decision",
        {"gate": "discovery", "feature": fid, "decision": decision, "reason": reason, "forced": bool(missing)},
        ref=fid,
    )
    out: dict[str, Any] = {"feature": fid, "decision": decision}
    if decision == "drop":
        planner._transition(fid, "cancel")
    elif decision == "build":
        planner._transition(fid, "plan")
        brief = folder / "discovery-brief.md"
        brief.write_text(r.discovery_brief(d), encoding="utf-8")
        report = planner.feature(fid, [str(brief)])  # o discovery vira insumo do refinamento
        brief.unlink(missing_ok=True)
        _announce(
            planner,
            fid,
            f"{fid} entrou em refinamento",
            f"{who.id} decidiu construir. Próximo passo: refinamento com a IA"
            + (" em sessão conjunta (PM, tech lead e dev)" if active(planner.ctx) else ""),
        )
        out["plan"] = report.__dict__
    out["state"] = planner.store.item_state(fid)
    return out


# ------------------------------------------------------------------ sessão conjunta


def session_start(planner: Any, fid: str, participants: dict[str, list[str]]) -> dict[str, Any]:
    _require_active(planner.ctx)
    folder = planner._folder(fid)
    if planner.store.item_state(fid) != "Refinamento":
        raise NexoError(f"sessão conjunta é no refinamento; {fid} está em {planner.store.item_state(fid)}")
    record = _record(folder)
    if record.open_session():
        raise NexoError(f"já há uma sessão aberta em {fid} ({record.open_session().id})")  # type: ignore[union-attr]
    who = identity.current(planner.ctx)
    team = roster(planner.ctx)
    for role, people in participants.items():
        for person in people:
            if team[role] and person not in team[role]:
                raise PolicyViolation(f"{person} não tem o papel {r.ROLE_LABEL[role]} (roles.{role})")
    everyone = {p for people in participants.values() for p in people}
    if who.id not in everyone:
        raise PolicyViolation(f"quem abre a sessão participa dela; {who.id} não está entre os participantes")
    session = r.Session(
        id=f"S-{len(record.sessions) + 1}", started_at=_now(), started_by=who.id, participants=participants
    )
    record.sessions.append(session)
    _save_record(folder, record)
    planner.ctx.audit("plan.session_started", {"feature": fid, "session": session.id, **participants}, ref=fid)
    missing = [r.ROLE_LABEL[x] for x in r.SESSION_ROLES if not participants.get(x)]
    _announce(
        planner,
        fid,
        f"Sessão conjunta {session.id} de {fid} aberta",
        "Participantes: " + "; ".join(f"{r.ROLE_LABEL[k]}: {', '.join(v)}" for k, v in participants.items() if v),
    )
    return {"feature": fid, "session": session.model_dump(), "missing_roles": missing}


def session_close(planner: Any, fid: str, notes: str = "") -> dict[str, Any]:
    _require_active(planner.ctx)
    folder = planner._folder(fid)
    record = _record(folder)
    session = record.open_session()
    if session is None:
        raise NexoError(f"{fid} não tem sessão aberta")
    who = identity.current(planner.ctx)
    if who.id not in {p for people in session.participants.values() for p in people}:
        raise PolicyViolation("só um participante encerra a sessão")
    session.closed_at = _now()
    session.notes = notes
    _save_record(folder, record)
    planner.ctx.audit(
        "plan.session_closed",
        {"feature": fid, "session": session.id, "complete": session.complete, "notes": notes[:500]},
        ref=fid,
    )
    status = planner.status(fid)
    if status.ready:  # fim da sessão já gera o pacote para as aprovações
        return package(planner, fid)
    return {"feature": fid, "session": session.model_dump(), "ready": False, "score": status.score}


# ------------------------------------------------------------------ pacote e aprovações


def _require_active(ctx: AppContext) -> None:
    flags.require(ctx, "role_flow")
    if not active(ctx):
        raise NexoError("fluxo por papéis desligado: planning.role_flow.enabled na config da org ou do squad")


def package(planner: Any, fid: str, feedback: str = "") -> dict[str, Any]:
    """Gera o plano (rascunho) a partir da spec pronta: o pacote requisitos + design/SDD + plano vai às aprovações."""
    _require_active(planner.ctx)
    folder = planner._folder(fid)
    if planner.store.item_state(fid) != "Refinamento":
        raise NexoError(f"{fid} está em {planner.store.item_state(fid)}; o pacote é gerado no refinamento")
    spec = specs.load_spec(folder)
    status = planner.status(fid)
    if not status.ready:
        missing = ", ".join(i["label"] for i in status.missing) or "dúvidas bloqueantes"
        raise NexoError(f"prontidão {status.score}% — antes do pacote: {missing}")
    planner._warn_suspects(folder)
    draft, run_id = planner.draft_backlog(folder, spec, feedback)
    yamlfile.dump(folder / DRAFT_FILE, draft.model_dump(mode="json"))
    record = _record(folder)
    record.packaged_at = _now()
    _save_record(folder, record)
    planner.ctx.audit(
        "plan.packaged", {"feature": fid, "stories": len(draft.stories), "tasks": len(draft.tasks)}, ref=fid
    )
    view = roles_status(planner, fid)
    pending = [p for p in view["parts"] if p["status"] != "aprovada"]
    _announce(
        planner,
        fid,
        f"Pacote de {fid} pronto para aprovação",
        "; ".join(f"{r.ROLE_LABEL[p['role']]} aprova {r.PART_LABEL[p['part']]}" for p in pending)
        + (f". Sessão conjunta exigida: {'; '.join(view['joint_reasons'])}" if view["joint_pending"] else ""),
    )
    return view | {"run_id": run_id}


def roles_status(planner: Any, fid: str) -> dict[str, Any]:
    folder = specs.feature_dir(planner.root, fid)
    spec = specs.load_spec(folder)
    draft = _draft(folder)
    record = _record(folder)
    statuses = r.part_status(record, r.part_digests(spec, draft))
    reasons = r.joint_reasons(spec, draft, r.JointPolicy.from_config(policy(planner.ctx)))
    team = roster(planner.ctx)
    open_s = record.open_session()
    return {
        "feature": fid,
        "state": planner.store.item_state(fid),
        "packaged": draft is not None,
        "joint_reasons": reasons,
        "joint_pending": bool(reasons) and not record.joint_done(),
        "open_session": open_s.id if open_s else None,
        "sessions": [s.model_dump() for s in record.sessions],
        "parts": [
            {
                "part": s.part,
                "role": s.role,
                "status": s.status,
                "by": s.by,
                "detail": s.detail,
                "who_can": team[s.role],
            }
            for s in statuses
        ],
    }


def approve_part(planner: Any, fid: str, part: str) -> dict[str, Any]:
    _require_active(planner.ctx)
    if part not in r.PARTS:
        raise NexoError(f"parte inválida: {part} (requisitos | design | plano)")
    folder = planner._folder(fid)
    draft = _draft(folder)
    if draft is None:
        raise NexoError(f"{fid} ainda não tem pacote (encerre a sessão ou rode nexo plan package {fid})")
    if planner.store.item_state(fid) != "Refinamento":
        raise NexoError(f"{fid} está em {planner.store.item_state(fid)}")
    spec = specs.load_spec(folder)
    record = _record(folder)
    who = identity.current(planner.ctx)
    role = r.ROLE_FOR[part]
    _require_role(planner.ctx, role, who, f"aprovar {r.PART_LABEL[part]}")
    digests = r.part_digests(spec, draft)
    reasons = r.joint_reasons(spec, draft, r.JointPolicy.from_config(policy(planner.ctx)))
    if reasons and not record.joint_done():
        raise PolicyViolation(
            f"{fid} exige sessão conjunta com PM, tech lead e dev antes das aprovações ({'; '.join(reasons)})"
        )
    if not (policy(planner.ctx).get("allow_same_person", False) or _team(planner.ctx).solo):
        for s in r.part_status(record, digests):
            if s.status == "aprovada" and s.by == who.id and s.part != part:
                raise PolicyViolation(
                    f"segregação de funções: {who.id} já aprovou {r.PART_LABEL[s.part]}; outra pessoa aprova esta parte"
                )
    last = record.open_session() or next((s for s in reversed(record.sessions) if s.complete), None)
    record.approvals.append(
        r.Approval(
            part=part,
            by=who.id,
            verified=who.verified,
            at=_now(),
            digest=digests[part],
            session=last.id if last else "",
        )
    )
    _save_record(folder, record)
    planner.ctx.audit(
        "gate.decision",
        {"gate": f"role:{part}", "feature": fid, "decision": "approved", "approver": who.id, "digest": digests[part]},
        ref=fid,
    )
    view = roles_status(planner, fid)
    pending = [p for p in view["parts"] if p["status"] != "aprovada"]
    if pending:
        _announce(
            planner,
            fid,
            f"{r.PART_LABEL[part].capitalize()} de {fid} aprovado(s) por {who.id}",
            "Aguardando: " + "; ".join(f"{r.ROLE_LABEL[p['role']]} ({r.PART_LABEL[p['part']]})" for p in pending),
        )
        return view
    return _finish(planner, fid, folder, spec, draft)


def _finish(planner: Any, fid: str, folder: Path, spec: Spec, draft: Backlog) -> dict[str, Any]:
    """Três aprovações válidas: backlog materializado, tracker e documentos, e o dev avisado com o SDD."""
    planner.ctx.audit("gate.decision", {"gate": "spec", "feature": fid, "decision": "approved", "score": 100}, ref=fid)
    report = planner.materialize(folder, spec, draft)
    sdd = folder / "spec.md"
    devs = roster(planner.ctx)["dev"]
    _announce(
        planner,
        fid,
        f"{fid} aprovada pelos três papéis: pronta para desenvolvimento",
        f"{len(draft.stories)} história(s) e {len(draft.tasks)} tarefa(s) no tracker; "
        f"SDD: {sdd.relative_to(planner.root)}"
        + (f"; devs: {', '.join(devs)}" if devs else "")
        + f". Próximo passo: nexo run feature {fid}",
    )
    return roles_status(planner, fid) | {"plan": report.__dict__}


def reject_part(planner: Any, fid: str, part: str, reason: str) -> dict[str, Any]:
    """Rejeição volta só a parte; o agente refaz usando o motivo como insumo (sem reescrita manual)."""
    _require_active(planner.ctx)
    if part not in r.PARTS:
        raise NexoError(f"parte inválida: {part} (requisitos | design | plano)")
    if len(reason.strip()) < 10:
        raise NexoError("--reason é obrigatório (mínimo 10 caracteres): vira insumo do retrabalho")
    folder = planner._folder(fid)
    draft = _draft(folder)
    if draft is None:
        raise NexoError(f"{fid} ainda não tem pacote")
    spec = specs.load_spec(folder)
    who = identity.current(planner.ctx)
    _require_role(planner.ctx, r.ROLE_FOR[part], who, f"rejeitar {r.PART_LABEL[part]}")
    record = _record(folder)
    digests = r.part_digests(spec, draft)
    record.rejections.append(r.Rejection(part=part, by=who.id, at=_now(), reason=reason, digest=digests[part]))
    _save_record(folder, record)
    planner.ctx.audit(
        "gate.decision",
        {"gate": f"role:{part}", "feature": fid, "decision": "rejected", "approver": who.id, "reason": reason},
        ref=fid,
    )
    _announce(planner, fid, f"{r.PART_LABEL[part].capitalize()} de {fid} rejeitado(s) por {who.id}", reason)
    if part == "plano":
        return package(planner, fid, feedback=reason)
    note = folder / f"ajuste-{part}.md"
    note.write_text(f"# Ajuste pedido pelo {r.ROLE_LABEL[r.ROLE_FOR[part]]} ({part})\n\n{reason}\n", "utf-8")
    report = planner.feature(fid, [str(note)])
    note.unlink(missing_ok=True)
    if report.ready:
        return package(planner, fid)
    return roles_status(planner, fid) | {"plan": report.__dict__}


def inbox(planner: Any) -> dict[str, Any]:
    """O que está esperando por mim: decisões de discovery (PM) e partes do pacote do meu papel."""
    who = identity.current(planner.ctx)
    mine = roles_of(planner.ctx, who.id)
    items: list[dict[str, Any]] = []
    root = specs.specs_root(planner.root)
    for folder in sorted(root.glob("F-*")) if root.is_dir() else []:
        if not folder.is_dir():
            continue
        fid = "-".join(folder.name.split("-")[:2])
        state = planner.store.item_state(fid)
        d = load_discovery(folder)
        if "pm" in mine and d is not None and state == "Ideia" and not d.decision:
            items.append({"feature": fid, "action": "decidir discovery", "command": f"nexo plan decide {fid}"})
        if state != "Refinamento" or _draft(folder) is None:
            continue
        view = roles_status(planner, fid)
        if view["joint_pending"]:
            items.append({"feature": fid, "action": "sessão conjunta", "command": f"nexo plan session start {fid}"})
            continue
        for p in view["parts"]:
            if p["role"] in mine and p["status"] != "aprovada":
                items.append(
                    {
                        "feature": fid,
                        "action": f"aprovar {r.PART_LABEL[p['part']]} ({p['status']})",
                        "command": f"nexo plan approve {fid} --part {p['part']}",
                    }
                )
    return {"who": who.id, "roles": mine, "items": items}


def _yaml(data: dict[str, Any]) -> str:
    from nexo.modules.planning.app.plan import yamlfile_dump

    return yamlfile_dump(data)
