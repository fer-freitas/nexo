"""Quem mostra que a IA está trabalhando: a CLI registra; a chamada de IA usa quando não traz o próprio `ui`.

Módulo leve de propósito: a CLI registra na inicialização sem carregar a camada de execução (abertura < 1 s).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

UI = Callable[[str, dict[str, Any]], None]
FEEDBACK: list[UI] = []


def set_feedback(ui: UI | None) -> None:
    FEEDBACK[:] = [ui] if ui else []
