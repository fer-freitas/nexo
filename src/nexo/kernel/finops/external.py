"""Consumo de IA fora do Nexo: o que é sessão do Nexo, preço de referência do Claude e agregação por dia.

Sessão do Nexo = pasta de trabalho dentro de `.nexo/` (worktrees e specs dos projetos) ou dentro da pasta do Nexo
(workspace sem repositório); essas já entram pelos passos. O resto é uso direto da ferramenta pelo dev.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

UNITS = ("tokens_in", "tokens_out", "cache_read", "cache_write", "premium_request", "ai_credits", "acu")

# US$ por milhão de tokens (entrada, saída) — referência para o custo equivalente de API do Claude fora do Nexo,
# que não vem calculado nos arquivos locais. A org ajusta em `pricing.models` (prefixo do modelo → preços).
DEFAULT_CLAUDE_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus": (5.0, 25.0),
    "claude-sonnet": (3.0, 15.0),
    "claude-haiku": (1.0, 5.0),
    "claude-fable": (3.0, 15.0),
}
CACHE_READ_FACTOR = 0.1  # leitura de cache custa 10% da entrada
CACHE_WRITE_FACTOR = 1.25  # escrita de cache (5 min) custa 125% da entrada


def is_nexo_session(cwd: str, nexo_home: str = "") -> bool:
    norm = cwd.replace("\\", "/").rstrip("/") + "/"
    if "/.nexo/" in norm:
        return True
    home = nexo_home.replace("\\", "/").rstrip("/")
    return bool(home) and norm.startswith(home + "/")


def claude_prices(overrides: dict[str, Any] | None = None) -> list[tuple[str, float, float]]:
    table = dict(DEFAULT_CLAUDE_PRICES)
    for prefix, value in (overrides or {}).items():
        if isinstance(value, dict):
            table[str(prefix)] = (float(value.get("input", 0)), float(value.get("output", 0)))
    return sorted(((p, i, o) for p, (i, o) in table.items()), key=lambda x: -len(x[0]))  # prefixo mais longo vence


def claude_api_usd(model: str, units: dict[str, float], prices: list[tuple[str, float, float]]) -> float:
    price = next(((i, o) for p, i, o in prices if model.startswith(p)), None)
    if price is None:
        return 0.0
    i, o = price
    usd = (
        units.get("tokens_in", 0.0) * i
        + units.get("tokens_out", 0.0) * o
        + units.get("cache_read", 0.0) * i * CACHE_READ_FACTOR
        + units.get("cache_write", 0.0) * i * CACHE_WRITE_FACTOR
    )
    return round(usd / 1_000_000, 6)


@dataclass
class DayTotal:
    """Total do dia por ferramenta × modelo, separado em dentro e fora do Nexo."""

    tool: str
    day: str
    model: str
    nexo: bool
    sessions: int = 0
    responses: int = 0
    units: dict[str, float] = field(default_factory=dict)
    api_equiv_usd: float = 0.0


def aggregate(rows: list[Any], nexo_home: str, prices: list[tuple[str, float, float]]) -> list[DayTotal]:
    """`rows`: itens com tool, session, day, model, cwd, units e responses (ver adapters.external_usage)."""
    out: dict[tuple[str, str, str, bool], DayTotal] = {}
    for r in rows:
        nexo = is_nexo_session(r.cwd, nexo_home)
        total = out.setdefault((r.tool, r.day, r.model, nexo), DayTotal(r.tool, r.day, r.model, nexo))
        total.sessions += 1
        total.responses += r.responses
        for unit, value in r.units.items():
            if unit in UNITS:
                total.units[unit] = round(total.units.get(unit, 0.0) + value, 6)
    for total in out.values():
        if total.tool == "claude":
            total.api_equiv_usd = claude_api_usd(total.model, total.units, prices)
    return sorted(out.values(), key=lambda t: (t.day, t.tool, t.model, t.nexo))
