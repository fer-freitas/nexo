"""Comandos de planejamento acrescentados depois (o `planning.py` está no limite de tamanho)."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.markup import escape

from nexo.cli.app import console, ctx, emit
from nexo.cli.commands.planning import plan_app


@plan_app.command("classify")
def plan_classify(
    feature: str,
    data_class: Annotated[str, typer.Argument(help="public, internal, confidential ou restricted")],
    reason: Annotated[str, typer.Option("--reason", help="Por que esta classe")] = "",
) -> None:
    """Classificação de dados da feature (decide o que pode ir para as ferramentas de IA); decisão auditada."""
    from nexo.modules.planning.app import classify

    result = classify.classify_feature(ctx(), feature, data_class, reason)
    if ctx().options.json:
        emit(result)
        return
    console.print(
        f"[nexo.good]✔[/] {escape(feature)}: {escape(result['from'] or 'sem classe')} → "
        f"[nexo.title]{escape(result['to'])}[/]  [nexo.muted]{escape(result['effects'])}[/]"
    )
