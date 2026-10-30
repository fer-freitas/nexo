"""Telemetria central automática: outbox local, envio em segundo plano, identidade do dispositivo,
atraso tolerado e transparência. Só metadados do contrato `nexo.telemetry/v1` (domínio `telemetry`)."""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from nexo import __version__
from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.monitoring.telemetry import CONTRACT, MAX_BATCH_BYTES
from nexo.kernel.platform import config
from nexo.kernel.runtime import http
from nexo.kernel.secrets import credentials

TRUSTED = ("default", "org", "squad", "anchor")
DAY = 86400.0


def _cfg(ctx: AppContext) -> dict[str, Any]:
    """Configuração efetiva: `enabled`, `url`, `required`, `max_delay_days` e `background` vêm só das camadas
    confiáveis (padrão, org, squad); o dev não desliga a telemetria nem a desvia para outro endereço."""
    from nexo.kernel.config.cascade import resolve

    raw = dict(config.resolved(ctx).values.get("telemetry") or {})
    trusted_layers = [layer for layer in config.load_layers(ctx) if layer.level in ("default", "org", "squad")]
    trusted = dict(resolve(trusted_layers).values.get("telemetry") or {})
    keys = ("enabled", "url", "required", "max_delay_days", "background", "external_usage", "project_name")
    return {**{k: v for k, v in raw.items() if k not in keys}, **{k: trusted.get(k) for k in keys}}


def ignored_settings(ctx: AppContext) -> list[str]:
    """Chaves de telemetria definidas no projeto/pessoal que foram ignoradas (para o doctor)."""
    res = config.resolved(ctx)
    keys = ("enabled", "url", "required", "max_delay_days", "background", "external_usage", "project_name")
    return sorted(
        k for k, o in res.origin.items() if k.startswith("telemetry.") and k.split(".")[1] in keys and o not in TRUSTED
    )


def enabled(ctx: AppContext) -> bool:
    return bool(_cfg(ctx).get("enabled"))


def device_id(ctx: AppContext) -> str:
    path = ctx.home / "device.json"
    if path.is_file():
        return str(json.loads(path.read_text(encoding="utf-8"))["device_id"])
    ctx.home.mkdir(parents=True, exist_ok=True)
    value = uuid.uuid4().hex
    path.write_text(json.dumps({"device_id": value}), encoding="utf-8")
    return value


def _url(ctx: AppContext) -> str:
    url = str(_cfg(ctx).get("url") or "").rstrip("/")
    if not url:
        raise NexoError("telemetria sem endereço do coletor (telemetry.url na org)")
    local = url.startswith(("http://127.0.0.1", "http://localhost"))  # coletor local de desenvolvimento/demo
    if not url.startswith("https://") and not local:
        raise NexoError("telemetry.url deve usar https://")
    return url


def _project(ctx: AppContext) -> str:
    return hashlib.sha256(str(ctx.project_root or "global").encode()).hexdigest()[:16]


def fleet_info(ctx: AppContext) -> dict[str, Any]:
    """Versões do cérebro e das CLIs dos agentes e o SO, com cache diário — só no envio em segundo plano."""
    path = ctx.home / "fleet.json"
    if path.is_file():
        cached = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - float(cached.get("at", 0)) < 86400:
            return dict(cached["info"])
    import platform

    from nexo.kernel import brain as brain_adapter
    from nexo.kernel import services

    agents_app = services.load("execution.agents")

    agents: dict[str, str] = {}
    with contextlib.suppress(Exception):
        for name, adapter in agents_app.adapters(ctx).items():
            tool = adapter.detect()
            if tool.installed:
                agents[name] = str(tool.version or "?")[:40]
    info: dict[str, Any] = {
        "brain": str((brain_adapter.cached_status(ctx.brain_dir) or {}).get("version") or ""),
        "agents": agents,
        "os": platform.system().lower(),
    }
    path.write_text(json.dumps({"at": time.time(), "info": info}), encoding="utf-8")
    return info


def project_name(ctx: AppContext, values: dict[str, Any]) -> str:
    """Nome do projeto para o dashboard (o `project` da configuração, ou o nome da pasta) — nunca o caminho.
    A org desliga com `telemetry.project_name: false`."""
    if ctx.project_root is None or _cfg(ctx).get("project_name") is False:
        return ""
    name = str(values.get("project") or ctx.project_root.name).strip()
    return name[:80] if name and "/" not in name and "\\" not in name else ""


