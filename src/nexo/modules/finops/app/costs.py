"""FinOps: custo de uso e alocado, fechamento do mês, orçamento por dev e alertas."""

from __future__ import annotations

import calendar
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.finops.costs import BudgetStatus, Pricing, UsageLine, allocate, usage_cost_brl
from nexo.kernel.finops.money import brl
from nexo.kernel.platform import config
from nexo.store.runs import now_iso, project_key


def pricing(ctx: AppContext) -> Pricing:
    return Pricing.from_mapping(dict(config.resolved(ctx).values.get("pricing") or {}))


def current_month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def next_month_epoch(month: str | None = None) -> float:
    y, m = (int(x) for x in (month or current_month()).split("-"))
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return datetime(y, m, 1, tzinfo=UTC).timestamp()


def usage_lines(
    ctx: AppContext, month: str | None = None, dev: str | None = None, project: str | None = None
) -> list[UsageLine]:
    sql = (
        "SELECT s.id AS step, s.tool, s.started_at, r.dev, r.target_id, r.project, u.unit, u.quantity,"
        " u.api_equiv_usd, u.estimated FROM usage u JOIN steps s ON s.id = u.step_id JOIN runs r ON r.id = s.run_id"
        " WHERE 1 = 1"
    )
    params: list[Any] = []
    if month:
        sql += " AND substr(s.started_at, 1, 7) = ?"
        params.append(month)
    if dev:
        sql += " AND r.dev = ?"
        params.append(dev)
    if project:
        sql += " AND r.project = ?"
        params.append(project)
    lines: dict[str, UsageLine] = {}
    for row in ctx.conn.execute(sql, params):
        line = lines.get(row["step"])
        if line is None:
            line = UsageLine(
                row["step"],
                row["dev"],
                row["tool"],
                date.fromisoformat(row["started_at"][:10]),
                row["target_id"],
                {},
                project=row["project"],
            )
            lines[row["step"]] = line
        line.units[row["unit"]] = line.units.get(row["unit"], 0.0) + float(row["quantity"])
        line.api_equiv_usd += float(row["api_equiv_usd"] or 0)
        line.estimated = line.estimated or bool(row["estimated"])
    return list(lines.values())


@dataclass
class CostRow:
    key: str
    usage_brl: float = 0.0
    allocated_brl: float = 0.0
    by_tool: dict[str, float] = field(default_factory=dict)
    units: dict[str, float] = field(default_factory=dict)
    estimated: bool = False


def _closed(ctx: AppContext, month: str) -> bool:
    return ctx.conn.execute("SELECT 1 FROM months_closed WHERE month = ?", (month,)).fetchone() is not None


def _allocations(ctx: AppContext, lines: list[UsageLine]) -> dict[str, float]:
    """Rateio do mês: fechado (congelado) quando existir; senão provisório com todo o uso do dev no mês."""
    result: dict[str, float] = {}
    months = {ln.day.strftime("%Y-%m") for ln in lines}
    p = pricing(ctx)
    for month in months:
        if _closed(ctx, month):
            for row in ctx.conn.execute(
                "SELECT step_id, fixed_brl + variable_brl AS total FROM allocations WHERE month = ?", (month,)
            ):
                result[row["step_id"]] = float(row["total"])
            continue
        devs = {ln.dev for ln in lines if ln.day.strftime("%Y-%m") == month}
        full = [ln for d in devs for ln in usage_lines(ctx, month, d)]
        for alloc in allocate(full, p):
            result[alloc.step_id] = alloc.total
    return result


