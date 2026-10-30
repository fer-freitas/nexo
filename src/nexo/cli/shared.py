"""Auxiliares de apresentação e de opções usados por mais de um grupo de comandos."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
from rich.table import Table

from nexo.cli.app import console, ctx, emit
from nexo.kernel.errors import NexoError


def _ui(kind: str, data: dict[str, Any]) -> None:
    o = ctx().options
    if o.json or o.quiet:
        return
    if kind == "llm":  # a IA está trabalhando — indicador com o tempo correndo
        from nexo.cli import look

        look.ai_start(console, str(data["role"]), str(data["route"]))
        return
    if kind == "llm_end":
        from nexo.cli import look

        look.ai_stop(console, data.get("status") == "success")
        return
    if kind == "state":
        suffix = f"  [nexo.muted]({data['reason']})[/]" if data.get("reason") else ""
        console.print(f"[nexo.accent]●[/] {data['from']} → [nexo.title]{data['to']}[/]{suffix}")
    elif kind == "route":
        detail = f" [nexo.muted]{data['route']} · {data['reason']}[/]" if o.verbose else ""
        console.print(f"[nexo.accent]▶[/] passo {data['step']} em execução{detail}")
    elif kind == "file":
        console.print(f"  [nexo.good]✎[/] {data['path']}")
    elif kind == "handoff":
        origin = f" de {data['from']}" if o.verbose else ""
        console.print(f"[nexo.warn]⇄ handoff{origin}:[/] {data['reason']}")
    elif kind == "retry":
        console.print(f"[nexo.warn]↻ nova tentativa:[/] {data['reason']}")
    elif kind == "step_end" and o.verbose:
        console.print(
            f"  [nexo.muted]fim do passo: {data['status']} · checkpoint {str(data.get('checkpoint'))[:10]}[/]"
        )
    elif kind == "quota" and o.verbose:
        console.print(f"  [nexo.muted]cota: {data}[/]")
    elif kind == "message" and o.verbose:
        text = str(data.get("text", "")).strip().splitlines()
        if text:
            console.print(f"  [nexo.muted]{text[0][:160]}[/]")
    elif kind == "info":
        console.print(f"[nexo.muted]{data['message']}[/]")
    elif kind == "warning":
        console.print(f"[nexo.warn]⚠ {data['message']}[/]", highlight=False)


def _print_add(report: Any) -> None:
    if ctx().options.json:
        emit(report.__dict__)
        return
    where = "proposta (squad/org mudam via PR no nexo-brain)" if report.proposal_only else report.path
    console.print(f"[nexo.good]✔[/] {report.key} · escopo {report.scope} · {where}")
    for kind, label in (("installed", "nativo"), ("emulated", "emulado pelo Nexo"), ("unavailable", "indisponível")):
        for tool, names in getattr(report, kind).items():
            if names:
                console.print(f"  {tool}: {label}")
    for sim in report.similar:
        console.print(f"  [nexo.warn]≈ similar a {sim['key']} ({sim['scope']}) — {sim['similarity']:.0%}[/]")
    console.print(f"  issue: {report.issue or '[nexo.muted]na fila / repositório do cérebro não configurado[/]'}")


def _ext_group(ext_type: str, group: typer.Typer) -> None:
    @group.command("add")
    def add_cmd(
        source: Path,
        scope: Annotated[str, typer.Option("--scope")] = "project",
        name: Annotated[str | None, typer.Option("--name")] = None,
        event: Annotated[str | None, typer.Option("--event", help="(hook) evento")] = None,
        matcher: Annotated[str, typer.Option("--matcher", help="(hook) filtro")] = "*",
        description: Annotated[str, typer.Option("--description")] = "",
    ) -> None:
        from nexo.modules.packs.app import extensions

        extra: dict[str, Any] = {"description": description} if description else {}
        if ext_type == "hook":
            if not event and not (source / "extension.yaml").is_file():
                raise NexoError("hook avulso exige --event")
            if event:
                extra["hook"] = {"event": event, "matcher": matcher}
        _print_add(extensions.add(ctx(), ext_type, source, scope, name, extra))

    @group.command("remove")
    def remove_cmd(name: str, scope: Annotated[str | None, typer.Option("--scope")] = None) -> None:
        from nexo.modules.packs.app import extensions

        removed = extensions.remove(ctx(), ext_type, name, scope)
        console.print(f"{ext_type} {name} removida ({len(removed)} local(is))")

    @group.command("list")
    def list_cmd() -> None:
        from nexo.modules.packs.app import extensions

        items = [e for e in extensions.effective(ctx()) if e.meta.type == ext_type]
        rows = [
            (e.meta.name, e.meta.scope, e.meta.version, e.meta.locked, e.meta.targets, e.meta.description)
            for e in items
        ]
        if ext_type == "skill":  # baseline embutida que o cérebro ainda não sobrepôs
            from nexo.modules.knowledge.app import content

            names = {r[0] for r in rows}
            rows += [
                (n, "embutido", "baseline", False, ["claude"], "baseline de fábrica (migre para o cérebro)")
                for n in content.embedded_skills()
                if n not in names
            ]
        if ctx().options.json:
            emit(
                [
                    {
                        "name": name,
                        "scope": scope,
                        "version": version,
                        "locked": locked,
                        "targets": targets,
                        "description": description,
                    }
                    for name, scope, version, locked, targets, description in rows
                ]
            )
            return
        table = Table(title=f"{ext_type}s efetivas", title_justify="left")
        for col in ("nome", "escopo", "versão", "alvos", "descrição"):
            table.add_column(col)
        for name, scope, version, locked, targets, description in rows:
            table.add_row(
                name + (" 🔒" if locked else ""),
                scope,
                version,
                ", ".join(targets),
                description[:80],
            )
        console.print(table)

    @group.command("show")
    def show_cmd(name: str) -> None:
        from nexo.modules.packs.app import extensions

        data = extensions.show(ctx(), ext_type, name)
        if ctx().options.json:
            emit(data)
            return
        console.print(f"[nexo.title]{ext_type} {name}[/] · {data['meta']['scope']} · {data['path']}")
        console.print(data["content"], markup=False)

    @group.command("sync")
    def sync_cmd() -> None:
        from nexo.modules.packs.app import extensions

        report = extensions.sync(ctx())
        if ctx().options.json:
            emit(report.__dict__)
            return
        for kind, label in (("installed", "nativo"), ("emulated", "emulado"), ("unavailable", "indisponível")):
            for tool, names in getattr(report, kind).items():
                console.print(f"  {tool} · {label}: {', '.join(sorted(set(names)))}")