def batch_context(ctx: AppContext) -> dict[str, Any]:
    """Projeto (hash do caminho), squad e stack do momento do evento — gravados junto na outbox."""
    cached = ctx.__dict__.get("_telemetry_context")
    if cached is None:
        # provisório antes de resolver: a resolução pode auditar (policy.violation) e voltar aqui
        ctx.__dict__["_telemetry_context"] = {"project": _project(ctx), "squad": "", "stack": ""}
        values = config.resolved(ctx).values if ctx.project_root else {}
        stacks = values.get("stacks") or []
        cached = {
            "project": _project(ctx),
            "squad": str(values.get("squad") or ""),
            "stack": str(stacks[0]) if stacks else "",
        }
        name = project_name(ctx, values)
        if name:
            cached["project_name"] = name
        ctx.__dict__["_telemetry_context"] = cached
    return dict(cached)


# ---------------------------------------------------------------- identidade do dispositivo


def _secret_name(ctx: AppContext) -> str:
    """Um segredo por dispositivo: duas instalações (NX_HOME) na mesma máquina não compartilham token."""
    return f"central:{device_id(ctx)}"


def _tokens(ctx: AppContext) -> dict[str, Any]:
    raw = credentials.get_secret(_secret_name(ctx))
    return dict(json.loads(raw)) if raw else {}


def _save_tokens(ctx: AppContext, data: dict[str, Any]) -> None:
    credentials.set_secret(_secret_name(ctx), json.dumps(data))


def login_central(ctx: AppContext, show: Any, wait: float = 5.0, timeout: float = 600.0) -> dict[str, Any]:
    """Fluxo de código de dispositivo: a pessoa aprova no navegador com a conta corporativa; a CLI recebe um token
    curto (renovado sozinho) guardado só no keyring."""
    url = _url(ctx)
    start = http.request("POST", f"{url}/v2/device/code", None, {"device_id": device_id(ctx)}).body
    show(start["verification_uri"], start["user_code"])
    deadline = time.time() + min(timeout, float(start.get("expires_in", timeout)))
    while time.time() < deadline:
        resp = http.request(
            "POST", f"{url}/v2/device/token", None, {"device_code": start["device_code"]}, expect=(200, 428)
        )
        if resp.status == 200:
            body = dict(resp.body)
            _save_tokens(ctx, {**body, "expires_at": time.time() + float(body["expires_in"])})
            ctx.audit("telemetry.device_registered", {"device": device_id(ctx)})
            return {"device_id": device_id(ctx), "expires_in": body["expires_in"]}
        time.sleep(max(0.0, min(wait, float(start.get("interval", wait)))))
    raise NexoError("aprovação do dispositivo expirou; rode `nexo login central` de novo")


def _bearer(ctx: AppContext) -> str:
    tokens = _tokens(ctx)
    if tokens.get("access_token"):
        if float(tokens.get("expires_at", 0)) - time.time() < 300:  # renova 5 min antes de expirar
            body = http.request(
                "POST", f"{_url(ctx)}/v2/device/refresh", None, {"refresh_token": tokens["refresh_token"]}
            ).body
            tokens = {**tokens, **dict(body), "expires_at": time.time() + float(body["expires_in"])}
            _save_tokens(ctx, tokens)
        return str(tokens["access_token"])
    legacy = credentials.get_secret("collector")  # token fixo (gestores, integrações)
    if legacy:
        return legacy
    raise NexoError("dispositivo não registrado no coletor: rode `nexo login central`")


# ---------------------------------------------------------------- fatos de consumo


def _story_for(ctx: AppContext, target: str) -> str | None:
    """História do alvo do passo: a própria (US-…, com sufixos de QA/correção) ou a dona da tarefa (T-…)."""
    if target.startswith("US-"):
        return target.split("-QA")[0].split("-FIX")[0].split("-CHAR")[0]
    if not target.startswith("T-") or ctx.project_root is None:
        return None
    task = target.split("-FIX")[0]
    owners = ctx.__dict__.get("_task_owners")
    if owners is None or task not in owners:
        from nexo.kernel.workspace import specs

        owners = {}
        for folder in sorted(specs.specs_root(ctx.project_root).glob("F-*")):
            backlog = specs.load_backlog(folder) if folder.is_dir() else None
            for item in backlog.tasks if backlog else []:
                owners[item.id] = item.story
        ctx.__dict__["_task_owners"] = owners
    return owners.get(task)


