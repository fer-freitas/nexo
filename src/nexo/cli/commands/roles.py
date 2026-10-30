"""comandos de cada papel além do planejamento comum.

- Tech lead: `tech analyze F-n` (analisa o requisito funcional) e `tech spec F-n` (requisito técnico com premissas).
- Dev: `tasks` (o que está pendente), `explain T-n|US-n` (o que a tarefa pede) e `check US-n` (está tudo certo?).
"""

from __future__ import annotations

from typing import Annotated, Any

import typer
from rich.markup import escape

from nexo.cli import look
from nexo.cli.app import app, console, ctx, emit
from nexo.cli.commands.planning import _planner

tech_app = typer.Typer(
    help="Tech lead: analisar o requisito funcional e escrever o requisito técnico", no_args_is_help=True
)
app.add_typer(tech_app, name="tech")

SEVERITY = {"blocking": "bloqueia", "important": "importante", "optional": "opcional"}


def _list(title: str, items: list[Any], style: str = "") -> None:
    if not items:
        return
    console.print(f"\n  [nexo.title]{escape(title)}[/]")
    for item in items:
        console.print(f"    {'[' + style + ']' if style else ''}• {escape(str(item))}{'[/]' if style else ''}")


@tech_app.command("analyze")
def tech_analyze(feature: str) -> None:
    """Analisa o requisito funcional: lacunas, riscos, o que é afetado e perguntas para o PM."""
    from nexo.modules.planning.app import techlead

    data = techlead.analyze(_planner(None), feature)
    if ctx().options.json:
        emit(data)
        return
    a = data["analysis"]
    verdict = (
        "[nexo.good]pronto para o requisito técnico[/]"
        if a["ready_for_tech"]
        else "[nexo.warn]há lacuna funcional para decidir[/]"
    )
    effort = {"P": "pequeno", "M": "médio", "G": "grande"}[a["effort"]]
    console.print(f"\n[nexo.title]{escape(feature)}[/]  esforço {effort} · {verdict}")
    console.print(f"  {escape(a['summary'])}")
    gaps = [
        f"({SEVERITY[g['severity']]}) {g['text']}" + (f" — sugestão: {g['suggestion']}" if g.get("suggestion") else "")
        for g in a["gaps"]
    ]
    _list("Lacunas técnicas (você decide)", gaps)
    risks = [r["title"] + (f" — {r['mitigation']}" if r.get("mitigation") else "") for r in a["risks"]]
    _list("Riscos técnicos", risks)
    _list("O que é afetado", a["affected"])
    if a["functional_gaps"]:
        _list(
            "Lacunas funcionais que escaparam do refinamento",
            [f"({SEVERITY[g['severity']]}) {g['text']}" for g in a["functional_gaps"]],
            "nexo.warn",
        )
        console.print(
            f"\n  você decide: assumir como premissa ([nexo.accent]{look.prefix()}tech spec {escape(feature)}[/]) "
            f"ou devolver ao PM ([nexo.accent]{look.prefix()}tech return {escape(feature)}[/])"
        )
    else:
        console.print(f"\n  próximo passo: [nexo.accent]{look.prefix()}tech spec {escape(feature)}[/]")
    console.print(f"  [nexo.muted]documento: {escape(data['document'])}[/]")


@tech_app.command("return")
def tech_return(
    feature: str,
    reason: Annotated[str, typer.Option("--reason", help="Por que volta ao PM")] = "",
) -> None:
    """Devolve ao PM as lacunas funcionais que a análise técnica achou (decisão do tech lead)."""
    from nexo.modules.planning.app import techlead

    data = techlead.return_to_pm(_planner(None), feature, reason)
    if ctx().options.json:
        emit(data)
        return
    _list("Voltaram para o PM", [f"{x['id']} {x['text']}" for x in data["questions"]], "nexo.warn")
    console.print(f"\n  o PM responde com [nexo.accent]{look.prefix()}plan answer {escape(feature)}[/]")


@tech_app.command("spec")
def tech_spec(
    feature: str,
    notes: Annotated[
        str, typer.Option("--notes", help="Orientação do tech lead (padrões, restrições, preferências)")
    ] = "",
) -> None:
    """Escreve o requisito técnico com premissas: design, decisões, NFR, riscos, dependências e testes."""
    from nexo.modules.planning.app import techlead

    data = techlead.tech_spec(_planner(None), feature, notes)
    if ctx().options.json:
        emit(data)
        return
    console.print(f"\n[nexo.title]{escape(feature)}[/]  requisito técnico escrito")
    if data["premises"]:
        console.print("\n  [nexo.title]Premissas[/]")
        for p in data["premises"]:
            console.print(f"    • {escape(p['text'])}")
            if p.get("if_false"):
                console.print(f"      [nexo.muted]se não se confirmar: {escape(p['if_false'])}[/]")
    console.print(f"\n  seções da spec atualizadas: {escape(', '.join(data['sections']) or 'nenhuma')}")
    console.print(f"  [nexo.muted]documento: {escape(data['document'])}[/]")
    console.print(f"  próximo passo: [nexo.accent]{look.prefix()}plan approve {escape(feature)}[/]")


