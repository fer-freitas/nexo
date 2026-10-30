"""Comandos `nexo mcp search|add|test`: descobrir, ligar e provar. O catálogo e `check` continuam onde estavam."""

from __future__ import annotations

import json as _json
from typing import Annotated, Any

import typer

from nexo.modules.mcp.app import catalog


def register(root: typer.Typer, tools: Any) -> None:
    ctx, emit, console = tools.ctx, tools.emit, tools.console
    group = next(g.typer_instance for g in root.registered_groups if g.name == "mcp")

    @group.command("search")
    def mcp_search(query: str) -> None:
        """Busca no catálogo e no registro oficial, que só descobre e não aprova."""
        rows = catalog.search(ctx(), query)
        if ctx().options.json:
            emit(rows)
            return
        for r in rows:
            ok = "aprovado" if r["approved"] else "não aprovado"
            console.print(f"{r['id']}  [{r['source']}, {ok}]  {str(r['product'] or '')[:70]}", markup=False)

    @group.command("add")
    def mcp_add(server: str) -> None:
        """Liga no projeto e mostra o trecho a aprovar no cérebro, por PR. Sem aprovação, não entra nas execuções."""
        emit(catalog.add(ctx(), server))

    @group.command("test")
    def mcp_test(
        server: str,
        tool: Annotated[str, typer.Option("--tool", help="Ferramenta de leitura para chamar")] = "",
        args: Annotated[str, typer.Option("--args", help="Argumentos em JSON")] = "{}",
    ) -> None:
        """Conecta de verdade. Só chamar uma ferramenta prova o estado `used`."""
        emit(catalog.probe(ctx(), server, tool, _json.loads(args)))
