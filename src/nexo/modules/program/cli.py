"""Registra os comandos de programas."""

from __future__ import annotations

from typing import Any

import typer


def register(_root: typer.Typer, _tools: Any) -> None:
    from nexo.modules.program import commands  # noqa: F401
