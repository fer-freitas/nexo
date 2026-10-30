"""Consumo de IA dentro e fora do Nexo: varredura local, resumo para o dev e envio agregado ao central.

A varredura lê os registros locais das ferramentas (Claude Code, Copilot, Devin) e guarda totais por dia ×
ferramenta × modelo, separando o que o Nexo abriu. O dev vê o próprio consumo com `nexo cost usage`.
O central só recebe
os totais de fora do Nexo quando a org liga `telemetry.external_usage` (e a flag `external_usage` está ligada),
sem pastas, projetos, sessões nem conteúdo.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, date, datetime
from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.finops.costs import UsageLine, usage_cost_brl
from nexo.kernel.finops.external import DayTotal, aggregate, claude_prices
from nexo.kernel.platform import config, flags
from nexo.modules.finops.app import costs
from nexo.modules.usage.adapters import local_sessions

STATE = "local_sessions.json"
AUTO_EVERY_S = 3600  # varredura automática no envio da telemetria, no máximo uma vez por hora


def _state(ctx: AppContext) -> dict[str, Any]:
    path = ctx.home / STATE
    return dict(json.loads(path.read_text(encoding="utf-8"))) if path.is_file() else {}


def _save_state(ctx: AppContext, data: dict[str, Any]) -> None:
    ctx.home.mkdir(parents=True, exist_ok=True)
    (ctx.home / STATE).write_text(json.dumps(data), encoding="utf-8")


def _brl(ctx: AppContext, total: DayTotal) -> float:
    pricing = costs.pricing(ctx)
    line = UsageLine("", "", total.tool, date.fromisoformat(total.day), "", dict(total.units), total.api_equiv_usd)
    try:
        return usage_cost_brl(line, pricing)
    except ValueError:  # sem cotação vigente no dia
        return 0.0


def _company_only(ctx: AppContext, rows: list[Any]) -> list[Any]:
    """Com projetos registrados, só eles contam: sessão fora de projeto conhecido não é contada nem enviada."""
    from nexo.kernel import services

    if not services.available("lineage.app"):
        return rows
    roots = [str(p) for p in services.load("lineage.app").known_projects()]
    if not roots or not bool(dict(config.resolved(ctx).values.get("usage") or {}).get("only_known_projects", True)):
        return rows
    return [r for r in rows if any(str(r.cwd).replace("\\", "/").startswith(x.replace("\\", "/")) for x in roots)]


def scan(ctx: AppContext, full: bool = False, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Relê os registros das ferramentas e atualiza os totais locais. `full` relê todos os arquivos do Claude."""
    flags.require(ctx, "external_usage")
    state = _state(ctx)
    since = 0.0 if full else float(state.get("claude_since", 0.0))
    started = time.time()
    rows = _company_only(ctx, local_sessions.scan_all(since=since, env=env))
    prices = claude_prices(dict((config.resolved(ctx).values.get("pricing") or {}).get("models") or {}))
    totals = aggregate(rows, str(ctx.home), prices)
    touched_claude_days = {t.day for t in totals if t.tool == "claude"}
    ctx.conn.execute("BEGIN IMMEDIATE")
    try:
        if full:
            ctx.conn.execute("DELETE FROM external_usage")
        else:  # dias do Claude relidos e todas as linhas do Copilot/Devin são recalculadas por inteiro
            ctx.conn.execute("DELETE FROM external_usage WHERE tool IN ('copilot', 'devin')")
            for day in touched_claude_days:
                ctx.conn.execute("DELETE FROM external_usage WHERE tool = 'claude' AND day = ?", (day,))
        for t in totals:
            ctx.conn.execute(
                "INSERT OR REPLACE INTO external_usage (tool, day, model, nexo, sessions, responses, units,"
                " api_equiv_usd, usage_brl, scanned_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    t.tool,
                    t.day,
                    t.model,
                    int(t.nexo),
                    t.sessions,
                    t.responses,
                    json.dumps(t.units, sort_keys=True),
                    t.api_equiv_usd,
                    _brl(ctx, t),
                    started,
                ),
            )
        ctx.conn.execute("COMMIT")
    except Exception:
        ctx.conn.execute("ROLLBACK")
        raise
    # próximo incremental relê arquivos alterados desde o início desta varredura, menos uma folga
    _save_state(ctx, {**state, "claude_since": started - 300, "scanned_at": started})
    ctx.audit("usage.scanned", {"days": len({t.day for t in totals}), "rows": len(totals), "full": full})
    return {"rows": len(totals), "sessions": sum(t.sessions for t in totals)}


