"""Autonomia conquistada por squad × área.

- L0 sugere: o agente trabalha, mas nada sai da máquina sem humano (portão humano, sem PR).
- L1 abre PR: portão sempre humano; PR aberto para revisão.
- L2 aprova sozinho: portão automático acima do limiar (comportamento padrão do Nexo).
- L3 faz merge: além do L2, o Nexo faz merge do PR quando o CI fica verde.

Subir exige métricas (padrão: FPY ≥ 85% nas últimas 20 histórias e nenhum escape em 30 dias) **e** aprovação
do gestor do squad; escape grave ou incidente causado por mudança do Nexo rebaixa um nível na hora.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

LEVELS = ("L0", "L1", "L2", "L3")
DEFAULT_LEVEL = "L2"


def index(level: str) -> int:
    if level not in LEVELS:
        raise ValueError(f"nível de autonomia inválido: {level} (use {', '.join(LEVELS)})")
    return LEVELS.index(level)


def auto_gate(level: str) -> bool:
    return index(level) >= 2


def opens_pr(level: str) -> bool:
    return index(level) >= 1


def merges(level: str) -> bool:
    return index(level) >= 3


@dataclass
class Criteria:
    min_stories: int = 20
    min_fpy: float = 0.85
    escape_free_days: int = 30


@dataclass
class Eligibility:
    level: str
    next_level: str | None
    eligible: bool
    reasons: list[str] = field(default_factory=list)
    stories: int = 0
    fpy: float | None = None
    escapes: int = 0

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def eligibility(level: str, done: list[dict[str, Any]], escapes: int, criteria: Criteria | None = None) -> Eligibility:
    """`done`: histórias concluídas (mais recentes primeiro) com `bugs`; `escapes`: bugs pós-conclusão no período."""
    c = criteria or Criteria()
    nxt = LEVELS[index(level) + 1] if index(level) < len(LEVELS) - 1 else None
    recent = done[: c.min_stories]
    fpy = sum(1 for s in recent if not s.get("bugs")) / len(recent) if recent else None
    reasons = []
    if nxt is None:
        reasons.append("já está no nível máximo")
    if len(recent) < c.min_stories:
        reasons.append(f"{len(recent)} de {c.min_stories} histórias concluídas")
    if fpy is not None and fpy < c.min_fpy:
        reasons.append(f"FPY {fpy:.0%} < {c.min_fpy:.0%}")
    if escapes:
        reasons.append(f"{escapes} escape(s) nos últimos {c.escape_free_days} dias")
    return Eligibility(level, nxt, not reasons, reasons, len(recent), fpy, escapes)


def demoted(level: str) -> str:
    return LEVELS[max(0, index(level) - 1)]
