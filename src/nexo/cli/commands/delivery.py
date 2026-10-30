"""Comandos de entrega.

mudança, programas multi-repositório, dependências, revisão de PR, evidências, aprovações externas e PR.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from nexo.cli.app import app, console, ctx, emit, err_console
from nexo.kernel.errors import ExitCode
from nexo.kernel.finops.money import brl

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
    from nexo.modules.delivery.app import review as delivery_review

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
    from nexo.modules.delivery.app import evidence

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
    from nexo.modules.delivery.app import evidence

    result = evidence.verify(ctx(), folder)
    if ctx().options.json:
        emit(result)
    elif result["ok"]:
        console.print(f"[nexo.good]pacote íntegro[/] · {'assinatura válida' if result['signed'] else result['reason']}")
    else:
        err_console.print(f"[nexo.bad]pacote inválido:[/] {result['reason']}")
    if not result["ok"]:
        raise typer.Exit(ExitCode.INTEGRITY_FAILURE)


approvals_app = typer.Typer(help="Aprovações externas", no_args_is_help=True)


app.add_typer(approvals_app, name="approvals", hidden=True)


@approvals_app.command("list")
def approvals_list_cmd(all_: Annotated[bool, typer.Option("--all", help="Inclui as resolvidas")] = False) -> None:
    from nexo.modules.delivery.app import approval

    rows = approval.listing(ctx(), only_pending=not all_)
    if ctx().options.json:
        emit(rows)
        return
    table = Table(title="Aprovações externas", title_justify="left")
    for col in ("id", "tipo", "item", "canal", "status", "pedido"):
        table.add_column(col)
    for r in rows:
        table.add_row(r["id"], r["kind"], r["target"], r["channel"], r["status"], r["detail"].get("title", ""))
    console.print(table)


@approvals_app.command("sync")
def approvals_sync_cmd() -> None:
    from nexo.modules.delivery.app import approval

    resolved = approval.sync(ctx())
    if ctx().options.json:
        emit(resolved)
        return
    for r in resolved:
        extra = f" · crédito {brl(r['granted_brl'])}" if "granted_brl" in r else ""
        extra += f" · {r['target']} → {r['state']}" if "state" in r else ""
        console.print(f"{r['request']} ({r['kind']}): {r['decision']}{extra}")
    if not resolved:
        console.print("nenhum sinal novo")


@approvals_app.command("signal")
def approvals_signal_cmd(request_id: str, decision: str) -> None:
    """Repassa um sinal recebido fora do Nexo (continue ou stop)."""
    from nexo.modules.delivery.app import approval

    result = approval.signal(ctx(), request_id, decision)
    if ctx().options.json:
        emit(result)
        return
    console.print(f"{result['request']}: {result['decision']}" + (f" · {result['state']}" if "state" in result else ""))


pr_app = typer.Typer(help="Pull requests das histórias", no_args_is_help=True)


app.add_typer(pr_app, name="pr")


@pr_app.command("sync")
def pr_sync_cmd() -> None:
    """Reage ao CI e aos pedidos de mudança nos PRs; no L3 faz o merge com CI verde (também roda no `nexo watch`)."""
    from nexo.modules.delivery.app import pr

    actions = pr.sync(ctx())
    if ctx().options.json:
        emit(actions)
        return
    for a in actions:
        console.print(f"{a['story']}: {a['action']}" + (f" → {a['state']}" if a.get("state") else ""))
    if not actions:
        console.print("nada a fazer")