def cost_report(
    ctx: AppContext,
    group_by: str,
    month: str | None = None,
    target_prefix: str | None = None,
    dev: str | None = None,
    all_projects: bool = False,
) -> dict[str, Any]:
    root = ctx.project_root
    project = None if all_projects or root is None else project_key(root)
    lines = usage_lines(ctx, month, dev, project)
    if target_prefix:
        lines = [
            ln
            for ln in lines
            if ln.target == target_prefix
            or ln.target.startswith(f"{target_prefix}-")
            or _belongs(ctx, ln.target, target_prefix)
        ]
    p = pricing(ctx)
    allocations = _allocations(ctx, lines)
    rows: dict[str, CostRow] = {}
    experiments = round(sum(usage_cost_brl(ln, p) for ln in lines if ln.target.startswith("TRY-")), 2)
    lines = [ln for ln in lines if not ln.target.startswith("TRY-")]  # experimento não distorce o custo das demandas
    for ln in lines:
        key = {
            "story": ln.target,
            "dev": ln.dev,
            "project": ln.project,
            "month": ln.day.strftime("%Y-%m"),
            "tool": ln.tool,
        }.get(group_by, ln.target)
        row = rows.setdefault(key, CostRow(key))
        cost = usage_cost_brl(ln, p)
        row.usage_brl = round(row.usage_brl + cost, 2)
        row.allocated_brl = round(row.allocated_brl + allocations.get(ln.step_id, 0.0), 2)
        row.by_tool[ln.tool] = round(row.by_tool.get(ln.tool, 0.0) + cost, 2)
        for unit, qty in ln.units.items():
            row.units[f"{ln.tool}:{unit}"] = row.units.get(f"{ln.tool}:{unit}", 0.0) + qty
        row.estimated = row.estimated or ln.estimated
    months = sorted({ln.day.strftime("%Y-%m") for ln in lines})
    return {
        "group_by": group_by,
        "rows": [r.__dict__ for r in sorted(rows.values(), key=lambda r: -r.usage_brl)],
        "total_usage_brl": round(sum(r.usage_brl for r in rows.values()), 2),
        "total_allocated_brl": round(sum(r.allocated_brl for r in rows.values()), 2),
        "experiments_brl": experiments,
        "months": months,
        "provisional": [m for m in months if not _closed(ctx, m)],
        "estimated_prices": sorted(_estimated_tools(ctx) & {ln.tool for ln in lines}),
    }


def _estimated_tools(ctx: AppContext) -> set[str]:
    from nexo.modules.finops.app import pricing as finops_pricing

    return finops_pricing.estimated_tools(ctx)


def story_costs(ctx: AppContext, story_id: str) -> dict[str, float]:
    """Custo de uso (R$, preço de tabela) por alvo da história: ela mesma, QA/correções e tarefas do backlog."""
    root = ctx.project_root
    if root is None:
        return {}
    p = pricing(ctx)
    member: dict[str, bool] = {}
    out: dict[str, float] = defaultdict(float)
    for ln in usage_lines(ctx, project=project_key(root)):
        if ln.target not in member:
            member[ln.target] = (
                ln.target == story_id or ln.target.startswith(f"{story_id}-") or _belongs(ctx, ln.target, story_id)
            )
        if member[ln.target]:
            out[ln.target] += usage_cost_brl(ln, p)
    return {k: round(v, 4) for k, v in out.items()}


def _belongs(ctx: AppContext, target: str, story: str) -> bool:
    """Tarefas do backlog da história (T-x-y) contam no custo da história."""
    from nexo.kernel.workspace import tasks

    root = ctx.project_root
    if root is None or not target.startswith("T-"):
        return False
    try:
        return tasks.load_task(root, target).story == story
    except NexoError:
        return False


def close_month(ctx: AppContext, month: str) -> dict[str, Any]:
    if _closed(ctx, month):
        raise NexoError(f"{month} já está fechado")
    if month >= current_month():
        raise NexoError("só é possível fechar meses encerrados")
    lines = usage_lines(ctx, month)
    allocations = allocate(lines, pricing(ctx))
    by_step = {ln.step_id: ln for ln in lines}
    ctx.conn.executemany(
        "INSERT INTO allocations(month, step_id, dev, tool, target, project, fixed_brl, variable_brl)"
        " VALUES (?,?,?,?,?,?,?,?)",
        [
            (
                month,
                a.step_id,
                by_step[a.step_id].dev,
                by_step[a.step_id].tool,
                by_step[a.step_id].target,
                by_step[a.step_id].project,
                a.fixed_brl,
                a.variable_brl,
            )
            for a in allocations
        ],
    )
    ctx.conn.execute(
        "INSERT INTO months_closed(month, closed_by, closed_at) VALUES (?,?,?)", (month, ctx.actor, now_iso())
    )
    total = round(sum(a.total for a in allocations), 2)
    ctx.audit("finops.month_closed", {"month": month, "steps": len(allocations), "total_brl": total})
    return {"month": month, "steps": len(allocations), "total_brl": total}


