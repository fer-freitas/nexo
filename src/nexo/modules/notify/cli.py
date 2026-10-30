"""Comando `nexo notify test`: confere o canal de avisos configurado, com uma mensagem de verdade."""

from __future__ import annotations

from typing import Any

import typer

from nexo.modules.notify import app as notify_app


def register(root: typer.Typer, tools: Any) -> None:
    ctx, emit = tools.ctx, tools.emit
    group = typer.Typer(help="Avisos por Slack, Teams ou e-mail", no_args_is_help=True)
    root.add_typer(group, name="notify")

    @group.command("test")
    def notify_test() -> None:
        """Envia uma mensagem de teste para os canais configurados."""
        emit(notify_app.test(ctx()))
