"""Base de conhecimento e destino da documentação: `docs site` (Docusaurus no GitHub Pages) e `docs target`."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.markup import escape

from nexo.cli import look
from nexo.cli.app import console, ctx, emit
from nexo.cli.commands.knowledge import docs_app
from nexo.kernel.errors import NexoError
from nexo.kernel.platform import config

OPTIONS = (
    ("docusaurus", "Docusaurus no GitHub Pages: base de conhecimento do projeto no próprio repositório"),
    ("confluence", "Confluence: páginas no espaço da empresa"),
    ("azure_wiki", "Azure Wiki: páginas na wiki do projeto no Azure DevOps"),
    ("none", "Só no repositório (.nexo/specs), sem publicar"),
)


@docs_app.command("site")
def docs_site() -> None:
    """Gera ou atualiza a base de conhecimento (Docusaurus) a partir do projeto e do cérebro."""
    from nexo.modules.docsite.app import docsite

    result = docsite.sync(ctx())
    if ctx().options.json:
        emit(result)
        return
    console.print(f"[nexo.good]✔[/] base de conhecimento em {escape(result['site'])}")
    console.print(f"  {result['features']} feature(s) · {result['delivered']} entrega(s)")
    console.print(
        "  [nexo.muted]ver localmente: cd docs-site && npm install && npm start · "
        "`npm run build` gera o site para hospedar[/]"
    )


@docs_app.command("target")
def docs_target(
    target: Annotated[str | None, typer.Argument(help="docusaurus | confluence | azure_wiki | none")] = None,
    level: Annotated[str, typer.Option("--level", help="project ou personal")] = "project",
) -> None:
    """Escolhe onde fica a documentação das features (a org ou o squad também definem no cérebro)."""
    keys = [k for k, _ in OPTIONS]
    if target is None:
        console.print("[nexo.title]Onde fica a documentação das features?[/]")
        for i, (_, label) in enumerate(OPTIONS, start=1):
            console.print(f"  [nexo.key]{i}[/] {escape(label)}")
        try:
            answer = input("  número › ").strip()
        except (EOFError, OSError):
            answer = ""
        if not answer.isdigit() or not 1 <= int(answer) <= len(OPTIONS):
            console.print("[nexo.muted]  nada mudou[/]")
            return
        target = keys[int(answer) - 1]
    if target not in keys:
        raise NexoError(f"destino desconhecido: {target} ({', '.join(keys)})")
    config.set_value(ctx(), "docs.publish.target", target, level=level)
    if target == "docusaurus":
        from nexo.modules.docsite.app import docsite

        root = ctx().project_root
        created = docsite.scaffold(ctx(), root) if root is not None else []
        if not ctx().options.json:
            console.print(f"[nexo.good]✔[/] documentação no Docusaurus ({len(created)} arquivo(s) criados)")
            console.print(f"  gere o conteúdo com [nexo.accent]{look.prefix()}docs site[/]")
    elif not ctx().options.json:
        console.print(f"[nexo.good]✔[/] documentação: {escape(target)}")
        if target in ("confluence", "azure_wiki"):
            console.print(f"  [nexo.muted]conecte a conta com {look.prefix()}login {target.split('_')[0]}[/]")
    if ctx().options.json:
        emit({"target": target})


@docs_app.command("portal")
def docs_portal(
    repos: Annotated[str, typer.Option("--repos", help="Pasta com um clone de cada repositório do projeto")],
    product: Annotated[str | None, typer.Option("--product", help="Projeto (padrão: o do projeto)")] = None,
    out: Annotated[str, typer.Option("--out", help="Pasta do site do portal")] = "portal",
    check: Annotated[
        bool,
        typer.Option("--check", help="Termina com erro se houver desvio do desenho ou entrega sem requisito técnico"),
    ] = False,
) -> None:
    """Monta o portal do projeto (todas as frentes e repositórios) com o desenho da solução; feito para o CI."""
    from pathlib import Path

    from nexo.kernel import services
    from nexo.modules.docsite.app import portal

    pc = services.load("planning.product_context")

    pid = product or pc.project_settings(ctx())[0]
    if not pid:
        raise NexoError("informe o projeto (--product) ou ligue o projeto a um projeto (nexo context)")
    result = portal.build(ctx(), pid, Path(repos), Path(out))
    blocking = [a for a in result["attention"] if a["kind"] in ("desvio", "completude")]
    if ctx().options.json:
        emit(result)
    else:
        console.print(f"[nexo.good]✔[/] portal de {escape(pid)} em {escape(result['out'])}")
        console.print(
            f"  {result['repos_read']} de {result['repos_total']} repositório(s) lidos"
            f" · {result['features']} feature(s) · {result['deliveries']} entrega(s)"
            f" · {result['decisions']} decisão(ões)"
        )
        if result["attention"]:
            console.print(f"[nexo.warn]Precisa de atenção ({len(result['attention'])})[/]")
            for a in result["attention"][:10]:
                console.print(f"  [nexo.muted]{escape(a['kind'])}[/] {escape(a['title'])}")
        console.print("  [nexo.muted]montar o site: npm ci && npm run build (detalhes no README do portal)[/]")
    if check and blocking:
        raise typer.Exit(1)
