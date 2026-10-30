"""Comandos `nexo central`: parear a máquina, ver a saúde do envio e subir um coletor de teste."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Annotated, Any

import typer

from nexo.kernel.errors import NexoError


def register(root: typer.Typer, tools: Any) -> None:
    internal = tools.internal
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
        env = {**os.environ, "NX_COLLECTOR_DB": db}
        raise typer.Exit(
            subprocess.run(
                [sys.executable, "-m", "uvicorn", "nexo.central.app:app", "--host", "127.0.0.1", "--port", str(port)],
                env=env,
                check=False,
            ).returncode
        )