def summary(ctx: AppContext, month: str | None = None) -> dict[str, Any]:
    """Consumo do mês por ferramenta, dentro e fora do Nexo, e a parte feita pelo Nexo (adoção)."""
    month = month or datetime.now(UTC).strftime("%Y-%m")
    rows = ctx.conn.execute(
        "SELECT tool, nexo, SUM(sessions) AS sessions, SUM(responses) AS responses, SUM(usage_brl) AS brl,"
        " GROUP_CONCAT(units, '\n') AS units FROM external_usage WHERE day LIKE ? GROUP BY tool, nexo ORDER BY tool",
        (f"{month}-%",),
    ).fetchall()
    tools: dict[str, dict[str, Any]] = {}
    raw_brl = {"nexo": 0.0, "fora": 0.0}
    for r in rows:
        units: dict[str, float] = {}
        for blob in str(r["units"] or "").split("\n"):
            for k, v in (json.loads(blob) if blob else {}).items():
                units[k] = round(units.get(k, 0.0) + float(v), 6)
        side = "nexo" if r["nexo"] else "fora"
        raw_brl[side] += float(r["brl"] or 0)
        entry = tools.setdefault(str(r["tool"]), {"nexo": {}, "fora": {}})
        entry[side] = {
            "sessions": int(r["sessions"] or 0),
            "responses": int(r["responses"] or 0),
            "brl": round(float(r["brl"] or 0), 2),
            "units": units,
        }
    total_nexo, total_out = raw_brl["nexo"], raw_brl["fora"]
    share = round(100 * total_nexo / (total_nexo + total_out), 2) if (total_nexo + total_out) else None
    return {
        "month": month,
        "tools": tools,
        "nexo_brl": round(total_nexo, 2),
        "outside_brl": round(total_out, 2),
        "nexo_share_pct": share,
        "scanned_at": _state(ctx).get("scanned_at"),
        "shared": sharing(ctx),
    }


def sharing(ctx: AppContext) -> bool:
    from nexo.kernel.platform import telemetry

    return (
        telemetry.enabled(ctx)
        and bool(telemetry._cfg(ctx).get("external_usage"))
        and flags.enabled(ctx, "external_usage")
    )


def enqueue(ctx: AppContext) -> int:
    """Totais de fora do Nexo vão para a outbox, um retrato por dia × ferramenta × modelo (vale o mais novo)."""
    if not sharing(ctx):
        return 0
    from nexo.kernel.platform import telemetry
    from nexo.store.events import enqueue as put

    at = datetime.now(UTC).isoformat(timespec="milliseconds")
    rows = ctx.conn.execute("SELECT * FROM external_usage WHERE nexo = 0 ORDER BY day").fetchall()
    context = {k: v for k, v in telemetry.batch_context(ctx).items() if k != "project"}
    context["project"] = "-"  # uso fora do Nexo não pertence a um projeto do Nexo
    ctx.conn.execute("BEGIN IMMEDIATE")
    try:
        for r in rows:
            units = json.loads(r["units"])
            payload = {
                "tool": r["tool"],
                "model": r["model"],
                "day": r["day"],
                "sessions": r["sessions"],
                "responses": r["responses"],
                **{k: units.get(k) for k in ("tokens_in", "tokens_out", "cache_read", "cache_write")},
                **{k: units.get(k) for k in ("premium_request", "ai_credits", "acu")},
                "api_equiv_usd": r["api_equiv_usd"],
                "usage_brl": r["usage_brl"],
            }
            put(ctx.conn, "usage", "usage.external", at, payload, 0, context)
        ctx.conn.execute("COMMIT")
    except Exception:
        ctx.conn.execute("ROLLBACK")
        raise
    return len(rows)


def auto(ctx: AppContext) -> None:
    """Chamado no envio da telemetria: varre e enfileira no máximo uma vez por hora, só se a org ligou."""
    if not sharing(ctx) or time.time() - float(_state(ctx).get("scanned_at") or 0) < AUTO_EVERY_S:
        return
    scan(ctx)
    enqueue(ctx)
