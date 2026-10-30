"""Comandos `nexo pack`: listar, conferir e preparar a adoção de conteúdo de terceiros."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from nexo.kernel.errors import ExitCode
from nexo.modules.packs.app import packs


def register(root: typer.Typer, tools: Any) -> None:
    ctx, emit, console = tools.ctx, tools.emit, tools.console
    pack = typer.Typer(help="Conteúdo de terceiros com commit fixado, hash e licença", no_args_is_help=True)
    root.add_typer(pack, name="pack")

    @pack.command("list")
    def pack_list() -> None:
        """Packs adotados."""
        rows = packs.listing()
        if ctx().options.json:
            emit(rows)
            return
        for r in rows:
            console.print(f"{r['id']:<22} {r['commit']}  {r['license']:<6} {r['adopted']} item(ns)", markup=False)

    @pack.command("show")
    def pack_show(pack_id: str) -> None:
        """Origem, commit, licença, o que foi adotado e o que foi rejeitado, com o motivo."""
        emit(packs.show(pack_id))

    @pack.command("lock")
    def pack_lock() -> None:
        """Confere o conteúdo contra o lock. Um byte diferente é falha."""
        problems = packs.verify()
        emit({"ok": not problems, "problems": problems})
        if problems:
            raise typer.Exit(ExitCode.INTEGRITY_FAILURE)

    @pack.command("diff")
    def pack_diff(pack_id: str) -> None:
        """Se o que está em uso bate com o lock."""
        emit(packs.diff(pack_id))

    @pack.command("expose")
    def pack_expose(
        tool: Annotated[str, typer.Option("--for", help="claude ou copilot")] = "claude",
    ) -> None:
        """Cria comandos como /nexo-pm-refine dentro da ferramenta de IA, neste repositório."""
        from nexo.modules.packs.app import expose

        emit(expose.write(ctx(), tool))

    @pack.command("lint")
    def pack_lint() -> None:
        """Confere o formato Agent Skills de todas as skills."""
        problems = packs.lint_all()
        emit({"ok": not problems, "problems": problems})
        if problems:
            raise typer.Exit(ExitCode.ERROR)

    @pack.command("update")
    def pack_update(
        pack_id: str,
        commit: Annotated[str, typer.Option("--commit", help="Commit completo")],
        dest: Annotated[Path, typer.Option("--dest", help="Pasta `packs` do clone do cérebro")],
        source: Annotated[str, typer.Option("--source", help="Endereço do repositório")] = "",
        adopt: Annotated[list[str] | None, typer.Option("--adopt", help="Caminho a adotar")] = None,
        license_: Annotated[str, typer.Option("--license", help="Licença do pacote")] = "",
        allow_scripts: Annotated[bool, typer.Option("--allow-scripts", help="Aprova scripts nominalmente")] = False,
        accept_suspicious: Annotated[bool, typer.Option("--accept-suspicious", help="Revisado por uma pessoa")] = False,
    ) -> None:
        """Baixa num commit fixado, sem executar nada, e prepara a mudança para virar PR no cérebro."""
        data = packs.update(pack_id, commit, dest, source, adopt, license_, allow_scripts, accept_suspicious)
        ctx().audit(
            "pack.updated",
            {
                "pack": pack_id,
                "commit": commit,
                "scripts": len(data["review"]["scripts"]),
                "suspicious": len(data["review"]["suspicious"]),
                "accepted": accept_suspicious,
                "allow_scripts": allow_scripts,
            },
        )
        emit(data)
