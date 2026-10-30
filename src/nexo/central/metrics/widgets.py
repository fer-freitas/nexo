"""Componentes das páginas: KPI, tabela, série, página, arredondamento, percentual, tendência e drill."""

from __future__ import annotations

from typing import Any

from nexo.central.metrics.scope import Scope

MONTH_NAMES = (
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)


def month_name(month: str) -> str:
    """`2026-10` → `outubro`."""
    return MONTH_NAMES[int(month[5:7]) - 1]


def _r(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(float(value), digits)


def _pct(num: float, den: float) -> float | None:
    return round(100 * num / den, 1) if den else None


def kpi(id_: str, label: str, value: Any, fmt: str = "num", drill: dict[str, str] | None = None) -> dict[str, Any]:
    return {"id": id_, "label": label, "value": value, "fmt": fmt, "drill": drill}


def headline(
    id_: str,
    ask: str,
    value: Any,
    fmt: str,
    context: str,
    status: str = "neutral",
    status_text: str = "",
    bar: float | None = None,
    drill: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Pergunta respondida no topo da página: valor, contexto e estado (good | warn | bad | neutral)."""
    return {
        "id": id_,
        "ask": ask,
        "value": value,
        "fmt": fmt,
        "context": context,
        "status": status,
        "status_text": status_text,
        "bar": bar,
        "drill": drill,
    }


def attention(title: str, detail: str, action: str = "", level: str = "info", link: str = "") -> dict[str, str]:
    """Item de "Precisa de atenção": o que está faltando ou parado e o comando (ou página) que resolve."""
    return {"title": title, "detail": detail, "action": action, "level": level, "link": link}


def table(
    id_: str, title: str, columns: list[str], rows: list[list[Any]], individual: str | None = None
) -> dict[str, Any]:
    return {"id": id_, "title": title, "columns": columns, "rows": rows, "individual": individual}


def series(id_: str, title: str, points: list[tuple[str, float | None]], fmt: str = "num") -> dict[str, Any]:
    return {"id": id_, "title": title, "points": [[k, v] for k, v in points], "fmt": fmt}


def page(name: str, title: str, scope: Scope, **parts: Any) -> dict[str, Any]:
    return {
        "page": name,
        "title": title,
        "filters": scope.filters.query(),
        "period": list(scope.period),
        "squads": None if scope.squads is None else sorted(scope.squads),
        "kpis": parts.get("kpis", []),
        "tables": parts.get("tables", []),
        "charts": parts.get("charts", []),
        "notes": parts.get("notes", []),
        "org": parts.get("org", []),
        "headline": parts.get("headline", []),
        "attention": parts.get("attention", []),
    }


def _drill(source: str, **extra: str) -> dict[str, str]:
    return {"source": source, **{k: v for k, v in extra.items() if v}}