def enqueue_step(ctx: AppContext, step_id: str) -> None:
    """Consumo do passo vira fato na outbox logo que o passo termina (tokens, ACU, premium requests, R$)."""
    if not ctx.events._outbox:
        return
    from nexo.kernel import brain as brain_adapter
    from nexo.kernel import services
    from nexo.kernel.finops.costs import usage_cost_brl

    costs = services.load("finops.costs")
    from nexo.store.events import enqueue

    row = ctx.conn.execute(
        "SELECT s.id, s.run_id, s.role, s.tool, s.model, s.started_at, s.ended_at, s.outcome, s.error_kind,"
        " s.tool_calls, r.target_id FROM steps s JOIN runs r ON r.id = s.run_id WHERE s.id = ?",
        (step_id,),
    ).fetchone()
    if row is None:
        return
    line = next((ln for ln in costs.usage_lines(ctx) if ln.step_id == step_id), None)
    units = dict(line.units) if line else {}
    started = row["started_at"] or ""
    seconds = None
    if row["started_at"] and row["ended_at"]:
        from datetime import datetime

        seconds = round((datetime.fromisoformat(row["ended_at"]) - datetime.fromisoformat(started)).total_seconds(), 2)
    target = str(row["target_id"])
    fact = {
        "step": row["id"],
        "run": row["run_id"],
        "target": target,
        "story": _story_for(ctx, target),
        "role": row["role"],
        "tool": row["tool"],
        "model": row["model"],
        "started_at": started,
        "seconds": seconds,
        "outcome": row["outcome"],
        "error_kind": row["error_kind"],
        "tokens_in": units.get("tokens_in"),
        "tokens_out": units.get("tokens_out"),
        "cache_read": units.get("cache_read"),
        "cache_write": units.get("cache_write"),
        "premium_request": units.get("premium_request"),
        "acu": units.get("acu"),
        "ai_credits": units.get("ai_credits"),
        "api_equiv_usd": round(line.api_equiv_usd, 6) if line else 0.0,
        "usage_brl": round(usage_cost_brl(line, costs.pricing(ctx)), 4) if line else 0.0,
        "estimated": bool(line.estimated or line.tool in _estimated_tools(ctx)) if line else False,
        "brain_version": str((brain_adapter.cached_status(ctx.brain_dir) or {}).get("version") or ""),
        "project": _project(ctx),
        "tool_calls": row["tool_calls"],
    }
    ctx.conn.execute("BEGIN IMMEDIATE")
    try:
        enqueue(ctx.conn, "step", "step", started or time.strftime("%Y-%m-%dT%H:%M:%S"), fact, 0, batch_context(ctx))
        ctx.conn.execute("COMMIT")
    except Exception:
        ctx.conn.execute("ROLLBACK")
        raise


def enqueue_story(ctx: AppContext, story_id: str) -> None:
    """Retrato da história a cada transição: o central guarda o mais recente por projeto × história."""
    if not ctx.events._outbox or ctx.project_root is None:
        return
    from nexo.kernel import services

    metrics = services.load("finops.metrics")
    from nexo.store.events import enqueue

    facts = metrics.story_facts(ctx, only=story_id)
    if not facts:
        return
    f = facts[0]
    snapshot = {
        "story": f.story,
        "feature": f.feature,
        "stack": f.stack,
        "points": f.points,
        "state": f.state,
        "human_gate": f.human_gate,
        "escalated": f.escalated,
        "bugs": f.bugs,
        "attempts": f.attempts,
        "spec_returns": f.spec_returns,
        "clarifications": f.clarifications,
        "handoffs": f.handoffs,
        "lead_hours": f.lead_hours,
        "cycle_hours": f.cycle_hours,
        "waiting_human_hours": f.waiting_human_hours,
        "cost_brl": f.cost_brl,
        "cap_brl": f.cap_brl,
        "estimate_brl": f.estimate_brl,
        "started_at": f.started_at,
        "done_at": f.done_at,
        "project": _project(ctx),
    }
    from datetime import UTC, datetime

    at = datetime.now(UTC).isoformat(timespec="milliseconds")
    ctx.conn.execute("BEGIN IMMEDIATE")
    try:
        enqueue(ctx.conn, "story", "story", at, snapshot, 0, batch_context(ctx))
        ctx.conn.execute("COMMIT")
    except Exception:
        ctx.conn.execute("ROLLBACK")
        raise


