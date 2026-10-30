"""Comandos `nexo central`: parear a máquina, ver a saúde do envio e subir um coletor de teste."""

from __future__ import annotations

import os
import sys
from typing import Annotated, Any

import typer

from nexo.kernel.errors import ExitCode, NexoError


def register(root: typer.Typer, tools: Any) -> None:
    internal = tools.internal
    ctx, emit, console = tools.ctx, tools.emit, tools.console

    @root.command("setup")
    def setup_cmd(
        check: Annotated[bool, typer.Option("--check", help="Só mostra o que falta, sem instalar")] = False,
        yes: Annotated[bool, typer.Option("--yes", help="Instala sem perguntar")] = False,
        agent: Annotated[list[str] | None, typer.Option("--agent", help="Ferramenta de IA a instalar (repita)")] = None,
        squad_name: Annotated[str, typer.Option("--squad", help="Seu squad")] = "",
    ) -> None:
        """Confere e instala o que é preciso para rodar, e pergunta o seu squad."""
        from nexo.modules.setup.app import install, squad

        items = install.plan(ctx(), list(agent or []))
        if not check:

            def approve(item: install.Item) -> bool:
                if yes:
                    return True
                if ctx().options.json or not sys.stdin.isatty():
                    return False
                return typer.confirm(f"Instalar {item.name} ({item.why})?", default=item.required)

            items = install.apply(ctx(), items, approve)
            if squad_name:
                squad.set_squad(ctx(), squad_name)
            elif not squad.current(ctx())["squad"] and sys.stdin.isatty() and not ctx().options.json:
                answer = typer.prompt("Qual é o seu squad? Enter para informar depois", default="", show_default=False)
                if answer:
                    squad.set_squad(ctx(), answer)
        missing = [i for i in items if i.required and not i.present]
        if ctx().options.json:
            emit({"ok": not missing, "items": [i.as_dict() for i in items], "squad": squad.current(ctx())["squad"]})
        else:
            for i in items:
                mark = "✔" if i.present else ("✖" if i.required else "–")
                note = f"  {i.done}" if i.done else ""
                console.print(f"  {mark} {i.name:<12} {i.why}{note}", markup=False, highlight=False)
            console.print(
                f"  squad: {squad.current(ctx())['squad'] or 'não informado (nexo squad set <nome>)'}", markup=False
            )
            console.print(
                "Tudo pronto para rodar." if not missing else f"Falta: {', '.join(i.name for i in missing)}",
                markup=False,
            )
        if missing:
            raise typer.Exit(ExitCode.GATE)

    squad_app = typer.Typer(help="De qual squad você participa", no_args_is_help=False)
    root.add_typer(squad_app, name="squad")

    @squad_app.callback(invoke_without_command=True)
    def squad_show(context: typer.Context) -> None:
        """Mostra o seu squad e de onde vem esse valor."""
        if context.invoked_subcommand is not None:
            return
        from nexo.modules.setup.app import squad

        data = squad.state(ctx())
        if ctx().options.json:
            emit(data)
        elif data["squad"]:
            console.print(f"{data['squad']} (fonte: {data['source']})", markup=False)
        else:
            console.print("Squad não informado. Use: nexo squad set <nome>", markup=False)

    @squad_app.command("set")
    def squad_set(
        name: str,
        project: Annotated[
            bool, typer.Option("--project", help="Padrão deste repositório, para quem não informou")
        ] = False,
    ) -> None:
        """Informa o seu squad. Vale em todos os repositórios, salvo `--project`."""
        from nexo.modules.setup.app import squad

        emit(squad.set_squad(ctx(), name, project))

    @squad_app.command("list")
    def squad_list() -> None:
        """Squads que o cérebro e o time conhecem."""
        from nexo.modules.setup.app import squad

        emit(squad.known(ctx()))

    central = typer.Typer(help="Coletor central e portal da empresa", no_args_is_help=True)
    root.add_typer(central, name="central")

    @central.command("pair")
    def central_pair() -> None:
        """Pareia esta máquina com o central da empresa, sem colar token."""
        raise typer.Exit(internal(["login", "central"]))

    @central.command("status")
    def central_status() -> None:
        """Saúde do envio: pendências e atraso. Com o central fora, o trabalho segue e o envio espera."""
        raise typer.Exit(internal(["admin", "telemetry", "status"]))

    def _serve(port: int) -> None:
        import uvicorn

        uvicorn.run("nexo.central.app:app", host="127.0.0.1", port=port, log_level="warning")

    @central.command("serve", hidden=True)
    def central_serve(port: Annotated[int, typer.Option("--port")] = 8080) -> None:
        """Uso interno: sobe o servidor com o ambiente já definido por quem chamou. Funciona também no binário."""
        _serve(port)

    @central.command("up")
    def central_up(
        port: Annotated[int, typer.Option("--port")] = 8080,
        db: Annotated[str, typer.Option("--db", help="Arquivo SQLite")] = "nexo-central.db",
    ) -> None:
        """Só para teste: sobe o coletor em 127.0.0.1 com SQLite. Produção é na AWS (deploy/central)."""
        try:
            import uvicorn  # noqa: F401
        except ImportError as exc:
            raise NexoError("instale o extra: pip install nexo[central]") from exc
        os.environ["NX_COLLECTOR_DB"] = db
        _serve(port)