# ---------------- orçamento


def set_budget(ctx: AppContext, dev: str, limit_brl: float, tool: str = "", month: str | None = None) -> None:
    if limit_brl < 0:
        raise NexoError("limite deve ser positivo")
    m = month or current_month()
    previous = ctx.conn.execute(
        "SELECT limit_brl FROM budgets WHERE dev = ? AND month = ? AND tool = ?", (dev, m, tool)
    ).fetchone()
    ctx.conn.execute(
        "INSERT INTO budgets(dev, month, tool, limit_brl, set_by, set_at) VALUES (?,?,?,?,?,?)"
        " ON CONFLICT(dev, month, tool) DO UPDATE SET limit_brl = excluded.limit_brl, set_by = excluded.set_by,"
        " set_at = excluded.set_at",
        (dev, m, tool, limit_brl, ctx.actor, now_iso()),
    )
    ctx.audit(
        "budget.set",
        {
            "dev": dev,
            "month": m,
            "tool": tool or "*",
            "limit_brl": limit_brl,
            "previous": float(previous["limit_brl"]) if previous else None,
        },
    )


def _limits(ctx: AppContext, dev: str, month: str) -> tuple[float | None, dict[str, float]]:
    rows = ctx.conn.execute("SELECT tool, limit_brl FROM budgets WHERE dev = ? AND month = ?", (dev, month)).fetchall()
    total = next((float(r["limit_brl"]) for r in rows if r["tool"] == ""), None)
    if total is None:
        default = (config.resolved(ctx).values.get("budget") or {}).get("monthly_brl")
        total = float(default) if default is not None else None
    if total is not None:  # créditos de aprovações externas somam ao limite vigente
        granted = ctx.conn.execute(
            "SELECT COALESCE(SUM(amount_brl), 0) FROM budget_grants WHERE dev = ? AND month = ?", (dev, month)
        ).fetchone()[0]
        total = round(total + float(granted), 2)
    return total, {str(r["tool"]): float(r["limit_brl"]) for r in rows if r["tool"]}