# ---------------------------------------------------------------- envio


def _batches(rows: list[Any]) -> list[list[Any]]:
    """Lotes de até 64 KB, um contexto (projeto/squad/stack) por lote."""
    out: list[list[Any]] = []
    size = 0
    for r in rows:
        n = len(r["payload"]) + 200
        if not out or out[-1][0]["context"] != r["context"] or size + n > MAX_BATCH_BYTES:
            out.append([])
            size = 0
        out[-1].append(r)
        size += n
    return out


def flush(ctx: AppContext, max_rows: int = 5000) -> dict[str, Any]:
    """Envia a outbox pendente. Pelo menos uma vez; o coletor deduplica por `event_id`. Falha → espera crescente."""
    if not enabled(ctx):
        raise NexoError("telemetria desligada (telemetry.enabled na org)")
    from nexo.kernel import services

    external_usage = services.load("usage.external")

    with contextlib.suppress(NexoError, OSError, ValueError):  # falha na varredura nunca impede o envio
        external_usage.auto(ctx)
    now = time.time()
    rows = ctx.conn.execute(
        "SELECT * FROM outbox WHERE status = 'pending' AND next_attempt_at <= ? ORDER BY id LIMIT ?", (now, max_rows)
    ).fetchall()
    result: dict[str, Any] = {"sent": 0, "accepted": 0, "duplicate": 0, "rejected": 0, "error": None}
    if not rows:
        return {**result, "pending": pending_count(ctx)}
    url = _url(ctx)
    try:
        headers = {"Authorization": f"Bearer {_bearer(ctx)}"}
        oldest = min(float(r["created_at"]) for r in rows)
        rows = sorted(rows, key=lambda r: (r["context"], r["id"]))
        for batch in _batches(list(rows)):
            context = dict(json.loads(batch[0]["context"] or "{}"))
            body = {
                "contract": CONTRACT,
                "device_id": device_id(ctx),
                "project": str(context.pop("project", "") or _project(ctx)),
                "context": {
                    **{k: v for k, v in context.items() if k in ("squad", "stack", "project_name")},
                    "nexo": __version__,
                    "lag_hours": round((time.time() - oldest) / 3600, 2),
                    **fleet_info(ctx),
                },
                "events": [
                    {
                        "event_id": r["event_id"],
                        "kind": r["kind"],
                        "seq": r["seq"],
                        "type": r["type"],
                        "at": r["at"],
                        "payload": json.loads(r["payload"]),
                    }
                    for r in batch
                ],
            }
            answer = dict(http.request("POST", f"{url}/v2/events", headers, body).body)
            done = set(answer.get("accepted") or []) | set(answer.get("duplicate") or [])
            rejected = {str(x["event_id"]): str(x["reason"]) for x in answer.get("rejected") or []}
            for r in batch:
                if r["event_id"] in done:
                    ctx.conn.execute(
                        "UPDATE outbox SET status = 'sent', sent_at = ? WHERE id = ?", (time.time(), r["id"])
                    )
                elif r["event_id"] in rejected:
                    ctx.conn.execute(
                        "UPDATE outbox SET status = 'rejected', sent_at = ?, last_error = ? WHERE id = ?",
                        (time.time(), rejected[r["event_id"]], r["id"]),
                    )
            result["sent"] += len(batch)
            result["accepted"] += len(answer.get("accepted") or [])
            result["duplicate"] += len(answer.get("duplicate") or [])
            result["rejected"] += len(rejected)
            events = [r for r in batch if r["kind"] == "event" and r["seq"]]
            if events:
                last = ctx.conn.execute(
                    "SELECT seq, hash, at FROM events WHERE seq = ?", (events[-1]["seq"],)
                ).fetchone()
                if last:
                    http.request(
                        "POST",
                        f"{url}/v2/anchors",
                        headers,
                        {"device_id": device_id(ctx), "seq": last["seq"], "hash": last["hash"], "at": last["at"]},
                    )
    except (NexoError, OSError) as exc:
        message = exc.message if isinstance(exc, NexoError) else str(exc)
        for r in rows:
            if r["status"] == "pending":
                attempts = int(r["attempts"]) + 1
                ctx.conn.execute(
                    "UPDATE outbox SET attempts = ?, next_attempt_at = ?, last_error = ?"
                    " WHERE id = ? AND status = 'pending'",
                    (attempts, time.time() + min(3600.0, 30.0 * 2 ** min(attempts, 7)), message[:300], r["id"]),
                )
        result["error"] = message
    (ctx.home / "telemetry.last").write_text(json.dumps({"at": time.time(), **result}), encoding="utf-8")
    return {**result, "pending": pending_count(ctx)}


