"""Distribuição: resumo semanal por squad no Teams, relatório mensal de FinOps e issues sistêmicas
no nexo-brain; agendamento seguro para várias instâncias (cada job × período roda uma vez só).

Configuração (variáveis do coletor):
- `NX_COLLECTOR_TEAMS_WEBHOOKS` — JSON `{"<squad>": url, "finops": url, "default": url}` (Incoming Webhook/Workflow)
- `NX_COLLECTOR_GITHUB_TOKEN`, `NX_COLLECTOR_BRAIN_REPO` (org/repo) — issues sistêmicas
- `NX_COLLECTOR_PUBLIC_URL` — links do dashboard nos cards; `NX_COLLECTOR_TZ` (padrão America/Sao_Paulo)
- `NX_COLLECTOR_SCHEDULER=1` — liga o agendador interno (senão, CronJob chamando `/v2/admin/jobs/<job>`)
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
import urllib.request
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from nexo.central import insights as insight_engine
from nexo.central.db import CollectorDB
from nexo.central.metrics import Filters, Scope
from nexo.central.metrics.pages.finops import finops
from nexo.central.metrics.pages.squad import squad
from nexo.central.metrics.scope import _month_add
from nexo.central.visibility import Principal
from nexo.kernel.finops.money import brl

JOBS = ("insights", "weekly", "monthly_finops", "issues", "siem")


def _allowed_url(url: str) -> bool:
    return url.startswith("https://") or url.startswith(("http://127.0.0.1:", "http://localhost:"))


def _urllib_post(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
    if not _allowed_url(url):
        raise ValueError("destino precisa ser https")
    req = urllib.request.Request(  # noqa: S310 — esquema validado acima
        url, method="POST", data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
        raw = resp.read()
        try:
            return resp.status, dict(json.loads(raw or b"{}"))
        except (ValueError, TypeError):
            return resp.status, {}


transport: Callable[[str, dict[str, Any], dict[str, str]], tuple[int, dict[str, Any]]] = _urllib_post


def _log(d: CollectorDB, channel: str, kind: str, target: str, status: str, payload: dict[str, Any]) -> None:
    d.execute(
        "INSERT INTO deliveries (id, at, channel, kind, target, status, payload) VALUES (?,?,?,?,?,?,?)",
        (uuid.uuid4().hex, time.time(), channel, kind, target, status, json.dumps(payload, ensure_ascii=False)),
    )


def _webhooks() -> dict[str, str]:
    return {str(k): str(v) for k, v in dict(json.loads(os.environ.get("NX_COLLECTOR_TEAMS_WEBHOOKS", "{}"))).items()}


def _public_url() -> str:
    return os.environ.get("NX_COLLECTOR_PUBLIC_URL", "").rstrip("/")


# ---------------------------------------------------------------- cards (Adaptive Card 1.4)


def card(title: str, facts: list[tuple[str, str]], lines: list[str], link: str | None) -> dict[str, Any]:
    body: list[dict[str, Any]] = [
        {"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium", "wrap": True},
        {"type": "FactSet", "facts": [{"title": k, "value": v} for k, v in facts]},
    ]
    body += [{"type": "TextBlock", "text": line, "wrap": True, "spacing": "Small"} for line in lines]
    content: dict[str, Any] = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
    }
    if link:
        content["actions"] = [{"type": "Action.OpenUrl", "title": "Abrir no dashboard", "url": link}]
    return {
        "type": "message",
        "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": content}],
    }


def validate_card(payload: dict[str, Any]) -> list[str]:
    """Checagem estrutural do payload do Teams (o que o conector exige) e da ausência de dados pessoais."""
    problems = []
    if payload.get("type") != "message":
        problems.append("type != message")
    atts = payload.get("attachments") or []
    if len(atts) != 1 or atts[0].get("contentType") != "application/vnd.microsoft.card.adaptive":
        problems.append("anexo adaptive card ausente")
        return problems
    content = atts[0].get("content") or {}
    if content.get("type") != "AdaptiveCard" or content.get("version") not in ("1.2", "1.3", "1.4", "1.5"):
        problems.append("AdaptiveCard/versão inválidos")
    for block in content.get("body") or []:
        if block.get("type") not in ("TextBlock", "FactSet"):
            problems.append(f"bloco não suportado: {block.get('type')}")
    for action in content.get("actions") or []:
        if action.get("type") != "Action.OpenUrl" or not str(action.get("url", "")).startswith(("https://", "http")):
            problems.append("ação inválida")
    if len(json.dumps(payload)) > 28_000:
        problems.append("payload acima de 28 KB")
    return problems


def _fmt(value: Any, kind: str = "num") -> str:
    if value is None:
        return "—"
    if kind == "brl":
        return brl(float(value))
    if kind == "pct":
        return f"{float(value):.1f}%"
    return str(value)


SYSTEM = Principal(dev="nexo-collector", role="director")


def weekly_summary(d: CollectorDB, now: datetime | None = None) -> list[dict[str, Any]]:
    """Resumo semanal por squad (segunda, 9h): números do squad e insights abertos — sem recortes individuais."""
    now = now or datetime.now(UTC)
    hooks = _webhooks()
    squads = sorted(
        {str(r["squad"]) for r in d.query("SELECT DISTINCT squad FROM fact_story") if r["squad"]}
        | {str(r["squad"]) for r in d.query("SELECT squad FROM dim_squad")}
    )
    sent = []
    from nexo.central import flags

    for name in squads:
        if not flags.enabled(d, "weekly_summary", name):  # desligado para o squad
            continue
        url = hooks.get(name) or hooks.get("default")
        if not url:
            continue
        month = now.strftime("%Y-%m")
        scope = Scope(Filters(_month_add(month, -1), month, squad=name), {name}, SYSTEM, now)
        data = squad(d, scope, SYSTEM.dev)
        k = {x["id"]: x for x in data["kpis"]}
        principal = Principal(dev="nexo-collector", role="lead", member={name})
        open_insights = [
            i for i in insight_engine.list_for(d, principal, "aberto") if i["squad"] == name and not i["individual"]
        ][:5]
        link = f"{_public_url()}/dash/squad?squad={name}" if _public_url() else None
        payload = card(
            f"Nexo — resumo semanal · squad {name}",
            [
                ("Concluídas (período)", _fmt(k["done"]["value"])),
                ("Em andamento", _fmt(k["in_progress"]["value"])),
                ("FPY", _fmt(k["fpy"]["value"], "pct")),
                ("Espera por humano (h)", _fmt(k["waiting"]["value"])),
                ("Bugs", _fmt(k["bugs"]["value"])),
            ],
            [f"• [{i['severity']}] {i['title']}" for i in open_insights] or ["Nenhum insight aberto."],
            link,
        )
        sent.append(_deliver(d, "teams", "weekly", name, url, payload))
    return sent


def _top_centers(tables: list[dict[str, Any]], limit: int = 3) -> list[tuple[str, str]]:
    """os centros de custo que mais gastaram no mês, para o cartão (o detalhe fica no dashboard/CSV)."""
    rows: list[list[Any]] = next((t["rows"] for t in tables if t["id"] == "chargeback"), [])
    return [(f"Centro de custo {r[0]}", _fmt(r[5], "brl")) for r in rows[:limit]]


def monthly_finops(
    d: CollectorDB, month: str | None = None, now: datetime | None = None, deliver: bool = True
) -> dict[str, Any]:
    """Relatório mensal de FinOps (dia 1, mês anterior): uso × alocado, ACU, conciliação, insights de custo."""
    now = now or datetime.now(UTC)
    month = month or _month_add(now.strftime("%Y-%m"), -1)
    principal = Principal(dev="nexo-collector", role="director")  # só agregado: sem tabelas por pessoa
    scope = Scope(Filters(month, month), None, principal, now)
    data = finops(d, scope, principal.dev)
    k = {x["id"]: x for x in data["kpis"]}
    cost_insights = [
        i
        for i in insight_engine.list_for(d, principal)
        if i["detector"] in and i["state"] in ("aberto", "reconhecido") and not i["individual"]
    ]
    report = {
        "month": month,
        "kpis": data["kpis"],
        "tables": [t for t in data["tables"] if not t["individual"]],
        "insights": cost_insights,
    }
    from nexo.central import flags

    url = _webhooks().get("finops")
    if url and deliver and flags.enabled(d, "finops_report"):
        payload = card(
            f"Nexo — FinOps {month}",
            [
                ("Custo de uso", _fmt(k["usage"]["value"], "brl")),
                ("Alocado (assinaturas)", _fmt(k["allocated"]["value"], "brl")),
                ("ACU usados / inclusos", f"{_fmt(k['acu_used']['value'])} / {_fmt(k['acu_included']['value'])}"),
                ("Câmbio aplicado", _fmt(k["fx"]["value"])),
                *_top_centers(report["tables"]),
            ],
            [f"• {i['title']}" for i in cost_insights] or ["Sem divergências nem licenças ociosas."],
            f"{_public_url()}/dash/finops?month_from={month}&month_to={month}" if _public_url() else None,
        )
        report["delivery"] = _deliver(d, "teams", "monthly_finops", "finops", url, payload)
    return report


def systemic_issues(d: CollectorDB) -> list[dict[str, Any]]:
    """Insights sistêmicos abertos viram issue no nexo-brain."""
    from nexo.central import flags

    if not flags.enabled(d, "systemic_issues"):
        return []
    token = os.environ.get("NX_COLLECTOR_GITHUB_TOKEN", "")
    repo = os.environ.get("NX_COLLECTOR_BRAIN_REPO", "")
    api = os.environ.get("NX_COLLECTOR_GITHUB_API", "https://api.github.com").rstrip("/")
    rows = d.query(
        "SELECT * FROM insights WHERE systemic = 1 AND state IN ('aberto', 'reconhecido')"
        " AND issue_url IS NULL ORDER BY first_seen"
    )
    out = []
    for row in rows:
        evidence = json.loads(row["evidence"] or "{}")
        body = {
            "title": f"[insight {row['detector']}] {row['title']}"[:250],
            "body": "Aberto automaticamente pelo coletor do Nexo. Nenhuma mudança é aplicada sem PR"
            " aprovado.\n\n"
            f"- Detector: {row['detector']}\n- Severidade: {row['severity']}\n- Ação sugerida: {row['action']}\n"
            f"- Evidência:\n```json\n{json.dumps(evidence, ensure_ascii=False, indent=2)}\n```\n",
            "labels": ["insight", str(row["detector"])],
        }
        if not (token and repo):
            _log(d, "github", "issue", str(row["id"]), "skipped", body)
            out.append({"insight": row["id"], "status": "skipped"})
            continue
        try:
            status, answer = transport(
                f"{api}/repos/{repo}/issues",
                body,
                {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            )
        except (OSError, ValueError) as exc:
            _log(d, "github", "issue", str(row["id"]), f"error: {exc}"[:200], body)
            out.append({"insight": row["id"], "status": "error"})
            continue
        url = str(answer.get("html_url") or "")
        if status < 300 and url:
            d.execute("UPDATE insights SET issue_url = ? WHERE id = ?", (url, row["id"]))
        _log(d, "github", "issue", str(row["id"]), str(status), body)
        out.append({"insight": row["id"], "status": status, "url": url})
    return out


def _deliver(d: CollectorDB, channel: str, kind: str, target: str, url: str, payload: dict[str, Any]) -> dict[str, Any]:
    problems = validate_card(payload)
    if problems:
        _log(d, channel, kind, target, "invalid: " + "; ".join(problems), payload)
        return {"target": target, "status": "invalid", "problems": problems}
    try:
        status, _ = transport(url, payload, {})
    except (OSError, ValueError) as exc:
        _log(d, channel, kind, target, f"error: {exc}"[:200], payload)
        return {"target": target, "status": "error"}
    _log(d, channel, kind, target, str(status), payload)
    return {"target": target, "status": status}


# ---------------------------------------------------------------- agendamento


def due(job: str, local: datetime) -> str | None:
    """Período devido do job no horário local (ou None): diário 06h, semanal segunda 9h, mensal dia 1 9h."""
    if job == "insights":
        return local.strftime("%Y-%m-%d") if local.hour >= 6 else None
    if job == "issues":
        return local.strftime("%Y-%m-%d") if local.hour >= 7 else None
    if job == "siem":  # reenvio da fila do SIEM a cada 5 minutos
        return local.strftime("%Y-%m-%dT%H:") + f"{local.minute // 5 * 5:02d}"
    if job == "weekly":
        monday = (local - timedelta(days=local.weekday())).replace(hour=9, minute=0, second=0, microsecond=0)
        iso = local.isocalendar()
        return f"{iso[0]}-S{iso[1]:02d}" if local >= monday else None
    if job == "monthly_finops":
        start = local.replace(day=1, hour=9, minute=0, second=0, microsecond=0)
        return _month_add(local.strftime("%Y-%m"), -1) if local >= start else None
    raise ValueError(f"job desconhecido: {job}")


def run_job(d: CollectorDB, job: str, period: str | None = None, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    if job == "insights":
        result: Any = insight_engine.run(d, now)
    elif job == "weekly":
        result = weekly_summary(d, now)
    elif job == "monthly_finops":
        result = monthly_finops(d, period if period and len(period) == 7 else None, now)
    elif job == "issues":
        result = systemic_issues(d)
    elif job == "siem":
        from nexo.central import siem

        result = siem.flush(d)
    else:
        raise ValueError(f"job desconhecido: {job}")
    return {"job": job, "period": period, "result": result}


def tick(d: CollectorDB, now: datetime | None = None) -> list[dict[str, Any]]:
    """Roda os jobs devidos; a chave (job, período) garante execução única mesmo com várias instâncias."""
    now = now or datetime.now(UTC)
    local = now.astimezone(ZoneInfo(os.environ.get("NX_COLLECTOR_TZ", "America/Sao_Paulo")))
    done = []
    for job in JOBS:
        period = due(job, local)
        if period is None:
            continue
        claimed = d.execute(
            "INSERT INTO jobs (job, period, at, status) VALUES (?, ?, ?, 'running') ON CONFLICT DO NOTHING",
            (job, period, time.time()),
        )
        if not claimed:
            continue
        try:
            out = run_job(d, job, period, now)
            d.execute(
                "UPDATE jobs SET status = 'ok', detail = ? WHERE job = ? AND period = ?",
                (json.dumps(out["result"], default=str)[:4000], job, period),
            )
        except Exception as exc:
            d.execute(
                "UPDATE jobs SET status = 'error', detail = ? WHERE job = ? AND period = ?",
                (str(exc)[:2000], job, period),
            )
            out = {"job": job, "period": period, "error": str(exc)}
        done.append(out)
    return done


def start_scheduler(get_db: Callable[[], CollectorDB], interval: float = 60.0) -> threading.Thread:
    def loop() -> None:
        while True:
            with contextlib.suppress(Exception):  # o agendador não pode morrer; falhas ficam em `jobs`
                tick(get_db())
            time.sleep(interval)

    thread = threading.Thread(target=loop, name="nexo-scheduler", daemon=True)
    thread.start()
    return thread
