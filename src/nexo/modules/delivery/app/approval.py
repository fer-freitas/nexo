"""Aprovações parametrizáveis: o portão de qualidade, de custo ou de orçamento pode ser aprovado no Nexo
(`nexo approve`) ou fora dele. No modo externo o Nexo escala a solicitação pelo canal configurado e só espera o
sinal `continue` ou `stop`; a segregação de funções passa a ser responsabilidade do sistema externo."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError, PolicyViolation
from nexo.kernel.platform import config, identity
from nexo.kernel.secrets import credentials
from nexo.modules.delivery.adapters import approvals as channels
from nexo.store.runs import now_iso, project_key

KINDS = ("quality", "cost", "budget")
MODES = ("nexo", "external")


def _cfg(ctx: AppContext) -> dict[str, Any]:
    return dict(config.resolved(ctx).values.get("approvals") or {})


def mode(ctx: AppContext, kind: str) -> str:
    """Modo do portão: `approvals.gates.<kind>` ou `approvals.mode`. Só vale da org/squad (ou padrão):
    no projeto ou no pessoal o próprio dev poderia se aprovar por um arquivo de decisão."""
    res = config.resolved(ctx)
    cfg = dict(res.values.get("approvals") or {})
    key = f"approvals.gates.{kind}" if kind in (cfg.get("gates") or {}) else "approvals.mode"
    value = str((cfg.get("gates") or {}).get(kind) or cfg.get("mode") or "nexo")
    origin = res.origin.get(key, "default")
    if value not in MODES:
        raise NexoError(f"{key}: use nexo ou external")
    if value != "nexo" and origin not in identity.TRUSTED_ORIGINS:
        ctx.audit
        return "nexo"
    return value


def _channel_name(ctx: AppContext) -> str:
    return str(_cfg(ctx).get("channel") or "file")


def itsm_channel(ctx: AppContext, name: str, cfg: dict[str, Any]) -> Any:
    """Canal de ITSM a partir da configuração: `servicenow` ou `jsm`."""
    if name == "servicenow":
        sn = dict(cfg.get("servicenow") or {})
        token = credentials.get_secret("servicenow")
        if not token:
            raise NexoError("credencial 'servicenow' ausente: rode `nexo login servicenow`")
        return channels.ServiceNowChannel(str(sn.get("instance_url") or ""), token, sn.get("user"))
    jsm = dict(cfg.get("jsm") or {})
    token = credentials.get_secret("jsm") or credentials.get_secret("jira")
    if not token:
        raise NexoError("credencial 'jsm' ausente: rode `nexo login jsm` (ou use a do Jira)")
    missing = [k for k in ("base_url", "email", "service_desk_id", "request_type_id") if not jsm.get(k)]
    if missing:
        raise NexoError(f"Jira Service Management sem {', '.join(missing)} em approvals.jsm")
    return channels.JsmChannel(
        str(jsm["base_url"]), str(jsm["email"]), token, str(jsm["service_desk_id"]), str(jsm["request_type_id"])
    )


def _channel(ctx: AppContext) -> Any:
    cfg = _cfg(ctx)
    name = _channel_name(ctx)
    if name in ("servicenow", "jsm"):
        return itsm_channel(ctx, name, cfg)
    if name == "file":
        folder = str((cfg.get("file") or {}).get("dir") or "")
        return channels.FileChannel(Path(folder).expanduser() if folder else ctx.home / "approvals")
    if name == "http":
        return channels.HttpChannel(str((cfg.get("http") or {}).get("url") or ""), credentials.get_secret("approvals"))
    raise NexoError(f"canal de aprovação desconhecido: {name} (file | http | tracker | servicenow | jsm)")


def _requester(ctx: AppContext) -> str:
    try:
        return identity.current(ctx).id
    except NexoError:
        return f"os:{ctx.actor}"


def _scope(ctx: AppContext) -> str:
    return project_key(ctx.project_root) if ctx.project_root else "global"


def find_pending(ctx: AppContext, kind: str, target: str | None = None, dev: str | None = None) -> str | None:
    sql = "SELECT id FROM approval_requests WHERE status = 'pending' AND kind = ?"
    params: list[Any] = [kind]
    if target is not None:
        sql += " AND target = ?"
        params.append(target)
    if dev is not None:
        sql += " AND dev = ?"
        params.append(dev)
    row = ctx.conn.execute(sql + " ORDER BY created_at LIMIT 1", params).fetchone()
    return str(row["id"]) if row else None


def request(ctx: AppContext, kind: str, target: str, title: str, detail: dict[str, Any]) -> str:
    """Abre a solicitação, escala pelo canal e notifica. Devolve o id `AP-n`."""
    from nexo.modules.finops.app import costs
    from nexo.modules.notify.app import notify

    n = int(ctx.conn.execute("SELECT COUNT(*) FROM approval_requests").fetchone()[0]) + 1
    request_id = f"AP-{n}"
    name = _channel_name(ctx)
    payload = {
        "id": request_id,
        "kind": kind,
        "target": target,
        "title": title,
        "project": ctx.project_root.name if ctx.project_root else None,
        "requested_by": _requester(ctx),
        "detail": detail,
        "created_at": now_iso(),
        "answer": "continue ou stop",
    }
    if name == "tracker":
        ref = _tracker_escalate(ctx, target, request_id, title)
    else:
        ref = _channel(ctx).request(request_id, payload)
    ctx.conn.execute(
        "INSERT INTO approval_requests(id, project, kind, target, dev, month, channel, status, detail, external_ref,"
        " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            request_id,
            _scope(ctx),
            kind,
            target,
            ctx.actor,
            costs.current_month(),
            name,
            "pending",
            json.dumps({"title": title, "requested_by": payload["requested_by"], **detail}, ensure_ascii=False),
            ref,
            now_iso(),
        ),
    )
    ctx.audit("approval.requested", {"request": request_id, "kind": kind, "item": target, "channel": name}, ref=target)
    notify(ctx, "approval", f"{target}: aprovação externa {request_id}", title, ref=target)
    return request_id


def _tracker_cfg(ctx: AppContext) -> dict[str, Any]:
    return dict(_cfg(ctx).get("tracker") or {})


def _tracker_escalate(ctx: AppContext, target: str, request_id: str, title: str) -> str | None:
    from nexo.modules.demands.app import tracker as integrations_tracker

    if ctx.project_root is None:
        raise NexoError("o canal tracker exige um projeto Nexo")
    sync = integrations_tracker.for_root(ctx, ctx.project_root)
    if not sync.enabled:
        raise NexoError("approvals.channel=tracker exige um tracker configurado")
    cfg = _tracker_cfg(ctx)
    cont = ", ".join([*cfg.get("continue_states", []), *cfg.get("continue_tags", [])])
    stop = ", ".join([*cfg.get("stop_states", []), *cfg.get("stop_tags", [])])
    sync.comment(target, f"Nexo aguarda aprovação {request_id}: {title}. Para continuar: {cont}. Para parar: {stop}.")
    link = sync.ref(target)
    return str(link["ref"]) if link else None


def _tracker_poll(ctx: AppContext, row: Any) -> str | None:
    from nexo.modules.demands.app import tracker as integrations_tracker

    if ctx.project_root is None or not row["external_ref"]:
        return None
    tracker = integrations_tracker.for_root(ctx, ctx.project_root).tracker()
    states = tracker.fetch([str(row["external_ref"])])
    if not states:
        return None
    ext, cfg = states[0], _tracker_cfg(ctx)
    tags = set(ext.tags)
    if ext.state in cfg.get("stop_states", []) or tags & set(cfg.get("stop_tags", [])):
        return "stop"
    if ext.state in cfg.get("continue_states", []) or tags & set(cfg.get("continue_tags", [])):
        return "continue"
    return None


def listing(ctx: AppContext, only_pending: bool = False) -> list[dict[str, Any]]:
    rows = ctx.conn.execute(
        "SELECT * FROM approval_requests WHERE (? = 0 OR status = 'pending') ORDER BY created_at", (int(only_pending),)
    ).fetchall()
    return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]


def sync(ctx: AppContext) -> list[dict[str, Any]]:
    """Lê o sinal de cada solicitação pendente e aplica a decisão."""
    resolved = []
    for row in ctx.conn.execute("SELECT * FROM approval_requests WHERE status = 'pending'").fetchall():
        if row["kind"] != "budget" and row["project"] != _scope(ctx):
            continue  # histórias de outro projeto são sincronizadas dentro dele
        try:
            if row["channel"] == "tracker":
                decision = _tracker_poll(ctx, row)
            else:  # ITSM conhece a requisição pelo id dela (sys_id, chave); file/http, pelo id da Nexo
                key = row["external_ref"] if row["channel"] in ("servicenow", "jsm") else row["id"]
                decision = _channel(ctx).poll(str(key))
        except (NexoError, OSError) as exc:
            ctx.audit("approval.error", {"request": row["id"], "error": str(exc)})
            continue
        if decision:
            resolved.append(resolve(ctx, str(row["id"]), decision, f"external:{row['channel']}"))
    return resolved


def signal(ctx: AppContext, request_id: str, decision: str) -> dict[str, Any]:
    """Repasse manual do sinal externo (ex.: aprovação recebida por e-mail). Quem pediu não repassa o próprio."""
    if decision not in channels.DECISIONS:
        raise NexoError("sinal inválido: use continue ou stop")
    row = _row(ctx, request_id)
    who = identity.current(ctx)
    if json.loads(row["detail"]).get("requested_by") == who.id:
        ctx.audit
        raise PolicyViolation("segregação de funções: quem pediu não repassa o sinal da própria aprovação")
    return resolve(ctx, request_id, decision, who.id)


def _row(ctx: AppContext, request_id: str) -> Any:
    row = ctx.conn.execute("SELECT * FROM approval_requests WHERE id = ?", (request_id,)).fetchone()
    if row is None:
        raise NexoError(f"solicitação {request_id} não encontrada")
    if row["status"] != "pending":
        raise NexoError(f"{request_id} já foi resolvida ({row['status']})")
    return row


def resolve(ctx: AppContext, request_id: str, decision: str, by: str) -> dict[str, Any]:
    row = _row(ctx, request_id)
    status = "continued" if decision == "continue" else "stopped"
    ctx.conn.execute(
        "UPDATE approval_requests SET status = ?, resolved_at = ?, resolved_by = ? WHERE id = ?",
        (status, now_iso(), by, request_id),
    )
    ctx.audit(
        "approval.resolved",
        {"request": request_id, "kind": row["kind"], "item": row["target"], "decision": decision, "source": by},
        ref=str(row["target"]),
    )
    result = {"request": request_id, "kind": row["kind"], "target": row["target"], "decision": decision}
    if row["kind"] == "budget":
        if decision == "continue":
            result["granted_brl"] = _grant(ctx, row)
        return result
    from nexo.modules.execution.app.story import StoryRunner

    report = StoryRunner(ctx).apply_external(str(row["target"]), request_id, decision, by)
    result["state"] = report.state
    return result


def _grant(ctx: AppContext, row: Any) -> float:
    """Crédito no mês do dev: percentual do limite vigente (`approvals.on_continue.budget_grant_pct`)."""
    from nexo.modules.finops.app import costs

    pct = float((_cfg(ctx).get("on_continue") or {}).get("budget_grant_pct", 20.0))
    status = costs.budget_status(ctx, str(row["dev"]), str(row["month"]))
    amount = round((status.limit_brl or 0.0) * pct / 100, 2)
    ctx.conn.execute(
        "INSERT INTO budget_grants(dev, month, amount_brl, request_id, at) VALUES (?,?,?,?,?)",
        (row["dev"], row["month"], amount, row["id"], now_iso()),
    )
    ctx.audit("budget.granted", {"dev": row["dev"], "month": row["month"], "limit_brl": amount, "request": row["id"]})
    return amount


def cost_raise_pct(ctx: AppContext) -> float:
    return float((_cfg(ctx).get("on_continue") or {}).get("cost_raise_pct", 50.0))