push = flush  # compatibilidade com `nexo admin telemetry push`


def pending_count(ctx: AppContext) -> int:
    return int(ctx.conn.execute("SELECT COUNT(*) FROM outbox WHERE status = 'pending'").fetchone()[0])


def show(ctx: AppContext, only_pending: bool = False, limit: int = 50) -> list[dict[str, Any]]:
    """Exatamente o que foi e o que será enviado."""
    rows = ctx.conn.execute(
        "SELECT * FROM outbox WHERE (? = 0 OR status = 'pending') ORDER BY id DESC LIMIT ?", (int(only_pending), limit)
    ).fetchall()
    return [
        {
            "event_id": r["event_id"],
            "kind": r["kind"],
            "type": r["type"],
            "at": r["at"],
            "status": r["status"],
            "attempts": r["attempts"],
            "payload": json.loads(r["payload"]),
            "last_error": r["last_error"],
        }
        for r in rows
    ]


def health(ctx: AppContext) -> dict[str, Any]:
    oldest = ctx.conn.execute("SELECT MIN(created_at) FROM outbox WHERE status = 'pending'").fetchone()[0]
    last_path = ctx.home / "telemetry.last"
    last = json.loads(last_path.read_text(encoding="utf-8")) if last_path.is_file() else {}
    return {
        "enabled": enabled(ctx),
        "contract": CONTRACT,
        "device_id": device_id(ctx) if enabled(ctx) else None,
        "registered": bool(_tokens(ctx)) or bool(credentials.get_secret("collector")),
        "pending": pending_count(ctx),
        "oldest_pending_days": round((time.time() - float(oldest)) / DAY, 2) if oldest else 0.0,
        "last_send": last.get("at"),
        "last_error": last.get("error"),
        "ignored_settings": ignored_settings(ctx),
    }


def delay(ctx: AppContext) -> tuple[str, float]:
    """RN-101: pendência mais antiga > aviso → avisa; > bloqueio → novas execuções de agente param."""
    if not enabled(ctx):
        return "ok", 0.0
    limits = dict(_cfg(ctx).get("max_delay_days") or {})
    days = float(health(ctx)["oldest_pending_days"])
    if days > float(limits.get("block", 7)):
        return "block", days
    if days > float(limits.get("warn", 3)):
        return "warn", days
    return "ok", days


def check_delay(ctx: AppContext) -> None:
    level, days = delay(ctx)
    if level == "block":
        raise NexoError(
            f"telemetria atrasada há {days:.1f} dias: execuções de agente bloqueadas até sincronizar "
            "(`nexo admin telemetry flush`; verifique rede e `nexo login central`)"
        )


def after_step(ctx: AppContext, step_id: str) -> None:
    """Chamado ao fim de cada passo: registra o fato e dispara o envio sem atrasar o dev."""
    if not enabled(ctx):
        return
    enqueue_step(ctx, step_id)
    trigger(ctx)


