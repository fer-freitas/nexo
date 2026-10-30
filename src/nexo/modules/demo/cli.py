"""Comandos `nexo demo cli` e `nexo demo site`: experimentar sem preparar nada."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Any

import typer

from nexo.modules.demo.app import cli_demo, site_demo


def register(root: typer.Typer, tools: Any) -> None:
    ctx, emit, console = tools.ctx, tools.emit, tools.console
    demo = typer.Typer(help="Experimenta a CLI e o site com dados fictícios", no_args_is_help=True)
    root.add_typer(demo, name="demo")

    @demo.command("cli")
    def demo_cli(
        folder: Annotated[Path | None, typer.Option("--dir", help="Pasta vazia; padrão: uma pasta temporária")] = None,
        no_start: Annotated[bool, typer.Option("--no-start", help="Só prepara, sem abrir o console")] = False,
    ) -> None:
        """Prepara um repositório descartável com agente simulado e abre o console. Não gasta tokens."""
        data = cli_demo.prepare(folder)
        if ctx().options.json or no_start or not sys.stdin.isatty():
            emit(data)
            return
        console.print("Demonstração pronta, com dados fictícios e agente simulado.", style="nexo.title", markup=False)
        console.print(f"Pasta: {data['project']}", markup=False, highlight=False)
        console.print("Roteiro sugerido:", markup=False)
        for step in data["walkthrough"]:
            console.print(f"  {step['command']:<46} {step['what']}", markup=False, highlight=False)
        if not data["pytest"]:
            console.print(
                "`dev run` precisa do pytest: instale com `pip install pytest`.", style="nexo.warn", markup=False
            )
        raise typer.Exit(cli_demo.console(Path(data["folder"])))

    @demo.command("site")
    def demo_site(
        port: Annotated[int, typer.Option("--port", help="Porta; padrão: uma livre")] = 0,
    ) -> None:
        """Sobe o portal em 127.0.0.1 com dados fictícios e mostra um login por perfil. Ctrl+C encerra."""
        data = site_demo.start(port)
        server = data.pop("process")
        try:
            if ctx().options.json:
                emit(data)
            else:
                console.print(f"Portal: {data['url']}/login", style="nexo.title", markup=False)
                console.print("Dados fictícios. Tokens de login, válidos só nesta execução:", markup=False)
                for role, token in data["logins"].items():
                    console.print(f"  {role:<10} {token}", markup=False, highlight=False)
                console.print("Ctrl+C encerra.", markup=False)
            server.wait()
        except KeyboardInterrupt:
            pass
        finally:
            server.terminate()
