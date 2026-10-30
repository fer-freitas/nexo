"""Motor de insights.

detectores com algoritmo embutido e parâmetros em YAML (baseline e cérebro), ocorrências com escopo, evidência,
severidade, dono e estado. Nenhum insight muda comportamento sozinho nem compara pessoas.
"""

from __future__ import annotations

from nexo.central.insights.context import EvalCtx, Finding
from nexo.central.insights.engine import ALGORITHMS, load_detectors, run
from nexo.central.insights.store import list_for, set_state

__all__ = ["ALGORITHMS", "EvalCtx", "Finding", "list_for", "load_detectors", "run", "set_state"]
