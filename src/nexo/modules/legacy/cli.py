"""Registra os comandos de modernização e incidentes."""

from __future__ import annotations

from typing import Any

import typer


def register(_root: typer.Typer, _tools: Any) -> None:
    from nexo.modules.legacy import commands  # noqa: F401
