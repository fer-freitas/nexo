"""Comandos de entrega.

mudança, programas multi-repositório, dependências, revisão de PR, evidências, aprovações externas e PR.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from nexo.cli import look
from nexo.cli.app import app, console, ctx, emit, err_console
from nexo.kernel.errors import ExitCode

deps_app = typer.Typer(help="Dependências e vulnerabilidades em escala", no_args_is_help=True)


app.add_typer(deps_app, name="deps")


@deps_app.command("scan")
def deps_scan_cmd() -> None:
    """Lê dependências vulneráveis e desatualizadas com as ferramentas da stack (pip-audit, npm, dotnet)."""
    from nexo.modules.deps.app import deps

    data = deps.scan(ctx())
    if ctx().options.json:
        emit(data)
        return
    rows = data["findings"]
    table = Table(title=f"Dependências ({', '.join(data['managers'])})", title_justify="left")
    for col in ("pacote", "atual", "alvo", "tipo", "mudança", "severidade"):
        table.add_column(col)
    for f in sorted(rows, key=lambda r: (r["kind"] != "security", r["package"])):
        worst = max((v["severity"] for v in f["vulns"]), default="")
        table.add_row(f["package"], f["current"] or "?", f["target"] or "—", f["kind"], f["change"], worst)
    console.print(table)
    console.print(f"  próximo passo: [nexo.accent]{look.prefix()}deps plan[/]")


@deps_app.command("plan")
def deps_plan_cmd(
    major: Annotated[bool, typer.Option("--major", help="Inclui atualizações de versão maior (portão humano)")] = False,
) -> None:
    """Cria a feature de atualização: uma história por atualização, aplicada pelo Nexo, testada pelo pipeline."""
    from nexo.modules.deps.app import deps

    out = deps.plan_feature(ctx(), major)
    if ctx().options.json:
        emit(out)
        return
    if not out["feature"]:
        console.print(out["message"])
        return
    console.print(f"{out['feature']}: {len(out['stories'])} história(s) de atualização")
    for s in out["stories"]:
        gate = "  [nexo.warn](portão humano)[/]" if s["id"] in out["human_gates"] else ""
        console.print(f"  {s['id']} — {s['title']}{gate}")
    console.print(f"  próximo passo: [nexo.accent]{look.prefix()}run feature {out['feature']}[/]")


review_app = typer.Typer(help="Revisão de PRs feitos por humanos com as regras do Nexo", no_args_is_help=True)


app.add_typer(review_app, name="review")


@review_app.command("pr")
def review_pr_cmd(
    number: int,
    post: Annotated[
        bool, typer.Option("--post", help="Publica a revisão no PR (comentário ou pedido de mudança)")
    ] = False,
    no_quality: Annotated[
        bool, typer.Option("--no-quality", help="Sem o pipeline de qualidade (só revisores)")
    ] = False,
) -> None:
    """Revisa um PR humano com o pipeline de qualidade e os revisores do Nexo; nunca aprova sozinho."""
    from nexo.kernel import services

    delivery_review = services.load("delivery.review")

    result = delivery_review.review(ctx(), number, post=post, quality=not no_quality)
    if ctx().options.json:
        emit(result.__dict__)
        return
    console.print(delivery_review.render(result).replace(delivery_review.MARK, "").strip(), markup=False)
    if result.posted:
        console.print(f"publicado no PR #{number} como {result.posted}")


evidence_app = typer.Typer(help="Pacote de evidências por release (auditoria e regulador)", no_args_is_help=True)


app.add_typer(evidence_app, name="evidence", hidden=True)


@evidence_app.command("build")
def evidence_build_cmd(
    feature: str,
    out: Annotated[Path | None, typer.Option("--out", help="Pasta de destino (padrão .nexo/evidencias)")] = None,
    key: Annotated[str | None, typer.Option("--key", help="Chave para assinar: PEM ou azkv://…")] = None,
) -> None:
    """Monta o dossiê verificável da feature: aprovações, portões, testes, segurança, PRs, custo e trilha."""
    from nexo.kernel import services

    evidence = services.load("delivery.evidence")

    result = evidence.build(ctx(), feature, out, key)
    if ctx().options.json:
        emit(result)
        return
    audit = result["audit"]
    console.print(f"Pacote de evidências: {result['folder']}")
    console.print(
        f"  trilha íntegra: {'sim' if audit['chain_ok'] else '[nexo.bad]não[/]'} · {audit['events']} "
        "evento(s) da entrega"
        f" · {'assinado' if result['signed'] else 'sem assinatura (só hashes)'}"
    )


@evidence_app.command("verify")
def evidence_verify_cmd(folder: Path) -> None:
    """Confere os hashes de todos os arquivos e a assinatura do pacote."""
    from nexo.kernel import services

    evidence = services.load("delivery.evidence")

    result = evidence.verify(ctx(), folder)
    if ctx().options.json:
        emit(result)
    elif result["ok"]:
        console.print(f"[nexo.good]pacote íntegro[/] · {'assinatura válida' if result['signed'] else result['reason']}")
    else:
        err_console.print(f"[nexo.bad]pacote inválido:[/] {result['reason']}")
    if not result["ok"]:
        raise typer.Exit(ExitCode.INTEGRITY_FAILURE)
