"""Comandos de entrega.

mudança, programas multi-repositório, dependências, revisão de PR, evidências, aprovações externas e PR.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from nexo.cli import look
from nexo.cli.app import app, console, ctx, emit

program_app = typer.Typer(help="Feature em vários repositórios", no_args_is_help=True)


app.add_typer(program_app, name="program")


program_repo_app = typer.Typer(help="Repositórios da área de trabalho", no_args_is_help=True)


program_app.add_typer(program_repo_app, name="repo")


WorkspaceReq = Annotated[str, typer.Option("--workspace", help="Área de trabalho do programa")]


@program_repo_app.command("add")
def program_repo_add_cmd(
    name: str,
    path: Path,
    workspace: WorkspaceReq,
    after: Annotated[list[str] | None, typer.Option("--after", help="Repositório que precisa vir antes")] = None,
) -> None:
    """Declara um repositório da feature (projeto Nexo) e de quem ele depende."""
    from nexo.modules.program.app import program

    out = program.add_repo(ctx(), workspace, name, path, after or [])
    if ctx().options.json:
        emit(out)
        return
    for n, info in out.items():
        console.print(f"  {n}: {info['path']}" + (f" (depois de {', '.join(info['after'])})" if info["after"] else ""))


@program_app.command("split")
def program_split_cmd(feature: str, workspace: WorkspaceReq) -> None:
    """Cria em cada repositório uma feature normal com as histórias dele."""
    from nexo.modules.program.app import program

    out = program.split(ctx(), workspace, feature)
    if ctx().options.json:
        emit(out)
        return
    console.print(f"{feature}: ordem {' → '.join(out['order'])}")
    for name, info in out["repos"].items():
        console.print(f"  {name}: {info['feature']} ({len(info['stories'])} história(s))")
    console.print(f"  próximo passo: [nexo.accent]{look.prefix()}program run {feature} --workspace {workspace}[/]")


@program_app.command("run")
def program_run_cmd(
    feature: str,
    workspace: WorkspaceReq,
    parallel: Annotated[int, typer.Option("--parallel")] = 1,
) -> None:
    """Executa as features de cada repositório na ordem e liga os PRs entre si."""
    from nexo.modules.program.app import program

    out = program.run(ctx(), workspace, feature, parallel)
    if ctx().options.json:
        emit(out)
        return
    for name, res in out["repos"].items():
        states = ", ".join(f"{r['story']} {r.get('state')}" for r in res.get("stories") or [])
        console.print(f"  {name}: {states}")
    if out["stopped"]:
        console.print(f"  [nexo.warn]{out['stopped']}[/]")
    for url in out["prs"]:
        console.print(f"  PR: {url}")


@program_app.command("status")
def program_status_cmd(feature: str, workspace: WorkspaceReq) -> None:
    """Andamento da feature em cada repositório."""
    from nexo.modules.program.app import program

    out = program.status(ctx(), workspace, feature)
    if ctx().options.json:
        emit(out)
        return
    for r in out["repos"]:
        states = ", ".join(f"{k} {v}" for k, v in r["stories"].items())
        console.print(f"  {r['repo']} ({r['feature']}): {states}")
    console.print("  [nexo.good]concluída em todos os repositórios[/]" if out["done"] else "  em andamento")
