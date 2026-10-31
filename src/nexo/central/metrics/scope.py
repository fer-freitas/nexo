"""Escopo da consulta do painel: filtros, visibilidade por squad e período, e quem é cada pessoa."""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from nexo.central.common import pid_of
from nexo.central.db import CollectorDB
from nexo.central.visibility import Principal, check_filter_squads

FILTER_KEYS = ("month_from", "month_to", "directorate", "squad", "sigla", "project", "stack", "tool", "model")


DONE = "Concluido"


WAITING = "AguardandoHumano"


def _month_add(month: str, delta: int) -> str:
    year, mon = int(month[:4]), int(month[5:7])
    index = year * 12 + (mon - 1) + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _valid_month(value: str) -> bool:
    return len(value) == 7 and value[4] == "-" and value[:4].isdigit() and value[5:].isdigit()


@dataclass
class Filters:
    month_from: str
    month_to: str
    directorate: str = ""
    squad: str = ""
    project: str = ""
    stack: str = ""
    tool: str = ""
    model: str = ""
    sigla: str = ""

    @classmethod
    def parse(cls, params: dict[str, str], now: datetime | None = None) -> Filters:
        current = (now or datetime.now(UTC)).strftime("%Y-%m")
        to = params.get("month_to") or current
        start = params.get("month_from") or _month_add(to, -5)
        if not (_valid_month(to) and _valid_month(start)) or start > to:
            raise HTTPException(400, "período inválido (use AAAA-MM, início ≤ fim)")
        values = {k: str(params.get(k) or "")[:80] for k in FILTER_KEYS[2:]}
        values["squad"] = str(params.get("squad") or "")[:2000]  # vários squads, separados por vírgula
        return cls(month_from=start, month_to=to, **values)

    @property
    def selected_squads(self) -> list[str]:
        return [s.strip() for s in self.squad.split(",") if s.strip()]

    def query(self) -> dict[str, str]:
        return {k: getattr(self, k) for k in FILTER_KEYS if getattr(self, k)}


@dataclass
class Scope:
    """Filtros já cruzados com a visibilidade: `squads` None = todos."""

    filters: Filters
    squads: set[str] | None
    principal: Principal
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    projects: set[str] | None = None  # repositórios da sigla escolhida

    def has_squad(self, squad: str | None) -> bool:
        return self.squads is None or (squad or "") in self.squads

    def has_project(self, project: str | None) -> bool:
        if self.filters.project and project != self.filters.project:
            return False
        return self.projects is None or (project or "") in self.projects

    @property
    def by_project(self) -> bool:
        return bool(self.filters.project or self.filters.sigla)

    def where(
        self,
        alias: str = "",
        months: tuple[str, str] | None = None,
        month_col: str = "month",
        steps: bool = False,
        stack: bool = True,
    ) -> tuple[str, list[Any]]:
        a = f"{alias}." if alias else ""
        clauses: list[str] = []
        params: list[Any] = []
        if months is not None:
            clauses.append(f"{a}{month_col} BETWEEN ? AND ?")
            params += list(months)
        if self.squads is not None:
            if not self.squads:
                clauses.append("1 = 0")
            else:
                clauses.append(f"COALESCE({a}squad, '') IN ({', '.join('?' * len(self.squads))})")
                params += sorted(self.squads)
        if self.filters.project:
            clauses.append(f"{a}project = ?")
            params.append(self.filters.project)
        if self.projects is not None:
            if not self.projects:
                clauses.append("1 = 0")
            else:
                clauses.append(f"{a}project IN ({', '.join('?' * len(self.projects))})")
                params += sorted(self.projects)
        if stack and self.filters.stack:
            clauses.append(f"{a}stack = ?")
            params.append(self.filters.stack)
        if steps and self.filters.tool:
            clauses.append(f"{a}tool = ?")
            params.append(self.filters.tool)
        if steps and self.filters.model:
            clauses.append(f"{a}model = ?")
            params.append(self.filters.model)
        return (" AND ".join(clauses) or "1 = 1"), params

    @property
    def period(self) -> tuple[str, str]:
        return self.filters.month_from, self.filters.month_to

    @property
    def current(self) -> str:
        return self.filters.month_to


