"""Contexto de avaliação dos detectores e utilitários comuns (ocorrência, datas, taxas)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from nexo.central.db import CollectorDB

Finding = dict[str, Any]


class EvalCtx:
    def __init__(self, d: CollectorDB, now: datetime) -> None:
        self.d = d
        self.now = now
        self.month = now.strftime("%Y-%m")
        iso = now.isocalendar()
        self.week = f"{iso[0]}-S{iso[1]:02d}"

    def since(self, days: int) -> str:
        return (self.now - timedelta(days=days)).isoformat()


def _finding(scope_kind: str, scope: str, period: str, evidence: dict[str, Any], detail: str, **extra: Any) -> Finding:
    return {
        "scope_kind": scope_kind,
        "scope": scope,
        "period": period,
        "evidence": evidence,
        "detail": detail,
        "squad": extra.get("squad"),
        "dev_pid": extra.get("dev_pid"),
        "purpose": extra.get("purpose"),
    }


def _ts(value: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _rate(ok: int, n: int) -> float:
    return ok / n if n else 0.0


THRESHOLD_METRICS: dict[str, tuple[str, str]] = {  # métrica → (fonte, agregação)
    "step_cost_brl": ("step", "sum_brl"),
    "step_success_pct": ("step", "success_pct"),
    "story_fpy_pct": ("story", "fpy_pct"),
    "story_brl_per_point": ("story", "brl_per_point"),
    "story_waiting_hours": ("story", "avg_wait"),
    "handoffs": ("handoff", "count"),
    "blocks": ("blocked", "count"),
}
