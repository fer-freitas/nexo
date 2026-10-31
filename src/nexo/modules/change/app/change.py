"""Gestão de mudança: a requisição de mudança da entrega sai do pacote de evidências.

`nexo change open F-n` monta o pacote, decide o tipo (padrão quando todas as histórias são candidatas e as
evidências sustentam; normal nos demais casos) e abre a requisição no ServiceNow
com plano de implantação, plano de retorno e plano de teste. `nexo change status F-n` acompanha a aprovação.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nexo.kernel import services
from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.finops.money import brl
from nexo.kernel.platform import config, flags
from nexo.kernel.runtime import yamlfile
from nexo.kernel.workspace import specs
from nexo.store.runs import now_iso

approval = services.load("delivery.approval")
evidence = services.load("delivery.evidence")
ready = services.load("planning.ready")

RISK = {"baixo": "low", "medio": "moderate", "alto": "high"}
ORDER = ("baixo", "medio", "alto")


def _cfg(ctx: AppContext) -> dict[str, Any]:
    return dict(config.resolved(ctx).values.get("change") or {})


def _channel(ctx: AppContext) -> tuple[str, Any]:
    name = str(_cfg(ctx).get("channel") or "")
    if name != "servicenow":
        raise NexoError("configure change.channel: servicenow (ajustes em approvals.servicenow)")
    approvals = dict(config.resolved(ctx).values.get("approvals") or {})
    return name, approval.itsm_channel(ctx, name, approvals)


def _record_path(root: Path, fid: str) -> Path:
    return specs.feature_dir(root, fid) / "change.yaml"


def assess(root: Path, data: dict[str, Any]) -> dict[str, Any]:
    """Tipo e risco da mudança a partir do refinamento e das evidências (sem IA)."""
    reasons: list[str] = []
    risks, plans, backouts = [], [], []
    for story in data["stories"]:
        ref = ready.load_refinement(root, story["id"]) or {}
        change, deploy = ref.get("mudanca") or {}, ref.get("deploy") or {}
        risks.append(str(change.get("risco") or "alto"))
        if not change.get("candidata_mudanca_padrao", change.get("candidata_low_risk")):
            reasons.append(f"{story['id']} não é candidata a mudança padrão")
        if story.get("state") != "Concluido":
            reasons.append(f"{story['id']} ainda não está concluída ({story.get('state') or '—'})")
        plans.append(f"- {story['id']} — {story['title']}: {deploy.get('estrategia') or 'estratégia não informada'}")
        backouts.append(f"- {story['id']}: {deploy.get('rollback') or 'plano de retorno não informado'}")
    for perf in data.get("performance") or []:
        if perf.get("required") and perf.get("status") != "aprovado":
            reasons.append(f"{perf['story']}: teste de performance {perf.get('status')}")
    if not data["audit"]["chain_ok"]:
        reasons.append("trilha de auditoria com falha de integridade")
    risk = max(risks or ["alto"], key=lambda r: ORDER.index(r) if r in ORDER else len(ORDER))
    return {
        "standard_ok": not reasons,
        "reasons": reasons,
        "risk": risk,
        "implementation_plan": "\n".join(plans),
        "backout_plan": "\n".join(backouts),
    }


def _test_plan(data: dict[str, Any]) -> str:
    lines = [
        f"- {s['id']}: critérios {', '.join(s['acceptance']) or '—'}; tentativas {s.get('attempts') or 1};"
        f" PR {s.get('pr') or '—'}"
        for s in data["stories"]
    ]
    for p in data.get("performance") or []:
        m = p.get("measured") or {}
        extra = f" (p95 {m.get('p95_ms')} ms, erro {m.get('erro_max_pct')}%)" if m else ""
        lines.append(f"- Performance {p['story']}: {p.get('status')}{extra}")
    return "\n".join(lines)


def open_change(ctx: AppContext, fid: str, kind: str | None = None) -> dict[str, Any]:
    flags.require(ctx, "change")
    root = ctx.project_root
    if root is None:
        raise NexoError("rode dentro de um repositório com a Nexo (nexo init)")
    record = _record_path(root, fid)
    if record.is_file() and yamlfile.load(record).get("approval") not in ("rejected", "cancelled"):
        raise NexoError(f"{fid} já tem requisição de mudança aberta ({yamlfile.load(record).get('number')})")
    name, channel = _channel(ctx)
    built = evidence.build(ctx, fid)
    folder = Path(built["folder"])
    data = json.loads((folder / "dados.json").read_text(encoding="utf-8"))
    a = assess(root, data)
    if kind == "standard" and not a["standard_ok"]:
        raise NexoError("não atende aos critérios de mudança padrão: " + "; ".join(a["reasons"]))
    change_type = kind or ("standard" if a["standard_ok"] else "normal")
    title = f"Entrega {fid}: {data['title']}"
    description = "\n".join(
        [
            f"Entrega {fid} preparada pela Nexo. Pacote de evidências: {folder.name} (trilha íntegra:"
            f" {'sim' if data['audit']['chain_ok'] else 'não'}; último hash {data['audit']['last_hash']}).",
            f"Histórias: {', '.join(s['id'] for s in data['stories'])}. Custo de IA medido: {brl(data['cost_brl'])}.",
            *(["Motivos para mudança normal: " + "; ".join(a["reasons"])] if a["reasons"] else []),
        ]
    )
    if name == "servicenow":
        opened = channel.open_change(
            {
                "short_description": title[:160],
                "description": description,
                "type": change_type,
                "risk": RISK.get(a["risk"], "high"),
                "implementation_plan": a["implementation_plan"],
                "backout_plan": a["backout_plan"],
                "test_plan": _test_plan(data),
                "correlation_id": fid,
                "correlation_display": "nexo",
            }
        )
    else:
        body = "\n\n".join(
            [
                description,
                f"Tipo: {change_type} · risco: {a['risk']}",
                "Plano de implantação:\n" + a["implementation_plan"],
                "Plano de retorno:\n" + a["backout_plan"],
                "Plano de teste:\n" + _test_plan(data),
            ]
        )
        opened = channel.open_change(title, body)
    result = {
        "feature": fid,
        "channel": name,
        "ref": opened["ref"],
        "number": opened["number"],
        "type": change_type,
        "risk": a["risk"],
        "reasons": a["reasons"],
        "evidence": folder.name,
        "opened_at": now_iso(),
        "approval": "requested",
    }
    yamlfile.dump(record, result)
    ctx.audit("change.opened", {k: result[k] for k in ("feature", "channel", "number", "type", "risk")}, ref=fid)
    from nexo.kernel import services

    notify = services.get("notify.main", "notify")

    notify(ctx, "change", f"{fid}: mudança {opened['number']} aberta ({change_type})", title, ref=fid)
    return result


def status(ctx: AppContext, fid: str) -> dict[str, Any]:
    flags.require(ctx, "change")
    root = ctx.project_root
    if root is None:
        raise NexoError("rode dentro de um repositório com a Nexo (nexo init)")
    record = _record_path(root, fid)
    if not record.is_file():
        raise NexoError(f"{fid} não tem requisição de mudança (nexo change open {fid})")
    data = dict(yamlfile.load(record))
    _, channel = _channel(ctx)
    now = channel.status(str(data["ref"]))
    if now["approval"] != data.get("approval") and now["approval"] in ("approved", "rejected"):
        ctx.audit(
            "change.decided", {"feature": fid, "number": data.get("number"), "approval": now["approval"]}, ref=fid
        )
        from nexo.kernel import services

        notify = services.get("notify.main", "notify")

        verb = "aprovada" if now["approval"] == "approved" else "recusada"
        notify(
            ctx, "change", f"{fid}: mudança {data.get('number')} {verb}", f"Requisição {data.get('number')}", ref=fid
        )
    data.update(approval=now["approval"], state=now.get("state"), checked_at=now_iso())
    yamlfile.dump(record, data)
    return data