@app.command("tasks")
def tasks_cmd(all_: Annotated[bool, typer.Option("--all", help="Inclui as concluídas")] = False) -> None:
    """Minhas tarefas: o que está pendente, por história."""
    from nexo.modules.execution.app import devflow

    groups = devflow.my_tasks(ctx(), all_)
    if ctx().options.json:
        emit(groups)
        return
    if not groups:
        console.print("Nenhuma tarefa pendente. [nexo.muted]Planeje uma feature ou use --all.[/]")
        return
    for g in groups:
        state = look.state_label(str(g["state"])) if g["state"] != "—" else "—"
        console.print(
            f"\n[nexo.title]{escape(str(g['story']))}[/] {escape(str(g['title']))}  [nexo.muted]{escape(state)}[/]"
        )
        for t in g["tasks"]:
            files = f"{t['files']} arquivo{'s' if t['files'] != 1 else ''}" if t["files"] else "sem arquivos sugeridos"
            style = look.STATE_STYLE.get(t["state"], "")
            label = escape(look.state_label(t["state"]))
            mark = f"[{style}]{label}[/]" if style else label
            console.print(f"  {escape(t['id']):<9} {escape(t['title'])}  [nexo.muted]{t['size']} · {files} ·[/] {mark}")
    console.print(
        f"\n[nexo.muted]{look.prefix()}explain <T-n> explica a tarefa · "
        f"{look.prefix()}check <US-n> confere a história[/]"
    )


@app.command("explain")
def explain_cmd(
    item: str,
    ask: Annotated[bool, typer.Option("--ask", help="Leva ao tech lead as dúvidas que sobraram da explicação")] = False,
) -> None:
    """Explica a tarefa (ou a história) antes de começar: o quê, por quê, onde mexer, cuidados e como validar."""
    from nexo.cli.shared import _ui
    from nexo.modules.execution.app import devflow

    if ask:
        sent = devflow.ask_tech_lead(ctx(), item)
        if ctx().options.json:
            emit(sent)
            return
        _list("Foram para o tech lead", [f"{x['id']} {x['text']}" for x in sent["questions"]], "nexo.warn")
        console.print(
            f"\n  o tech lead responde com [nexo.accent]{look.prefix()}plan answer {escape(sent['feature'])}[/]"
        )
        return
    data = devflow.explain(ctx(), item, _ui)
    if ctx().options.json:
        emit(data)
        return
    console.print(f"\n[nexo.title]{escape(data['item'])}[/]")
    console.print(f"\n  [nexo.title]O quê[/]\n    {escape(data['what'])}")
    console.print(f"\n  [nexo.title]Por quê[/]\n    {escape(data['why'])}")
    _list("Onde mexer", data.get("where") or [])
    _list("Cuidados", data.get("watch_out") or [], "nexo.warn")
    _list("Como validar", data.get("how_to_check") or [])
    if data.get("unclear"):
        _list("Não está claro (pergunte antes de começar)", data["unclear"], "nexo.bad")


@app.command("check")
def check_cmd(story: str) -> None:
    """Confere se a história está pronta de verdade: DoR, critérios, tarefas, testes, pipeline, portão, PR e custo."""
    from nexo.modules.execution.app import devflow

    data = devflow.check(ctx(), story)
    if ctx().options.json:
        emit(data)
        return
    state = escape(look.state_label(data["state"]))
    console.print(f"\n[nexo.title]{escape(data['story'])}[/] {escape(data['title'])}  [nexo.muted]{state}[/]")
    for item in data["items"]:
        mark = {True: "[nexo.good]✔[/]", False: "[nexo.bad]✖[/]", None: "[nexo.muted]–[/]"}[item["ok"]]
        show_fix = item["fix"] and item["ok"] is not True
        fix = f"  [nexo.accent]{look.prefix()}{escape(item['fix'])}[/]" if show_fix else ""
        console.print(f"  {mark} {escape(item['label']):<26} [nexo.muted]{escape(item['detail'])}[/]{fix}")
    if data["ok"]:
        console.print("\n[nexo.good]Tudo certo:[/] a história está concluída e conferida.")
    else:
        pending = sum(1 for i in data["items"] if i["ok"] is False)
        console.print(
            f"\n[nexo.warn]{pending} item(ns) pedem ação[/]" if pending else "\n[nexo.muted]Ainda em andamento.[/]"
        )
