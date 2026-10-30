"""Formato único de dinheiro para pessoas: `R$ 1.234,56` (console, mensagens, notificações e dashboard)."""

from __future__ import annotations


def brl(value: float | int | None, empty: str = "—") -> str:
    if value is None:
        return empty
    return "R$ " + f"{float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