def make_scope(d: CollectorDB, principal: Principal, filters: Filters, now: datetime | None = None) -> Scope:
    allowed = principal.squads
    squads: set[str] | None = None if allowed is None else set(allowed)
    if filters.directorate:
        rows = d.query("SELECT squad FROM dim_squad WHERE directorate = ?", (filters.directorate,))
        in_dir = {str(r["squad"]) for r in rows}
        squads = in_dir if squads is None else squads & in_dir
    selected = filters.selected_squads
    if selected:
        check_filter_squads(principal, selected)
        squads = set(selected) if squads is None else squads & set(selected)
    projects: set[str] | None = None
    if filters.sigla:
        rows = d.query("SELECT project FROM dim_project WHERE sigla = ?", (filters.sigla,))
        projects = {str(r["project"]) for r in rows}
    return Scope(filters, squads, principal, now or datetime.now(UTC), projects)


def _day_fraction(scope: Scope) -> tuple[int, int, bool]:
    year, mon = int(scope.current[:4]), int(scope.current[5:7])
    days = calendar.monthrange(year, mon)[1]
    is_current = scope.now.strftime("%Y-%m") == scope.current
    return (scope.now.day if is_current else days), days, is_current


def devs_in(d: CollectorDB, squads: set[str] | None) -> set[str] | None:
    """Pessoas dos squads (diretório da org + quem enviou eventos com o squad) — None = todas."""
    if squads is None:
        return None
    if not squads:
        return set()
    marks = ", ".join("?" * len(squads))
    rows = d.query(
        f"SELECT dev FROM org_members WHERE relation = 'member' AND squad IN ({marks})", tuple(sorted(squads))
    )
    devs = {str(r["dev"]) for r in rows}
    rows = d.query(
        f"SELECT DISTINCT i.dev FROM raw_events e JOIN identities i ON i.dev_pid = e.dev_pid"
        f" WHERE COALESCE(e.squad, '') IN ({marks})",
        tuple(sorted(squads)),
    )
    return devs | {str(r["dev"]) for r in rows}


def squad_of_pid(d: CollectorDB) -> dict[str, str]:
    """Squad de cada pessoa (diretório; senão o squad mais recente dos eventos)."""
    out: dict[str, str] = {}
    for r in d.query("SELECT dev_pid, squad FROM raw_events WHERE squad IS NOT NULL AND squad <> '' ORDER BY at"):
        out[str(r["dev_pid"])] = str(r["squad"])
    for r in d.query("SELECT dev, squad FROM org_members WHERE relation = 'member'"):
        out[pid_of(str(r["dev"]))] = str(r["squad"])
    return out


DRILL: dict[str, tuple[str, tuple[str, ...], dict[str, str]]] = {
    "story": (
        "fact_story",
        ("project", "story", "feature", "state", "points", "cost_brl", "bugs", "attempts", "done_at"),
        {"state": "state", "bugs": "bugs", "human_gate": "human_gate", "escalated": "escalated", "feature": "feature"},
    ),
    "step": (
        "fact_step",
        ("project", "story", "run", "step", "role", "tool", "model", "outcome", "usage_brl", "started_at"),
        {"month": "month", "tool": "tool", "model": "model", "story": "story", "run": "run", "outcome": "outcome"},
    ),
    "gate": (
        "fact_gate",
        ("project", "story", "feature", "gate", "decision", "score", "threshold", "at"),
        {"gate": "gate", "decision": "decision", "story": "story"},
    ),
    "event": (
        "fact_event",
        ("project", "type", "story", "task", "kind", "tool", "at"),
        {"type": "type", "kind": "kind", "story": "story", "tool": "tool"},
    ),
    "quality": (
        "fact_quality",
        ("project", "story", "at", "failed", "coverage", "findings_high", "findings_critical"),
        {"story": "story"},
    ),
}
