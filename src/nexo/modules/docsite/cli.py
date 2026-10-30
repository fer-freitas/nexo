"""Registra os comandos de site de documentação."""

from __future__ import annotations

from typing import Any

import typer


def register(_root: typer.Typer, _tools: Any) -> None:
    from nexo.modules.docsite import commands  # noqa: F401