def trigger(ctx: AppContext) -> None:
    from nexo.kernel.platform import observability

    outbox = enabled(ctx) and pending_count(ctx) > 0
    spans = observability.pending(ctx) > 0
    if not outbox and not spans:
        return
    if not _cfg(ctx).get("background", True):
        with contextlib.suppress(NexoError, OSError):  # fica pendente; próxima tentativa com espera crescente
            if outbox:
                flush(ctx)
            if spans:
                observability.export(ctx)
        return
    stamp = ctx.home / "telemetry.spawned"
    if stamp.is_file() and time.time() - float(stamp.read_text(encoding="utf-8") or 0) < 60:
        return
    stamp.write_text(str(time.time()), encoding="utf-8")
    from nexo.kernel import services

    nexo_command = services.get("execution.agents", "nexo_command")

    kwargs: dict[str, Any] = (
        {"start_new_session": True}
        if sys.platform != "win32"
        else {"creationflags": getattr(subprocess, "DETACHED_PROCESS", 0)}
    )
    subprocess.Popen(
        [*nexo_command(), "--quiet", "telemetry", "flush"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )


def pull_budget(ctx: AppContext, month: str) -> dict[str, Any] | None:
    """Com `budget.source: central`, o limite do dev vem do coletor (fonte oficial do gestor)."""
    budget_cfg = dict(config.resolved(ctx).values.get("budget") or {})
    if budget_cfg.get("source") != "central":
        return None
    url = str(_cfg(ctx).get("url") or "").rstrip("/")
    if not url:
        return None
    try:
        token = _bearer(ctx)
    except NexoError:
        return None
    return dict(http.request("GET", f"{url}/v2/budgets/me?month={month}", {"Authorization": f"Bearer {token}"}).body)


def send_baseline(ctx: AppContext, summary: dict[str, Any]) -> None:
    """Linha de base do projeto para o central: só números agregados, por squad × projeto (hash)."""
    context = batch_context(ctx)
    numbers = {k: v for k, v in summary.items() if k not in ("tracker", "sent", "send_error")}
    http.request(
        "POST",
        f"{_url(ctx)}/v2/baselines",
        {"Authorization": f"Bearer {_bearer(ctx)}"},
        {"project": context["project"], "squad": context["squad"], "data": numbers},
    )


# ---------------------------------------------------------------- insights do central


def insights(ctx: AppContext, state: str = "aberto") -> list[dict[str, Any]]:
    """Insights visíveis para a pessoa (matriz de visibilidade aplicada no coletor)."""
    url = _url(ctx)
    query = f"?state={state}" if state else ""
    body = http.request("GET", f"{url}/v2/insights{query}", {"Authorization": f"Bearer {_bearer(ctx)}"}).body
    return list(dict(body).get("items") or [])


def set_insight(ctx: AppContext, insight_id: str, state: str, reason: str) -> dict[str, Any]:
    url = _url(ctx)
    return dict(
        http.request(
            "POST",
            f"{url}/v2/insights/{insight_id}/state",
            {"Authorization": f"Bearer {_bearer(ctx)}"},
            {"state": state, "reason": reason},
        ).body
    )


# ---------------------------------------------------------------- conciliação


def chargeback(ctx: AppContext, month: str, csv_path: Path | None = None) -> dict[str, Any]:
    """custo do mês por centro de custo, calculado no central; opcionalmente salva o CSV para o FinOps."""
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise NexoError("mês no formato AAAA-MM")
    url = _url(ctx)
    headers = {"Authorization": f"Bearer {_bearer(ctx)}"}
    data = dict(http.request("GET", f"{url}/v2/finops/chargeback?month={month}", headers).body)
    if csv_path is not None:
        text = http.request("GET", f"{url}/v2/finops/chargeback?month={month}&format=csv", headers).body
        csv_path.write_text(str(text), encoding="utf-8", newline="")  # o CSV já vem com \r\n; sem conversão no Windows
        data["csv"] = str(csv_path)
    ctx.audit("finops.chargeback", {"month": month, "rows": len(data.get("rows") or []), "csv": csv_path is not None})
    return data


def reconcile(ctx: AppContext, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fatura/relatório do fornecedor × total medido no coletor; divergência > 5% é destacada."""
    url = _url(ctx)
    headers = {"Authorization": f"Bearer {_bearer(ctx)}"}
    out = []
    for row in rows:
        body = http.request("POST", f"{url}/v2/invoices", headers, row).body
        out.append(dict(body))
    ctx.audit("finops.reconciled", {"invoices": len(out), "divergent": sum(1 for r in out if r.get("divergent"))})
    return out


def _estimated_tools(ctx: AppContext) -> set[str]:
    """custo com preço de fábrica sai marcado como estimado no painel (calculado uma vez por comando)."""
    cached = ctx.__dict__.get("_estimated_tools")
    if cached is None:
        from nexo.kernel import services

        finops_pricing = services.load("finops.pricing")

        cached = ctx.__dict__["_estimated_tools"] = finops_pricing.estimated_tools(ctx)
    return set(cached)
