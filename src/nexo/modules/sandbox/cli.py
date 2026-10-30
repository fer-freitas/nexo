"""Comandos `nexo try` e `nexo test`: experimentar e testar sem criar demanda."""

from __future__ import annotations

from typing import Annotated, Any

import typer

from nexo.kernel.errors import NexoError
from nexo.modules.sandbox import app as sandbox


def register(root: typer.Typer, tools: Any) -> None:
    ctx, emit, console, internal = tools.ctx, tools.emit, tools.console, tools.internal

    @root.command("try")
    def try_cmd(
        words: Annotated[
            list[str] | None, typer.Argument(help="O pedido, ou: list, show <id>, discard <id>, promote <id>")
        ] = None,
        agent: Annotated[str, typer.Option("--agent", help="Ferramenta de IA")] = "",
        skill: Annotated[str, typer.Option("--skill", help="Skill a usar")] = "",
    ) -> None:
        """Experimenta um pedido sem criar demanda, sem PR e sem aprovação."""
        args = list(words or [])
        verb = args[0] if args else ""
        if verb == "list":
            rows = sandbox.listing(ctx())
            if ctx().options.json:
                emit(rows)
                return
            for r in rows:
                console.print(f"{r['id']}  {r.get('state', '')}  {r.get('request', '')[:70]}", markup=False)
            if not rows:
                console.print("nenhum experimento")
            return
        if verb in ("show", "discard", "promote"):
            if len(args) < 2:
                raise NexoError(f"use `nexo try {verb} TRY-n`")
            if verb == "show":
                data = sandbox.show(ctx(), args[1])
                if not ctx().options.json:
                    console.print(
                        f"{args[1]}: {data.get('request')}\n{data.get('diff') or '(sem alterações)'}", markup=False
                    )
                    return
            else:
                data = getattr(sandbox, verb)(ctx(), args[1])
            emit(data)
            return
        request = " ".join(args)
        if not request:
            raise NexoError('use `nexo try "<pedido>"`')
        data = sandbox.run_agent(ctx(), sandbox.create(ctx(), request, agent, skill))
        if ctx().options.json:
            emit(data)
            return
        console.print(f"{data['id']}: {data['state']} · {', '.join(data.get('routes') or [])}")
        console.print(f"  ver: nexo try show {data['id']} · levar adiante: nexo try promote {data['id']}")

    @root.command("test", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
    def test_cmd(context: typer.Context) -> None:
        """Pipeline de qualidade no código atual, sem demanda e sem IA."""
        raise typer.Exit(internal(["quality", "run", *context.args]))