def sync_limits(ctx: AppContext, month: str | None = None, force: bool = False) -> dict[str, Any] | None:
    """Limite do dev vindo de fora do Nexo (`budget.source: central | external`), com cache curto:
    o gestor ou o processo de aprovação externo pode mudá-lo a qualquer momento."""
    import json as _json

    from nexo.kernel.platform.identity import TRUSTED_ORIGINS

    res = config.resolved(ctx)
    cfg = dict(res.values.get("budget") or {})
    source = str(cfg.get("source") or "local")
    if source == "local":
        return None
    untrusted = [
        k
        for k in ("budget.source", "budget.external.file", "budget.external.url")
        if res.origin.get(k, "default") not in TRUSTED_ORIGINS
    ]
    if untrusted:  # o dev não aponta o próprio limite para um arquivo seu
        ctx.audit(
            "policy.violation", {"rule": "SEC-52", "reason": f"{', '.join(untrusted)} só vale na org ou no squad"}
        )
        return None
    m = month or current_month()
    ext = dict(cfg.get("external") or {})
    stamp = ctx.home / "budget-sync.json"
    ttl = float(ext.get("ttl_seconds", 300))
    if not force and stamp.is_file():
        last = _json.loads(stamp.read_text(encoding="utf-8"))
        if last.get("month") == m and last.get("dev") == ctx.actor and time.time() - float(last["at"]) < ttl:
            return None
    try:
        if source == "central":
            from nexo.kernel.platform import telemetry

            data = telemetry.pull_budget(ctx, m)
            limits = {str(k): float(v) for k, v in ((data or {}).get("limits") or {}).items()}
        elif source == "external":
            from nexo.kernel import services
            from nexo.kernel.secrets import credentials

            approvals = services.load("delivery.approvals")

            if ext.get("file"):
                limits = approvals.external_limit_file(Path(str(ext["file"])).expanduser(), ctx.actor, m) or {}
            elif ext.get("url"):
                token = credentials.get_secret("budget")
                limits = approvals.external_limit_http(str(ext["url"]), token, ctx.actor, m) or {}
            else:
                raise NexoError("budget.source=external exige budget.external.file ou budget.external.url")
        else:
            raise NexoError(f"budget.source desconhecido: {source} (local | central | external)")
    except (NexoError, OSError) as exc:
        ctx.audit("budget.sync_failed", {"source": source, "error": str(exc)})
        return None  # segue com o último limite conhecido
    changed = {}
    for tool, limit in limits.items():
        key = "" if tool == "*" else tool
        row = ctx.conn.execute(
            "SELECT limit_brl FROM budgets WHERE dev = ? AND month = ? AND tool = ?", (ctx.actor, m, key)
        ).fetchone()
        if row is None or float(row["limit_brl"]) != limit:
            set_budget(ctx, ctx.actor, limit, key, m)
            changed[tool] = limit
    stamp.write_text(_json.dumps({"month": m, "dev": ctx.actor, "at": time.time()}), encoding="utf-8")
    return {"source": source, "limits": limits, "changed": changed}


def budget_status(ctx: AppContext, dev: str | None = None, month: str | None = None) -> BudgetStatus:
    who = dev or ctx.actor
    m = month or current_month()
    p = pricing(ctx)
    by_tool: dict[str, float] = defaultdict(float)
    acu = 0.0
    for ln in usage_lines(ctx, m, who):
        by_tool[ln.tool] += usage_cost_brl(ln, p)
        if ln.tool == "devin":
            acu += ln.units.get("acu", 0.0)
    total, tool_limits = _limits(ctx, who, m)
    units: dict[str, dict[str, float]] = {}
    included = p.included_units(who, "devin", date.fromisoformat(f"{m}-01"))
    if included is not None:
        units["devin"] = {"used": round(acu, 2), "included": included}
    return BudgetStatus(
        total, round(sum(by_tool.values()), 2), {k: round(v, 2) for k, v in by_tool.items()}, tool_limits, units
    )


def check_alerts(ctx: AppContext, dev: str | None = None) -> list[float]:
    """Alertas de 70%/90% — cada nível uma vez por mês."""
    from nexo.kernel import services

    notify = services.get("notify.main", "notify")

    who = dev or ctx.actor
    month = current_month()
    status = budget_status(ctx, who, month)
    if not status.limit_brl:
        return []
    levels = tuple(float(x) for x in (config.resolved(ctx).values.get("budget") or {}).get("alerts", [0.7, 0.9]))
    fired = []
    for level in (*levels, 1.0):
        if status.ratio < level:
            continue
        exists = ctx.conn.execute(
            "SELECT 1 FROM budget_alerts WHERE dev = ? AND month = ? AND level = ?", (who, month, level)
        ).fetchone()
        if exists:
            continue
        ctx.conn.execute(
            "INSERT INTO budget_alerts(dev, month, level, at) VALUES (?,?,?,?)", (who, month, level, now_iso())
        )
        title = "orçamento esgotado" if level >= 1.0 else f"orçamento em {int(level * 100)}%"
        notify(ctx, "budget", f"{who}: {title}", f"{brl(status.spent_brl)} de {brl(status.limit_brl)} em {month}")
        fired.append(level)
    return fired


def month_label(epoch: float) -> str:
    return time.strftime("%Y-%m", time.gmtime(epoch))


def days_in_month(month: str) -> int:
    y, m = (int(x) for x in month.split("-"))
    return calendar.monthrange(y, m)[1]
