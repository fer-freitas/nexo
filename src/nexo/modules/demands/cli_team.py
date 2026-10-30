"""Comandos `nexo team`, `role` e `join`: quem é o time, onde ele mora e como entrar."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer

from nexo.kernel.errors import NexoError
from nexo.modules.demands.app import core, membership
from nexo.modules.demands.domain import model as m


def _members(values: list[str] | None) -> list[dict[str, Any]]:
    out = []
    for item in values or []:
        who, sep, roles = item.partition("=")
        if not sep:
            raise NexoError(f"use pessoa=pm,qa,tl,dev (recebi {item})")
        out.append({"id": who.strip(), "roles": [r.strip() for r in roles.split(",")]})
    return out


def register_team(root: typer.Typer, ctx: Callable[[], Any], show: Callable[..., None]) -> None:
    team_app = typer.Typer(help="Repositório do time", no_args_is_help=True)
    root.add_typer(team_app, name="team")

    @team_app.command("init")
    def team_init(
        name: str,
        path: Annotated[str, typer.Option("--path", help="Pasta do repositório do time")] = "",
        mode: Annotated[str, typer.Option("--mode", help="team ou solo")] = "team",
        member: Annotated[list[str] | None, typer.Option("--member", help="pessoa=pm,qa,tl,dev")] = None,
    ) -> None:
        """Cria a estrutura de um repositório de time."""
        members = _members(member)
        folder = Path(path) if path else ctx().cwd / f"nexo-{name}"
        membership.init_team_repo(folder, name, mode, members)
        show({"team": name, "path": str(folder)}, f"Estrutura criada em {folder}. Faça o primeiro commit e o push.")

    @team_app.command("link")
    def team_link(
        name: str,
        repo: Annotated[str, typer.Option("--repo", help="Endereço do repositório do time")],
    ) -> None:
        """Liga este repositório de código ao time: demandas e refinamento passam a morar lá."""
        data = membership.link(ctx(), name, repo)
        show(data, f"Projeto {data['project']} ligado ao time {name}.")

    @root.command("role")
    def role_cmd() -> None:
        """Mostra seus papéis no time."""
        me = core.who(ctx())
        t = core.team(ctx())
        data = {"who": me, "mode": t.mode, "roles": t.roles_of(me) if t.declared else list(m.ROLE_LABEL)}
        show(data, f"{me}: {', '.join(data['roles']) or 'sem papel'} (modo {t.mode})")

    @root.command("join")
    def join_cmd(
        team: str,
        repo: Annotated[str, typer.Option("--repo", help="Endereço do repositório do time")] = "",
        project: Annotated[str, typer.Option("--project", help="Projeto dentro do time")] = "",
    ) -> None:
        """Entra no time sem precisar saber Git."""
        data = membership.join(ctx(), team, repo, project)
        show(data, f"Você entrou no time {team}. Projetos: {', '.join(data['projects']) or 'nenhum'}")
