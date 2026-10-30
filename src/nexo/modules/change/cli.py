"""Registra os comandos de gestão de mudança."""

from __future__ import annotations

from typing import Any

import typer


def register(_root: typer.Typer, _tools: Any) -> None:
    from nexo.modules.change import commands  # noqa: F401
