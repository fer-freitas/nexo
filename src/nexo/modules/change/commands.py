"""Comandos de entrega.

mudança, programas multi-repositório, dependências, revisão de PR, evidências, aprovações externas e PR.
"""

from __future__ import annotations

from typing import Annotated

import typer

from nexo.cli.app import app, console, ctx, emit
from nexo.kernel.errors import NexoError

change_app = typer.Typer(help="Gestão de mudança no ServiceNow ou no Jira Service Management", no_args_is_help=True)


app.add_typer(change_app, name="change")


@change_app.command("open")
def change_open_cmd(
    feature: Annotated[str, typer.Argument(help="Feature (F-n)")],
    kind: Annotated[
        str | None, typer.Option("--type", help="standard ou normal (padrão: decidido pelas evidências)")
    ] = None,
) -> None:
    """Abre a requisição de mudança da entrega a partir do pacote de evidências."""
    from nexo.modules.change.app import change

    if kind not in (None, "standard", "normal"):
        raise NexoError("--type deve ser standard ou normal")
    data = change.open_change(ctx(), feature, kind)
    if ctx().options.json:
        emit(data)
        return
    console.print(
        f"{feature}: mudança {data['number']} aberta no {data['channel']} · tipo {data['type']} · risco {data['risk']}"
    )
    for reason in data["reasons"]:
        console.print(f"  · {reason}", markup=False, highlight=False)


@change_app.command("status")
def change_status_cmd(feature: Annotated[str, typer.Argument(help="Feature (F-n)")]) -> None:
    """Situação da requisição de mudança (aprovação e estado na ferramenta de ITSM)."""
    from nexo.modules.change.app import change

    data = change.status(ctx(), feature)
    if ctx().options.json:
        emit(data)
        return
    console.print(
        f"{feature}: mudança {data.get('number')} · aprovação {data.get('approval')}"
        f" · estado {data.get('state') or '—'}"
    )
