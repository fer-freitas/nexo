"""Comandos de manutenção do que já existe: modernização de legado em ondas e do incidente à correção."""

from __future__ import annotations

from typing import Annotated

import typer

from nexo.cli import look
from nexo.cli.app import app, console, ctx, emit

modernize_app = typer.Typer(help="Modernização de legado em ondas", no_args_is_help=True)


app.add_typer(modernize_app, name="modernize")


@modernize_app.command("playbooks")
def modernize_playbooks_cmd() -> None:
    """Roteiros de modernização disponíveis (embutidos e do cérebro)."""
    from nexo.modules.legacy.app import modernize

    rows = modernize.playbooks(ctx())
    if ctx().options.json:
        emit(rows)
        return
    for r in rows:
        console.print(f"  {r['name']:<16} {r['title']}")


@modernize_app.command("plan")
def modernize_plan_cmd(
    playbook: Annotated[str | None, typer.Option("--playbook", help="Roteiro (nexo modernize playbooks)")] = None,
    find: Annotated[str | None, typer.Option("--find", help="Expressão regular do padrão antigo")] = None,
    goal: Annotated[str, typer.Option("--goal", help="O que fazer (com --find)")] = "",
    include: Annotated[str, typer.Option("--include", help="Globs separados por vírgula (com --find)")] = "",
) -> None:
    """Cria a feature de modernização em ondas: um módulo por história, na ordem das dependências."""
    from nexo.modules.legacy.app import modernize

    out = modernize.plan(ctx(), playbook, find, goal, include)
    if ctx().options.json:
        emit(out)
        return
    if not out["feature"]:
        console.print(out["message"])
        return
    console.print(f"{out['feature']}: {out['occurrences']} ocorrência(s) em {len(out['stories'])} módulo(s)")
    for i, wave in enumerate(out["waves"], 1):
        console.print(f"  onda {i}: {', '.join(wave)}")
    console.print(
        f"  próximo passo: [nexo.accent]{look.prefix()}run feature {out['feature']}[/] · progresso: "
        "nexo modernize status"
    )


@modernize_app.command("status")
def modernize_status_cmd(feature: str) -> None:
    """Progresso da modernização: ocorrências do padrão antigo antes e agora, por módulo."""
    from nexo.modules.legacy.app import modernize

    st = modernize.status(ctx(), feature)
    if ctx().options.json:
        emit(st)
        return
    console.print(f"{st['feature']} · {st['title']}: {st['progress']}% ({st['before']} → {st['now']} ocorrências)")
    for m in st["modules"]:
        console.print(f"  {m['module']:<30} {m['before']:>4} → {m['now']:<4} {m['progress']}%")
    if st["new_outside_plan"]:
        console.print(f"  [nexo.warn]novas ocorrências fora do plano: {', '.join(st['new_outside_plan'])}[/]")


incident_app = typer.Typer(help="Do incidente à correção", no_args_is_help=True)


app.add_typer(incident_app, name="incident")


@incident_app.command("open")
def incident_open_cmd(
    path: Annotated[str, typer.Argument(help="Arquivo JSON do incidente ou - para ler da entrada")],
    source: Annotated[str, typer.Option("--source", help="auto | generic | servicenow | sentry")] = "auto",
) -> None:
    """Transforma um incidente ou alerta numa história de correção com pilha, arquivos e commit suspeito."""
    from nexo.modules.legacy.app import incident

    out = incident.open_incident(ctx(), path, source)
    if ctx().options.json:
        emit(out)
        return
    console.print(f"{out['incident']} → {out['feature']} ({out['story']})")
    console.print(f"  arquivos da pilha: {', '.join(out['files']) or 'nenhum identificado'}")
    for s in out["suspects"]:
        console.print(f"  commit suspeito: {s['sha'][:12]} {s['subject']} ({s['author']})")
    if out["human_gate"]:
        console.print("  [nexo.warn]portão humano: incidente crítico ou texto com suspeita de injeção[/]")
    console.print(
        f"  próximo passo: [nexo.accent]{look.prefix()}run story {out['story']}[/] (o QA escreve "
        "primeiro o teste de regressão)"
    )
