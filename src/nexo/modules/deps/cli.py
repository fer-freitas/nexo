"""Registra os comandos de dependências em escala."""

from __future__ import annotations

from typing import Any

import typer


def register(_root: typer.Typer, _tools: Any) -> None:
    from nexo.modules.deps import commands  # noqa: F401
