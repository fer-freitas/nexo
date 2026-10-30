"""Comandos de integração com a ferramenta de gestão."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from nexo.cli.app import app, console, ctx, emit
from nexo.kernel.errors import ExitCode, NexoError

tracker_app = typer.Typer(help="Ferramenta de gestão", no_args_is_help=True)


app.add_typer(tracker_app, name="tracker")


@tracker_app.command("map")
def tracker_map_cmd(
    check: Annotated[bool, typer.Option("--check")] = False,
    suggest: Annotated[bool, typer.Option("--suggest")] = False,
) -> None:
    from nexo.modules.demands.app import tracker

    if suggest:
        path = tracker.suggest_mapping(ctx())
        console.print(f"De-para sugerido em {path} — revise e confirme.")
        return
    result = tracker.check_mapping(ctx())
    if ctx().options.json:
        emit(result)
    else:
        console.print(
            f"Tracker {result['tracker']} · modo {result['mode']}"
            + (f" · processo {result['process']}" if result["process"] else "")
        )
        for p in result["problems"]:
            console.print(f"  [nexo.bad]✖[/] {p}")
        if not result["problems"]:
            console.print("  [nexo.good]✔ de-para completo[/]")
    if result["problems"] and check:
        raise typer.Exit(ExitCode.ERROR)


@tracker_app.command("sync")
def tracker_sync_cmd() -> None:
    from nexo.modules.demands.app import tracker

    root = ctx().project_root
    if root is None:
        raise NexoError("nenhum projeto Nexo encontrado; rode `nexo init`")
    sync = tracker.Sync(ctx(), root)
    if not sync.enabled:
        raise NexoError("tracker local: nada a sincronizar (config tracker.kind)")
    ok, failed = sync.retry_pending()
    changes = sync.reconcile()
    if ctx().options.json:
        emit({"retried": ok, "failed": failed, "pending": sync.pending(), "reconciled": changes})
        return
    console.print(f"Pendências reenviadas: {ok} · falharam: {failed} · restantes: {sync.pending()}")
    for c in changes:
        console.print(f"  ⇄ {c['item']}: {c['from']} → {c['to']} (externo: {c['external']})")
    if failed:
        raise typer.Exit(ExitCode.ERROR)


@tracker_app.command("export")
def tracker_export_cmd(out: Annotated[Path | None, typer.Option("--out")] = None) -> None:
    from nexo.modules.demands.app import tracker

    root = ctx().project_root
    dest = out or ((root or ctx().cwd) / ".nexo" / "tracker")
    n = tracker.export_file(ctx(), dest)
    console.print(f"{n} item(ns) exportado(s) para {dest / 'outbox.csv'}")


@tracker_app.command("import")
def tracker_import_cmd(file: Path) -> None:
    from nexo.modules.demands.app import tracker

    changes = tracker.import_file(ctx(), file)
    if ctx().options.json:
        emit(changes)
        return
    console.print(f"{len(changes)} mudança(s) aplicada(s)")
    for c in changes:
        console.print(f"  ⇄ {c['item']}: {c['from']} → {c['to']}")
