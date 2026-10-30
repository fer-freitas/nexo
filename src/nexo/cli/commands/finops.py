"""Comandos de FinOps: preços do contrato, custo, consumo, métricas, orçamento e insights."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from nexo.cli import look, render
from nexo.cli.app import app, console, ctx, emit
from nexo.kernel.errors import ExitCode
from nexo.kernel.finops.money import brl

pricing_app = typer.Typer(help="Preços do contrato do cliente (câmbio e assinaturas)", no_args_is_help=True)


app.add_typer(pricing_app, name="pricing", hidden=True)


@pricing_app.command("show")
def pricing_show_cmd() -> None:
    """Preços que valem hoje, de onde vêm (fábrica, org, squad, projeto) e o que ainda é estimado."""
    from nexo.modules.finops.app import pricing

    data = pricing.show(ctx())
    if ctx().options.json:
        emit(data)
        return
    fx = data["fx"]
    console.print(
        f"Câmbio: R$ {fx['current'].get('usd_brl')} por US$ desde {fx['current'].get('valid_from')}"
        + (" [nexo.warn](fábrica)[/]" if fx["factory"] else "")
    )
    table = Table()
    for col in ("ferramenta", "US$/mês", "por unidade US$", "excedente", "inclusos", "vigências", "origem"):
        table.add_column(col)
    for s in data["subscriptions"]:
        cur = s["current"]
        over = (cur.get("overage_usd") and f"US$ {cur['overage_usd']}") or (
            cur.get("overage_brl") and f"R$ {cur['overage_brl']}"
        )
        table.add_row(
            s["tool"] + ("" if s["in_use"] else " [nexo.muted](sem uso)[/]"),
            str(cur.get("usd_month", "—")),
            str(cur.get("unit_price_usd") or cur.get("credit_price_usd") or "—"),
            str(over or "—"),
            str(cur.get("included_units") if cur.get("included_units") is not None else "—"),
            str(s["versions"]),
            "[nexo.warn]tabela pública[/]" if s["factory"] else ", ".join(o for o in s["origin"] if o != "default"),
        )
    console.print(table)
    console.print(f"[nexo.muted]Tabela pública de {data['list_prices_as_of']}; conferir com o contrato.[/]")
    for issue in data["issues"]:
        console.print(f"  [nexo.warn]·[/] {issue['item']}: {issue['reason']}", highlight=False)


@pricing_app.command("set")
def pricing_set_cmd(
    item: Annotated[str, typer.Argument(help="fx ou <ferramenta>.<campo> (ex.: copilot.usd_month)")],
    value: Annotated[str, typer.Argument(help="valor (US$, R$ por US$ no câmbio, ou unidade)")],
    valid_from: Annotated[
        str | None, typer.Option("--from", help="início da vigência AAAA-MM-DD (cria nova vigência)")
    ] = None,
    brain: Annotated[
        Path | None, typer.Option("--brain", help="clone do cérebro: grava o preço da org (depois assinar e PR)")
    ] = None,
    squad: Annotated[str | None, typer.Option("--squad", help="com --brain: preço só deste squad")] = None,
) -> None:
    """Informa um preço do contrato. Sem --brain, grava na camada do projeto."""
    from nexo.modules.finops.app import pricing

    data = pricing.set_price(ctx(), item, value, valid_from, brain, squad)
    if ctx().options.json:
        emit(data)
        return
    console.print(
        f"{data['item']}: {data['field']} = {data['value']} desde {data['valid_from']} ({data['level']})",
        highlight=False,
    )
    for step in data.get("next", []):
        console.print(f"  próximo passo: {look.hint(step)}", markup=False, highlight=False)


@pricing_app.command("check")
def pricing_check_cmd() -> None:
    """Sai com erro quando algum custo ainda é estimado (preço de fábrica ou ferramenta sem preço)."""
    from nexo.modules.finops.app import pricing

    issues = pricing.check(ctx())
    if ctx().options.json:
        emit({"ok": not issues, "issues": issues})
    elif not issues:
        console.print("Preços do contrato informados para tudo que está em uso.")
    for issue in [] if ctx().options.json else issues:
        console.print(f"  [nexo.warn]·[/] {issue['item']}: {issue['reason']}", highlight=False)
    if issues:
        raise typer.Exit(ExitCode.ERROR)


cost_app = typer.Typer(help="Custo em BRL", invoke_without_command=True)


app.add_typer(cost_app, name="cost")


usage_app = typer.Typer(help="IA usada dentro e fora do Nexo (Claude Code, Copilot, Devin)", no_args_is_help=True)


app.add_typer(usage_app, name="usage", hidden=True)


@usage_app.command("scan")
def usage_scan_cmd(full: Annotated[bool, typer.Option("--full", help="Relê todo o histórico")] = False) -> None:
    """Lê os registros locais das ferramentas de IA e atualiza os totais do seu consumo."""
    from nexo.modules.usage.app import external_usage

    out = external_usage.scan(ctx(), full)
    if ctx().options.json:
        emit(out)
        return
    console.print(f"{out['sessions']} sessão(ões) em {out['rows']} total(is) por dia × ferramenta × modelo")


@usage_app.command("show")
def usage_show_cmd(month: Annotated[str | None, typer.Option("--month", help="AAAA-MM")] = None) -> None:
    """Seu consumo de IA no mês por ferramenta: dentro do Nexo, fora dele e a parte feita pelo Nexo."""
    from nexo.modules.usage.app import external_usage

    data = external_usage.summary(ctx(), month)
    if ctx().options.json:
        emit(data)
        return
    table = Table(title=f"IA no mês {data['month']}", title_justify="left")
    for col in ("ferramenta", "no Nexo R$", "fora R$", "sessões fora", "tokens fora", "unidade de cobrança fora"):
        table.add_column(col)
    for tool, sides in sorted(data["tools"].items()):
        out = sides.get("fora") or {}
        units = out.get("units") or {}
        billing = {"claude": "", "copilot": "premium_request", "devin": "acu"}.get(tool, "")
        extra = f"{units.get(billing, 0):.2f} {billing}" if billing else "—"
        if tool == "copilot" and units.get("ai_credits"):
            extra += f" · {units['ai_credits']:.2f} AI Credits"
        tokens = int(units.get("tokens_in", 0) + units.get("tokens_out", 0))
        table.add_row(
            tool,
            f"{(sides.get('nexo') or {}).get('brl', 0):.2f}",
            f"{out.get('brl', 0):.2f}",
            str(out.get("sessions", 0)),
            f"{tokens:,}".replace(",", "."),
            extra,
        )
    console.print(table)
    share = data["nexo_share_pct"]
    console.print(
        f"  parte feita pelo Nexo: {share if share is not None else '—'}%"
        + ("  ·  enviado ao central (só totais por dia)" if data["shared"] else "  ·  fica só nesta máquina")
    )
    if not data["scanned_at"]:
        console.print("  [nexo.muted]rode nexo cost usage scan para ler os registros das ferramentas[/]")


@cost_app.callback()
def cost_cmd(
    context: typer.Context,
    story: Annotated[str | None, typer.Option("--story")] = None,
    dev: Annotated[str | None, typer.Option("--dev")] = None,
    project: Annotated[bool, typer.Option("--project", help="Agrupa por projeto (todos os projetos)")] = False,
    month: Annotated[str | None, typer.Option("--month")] = None,
    by: Annotated[str, typer.Option("--by", help="story|dev|project|month|tool")] = "story",
    watch: Annotated[bool, typer.Option("--watch", help="Com --story: acompanha o custo ao vivo")] = False,
    interval: Annotated[float, typer.Option("--interval", help="Segundos entre atualizações do --watch")] = 5.0,
) -> None:
    if context.invoked_subcommand is not None:
        return
    from nexo.modules.finops.app import costs

    if watch:
        if not story:
            raise typer.BadParameter("--watch exige --story")
        _watch_story_cost(story, interval)
        return

    group_by = "project" if project else ("dev" if dev and by == "story" else by)
    data = costs.cost_report(ctx(), group_by, month, story, dev, all_projects=project)
    if ctx().options.json:
        emit(data)
        return
    title = f"Custo por {group_by}" + (f" · {story}" if story else "") + (f" · {month}" if month else "")
    table = Table(title=title, title_justify="left")
    for col in ("item", "uso (tabela)", "alocado (rateio)", "por ferramenta", "unidades"):
        table.add_column(col)
    for row in data["rows"]:
        tools = ", ".join(f"{t} {brl(v)}" for t, v in row["by_tool"].items())
        units = ", ".join(f"{k} {v:,.0f}".replace(",", ".") for k, v in row["units"].items())
        table.add_row(
            row["key"],
            f"{brl(row['usage_brl'])}",
            f"{brl(row['allocated_brl'])}",
            tools,
            units + (" (estimado)" if row["estimated"] else ""),
        )
    console.print(table)
    if data.get("experiments_brl"):
        console.print(f"Experimentos (`nexo try`): {brl(data['experiments_brl'])}")
    note = f" · provisório: {', '.join(data['provisional'])}" if data["provisional"] else ""
    console.print(f"Total: uso {brl(data['total_usage_brl'])} · alocado {brl(data['total_allocated_brl'])}{note}")
    if data.get("estimated_prices"):
        console.print(
            f"[nexo.warn]Preço de tabela pública (estimado) para {', '.join(data['estimated_prices'])};"
            " informe o contrato com nexo cost pricing set.[/]"
        )


def _watch_story_cost(story: str, interval: float) -> None:
    """Custo ao vivo da história: atualiza até ela sair dos estados em andamento."""

    from nexo.modules.execution.app import status as execution_status

    while True:
        data = execution_status.story_cost(ctx(), story)
        if ctx().options.json:
            emit(data)
        else:
            cap = f" de {render.brl(data['cap_brl'])} de teto" if data["cap_brl"] is not None else ""
            proj = f" · projeção {render.brl(data['projection_brl'])}" if data["projection_brl"] is not None else ""
            console.print(f"{story} · {data['state']} · custo {render.brl(data['cost_brl'])}{cap}{proj}")
        if data["state"] not in execution_status.ACTIVE_STATES:
            return
        time.sleep(max(1.0, interval))


@cost_app.command("reconcile")
def cost_reconcile_cmd(invoice: Annotated[Path, typer.Option("--invoice", help="CSV: tool,month,amount_brl")]) -> None:
    """Fatura/relatório do fornecedor × total medido no coletor (divergência > 5% destacada)."""
    import csv

    from nexo.kernel.platform import telemetry

    with invoice.open(encoding="utf-8") as fh:
        rows = [
            {"tool": r["tool"].strip(), "month": r["month"].strip(), "amount_brl": float(r["amount_brl"])}
            for r in csv.DictReader(fh)
        ]
    result = telemetry.reconcile(ctx(), rows)
    if ctx().options.json:
        emit(result)
        return
    for r in result:
        flag = "[nexo.bad]divergente[/]" if r["divergent"] else "[nexo.good]ok[/]"
        console.print(
            f"{r['tool']} {r['month']}: fatura {brl(r['amount_brl'])} × medido {brl(r['measured_brl'])}"
            f" ({r['diff_pct']:+.1f}%) {flag}"
        )


@cost_app.command("chargeback")
def cost_chargeback_cmd(
    month: Annotated[str, typer.Option("--month", help="AAAA-MM")],
    csv_out: Annotated[Path | None, typer.Option("--csv", help="Salva também o CSV para o FinOps")] = None,
) -> None:
    """Custo do mês por centro de custo: uso pelo Nexo, uso fora do Nexo e licenças rateadas por squad."""
    from nexo.kernel.platform import telemetry

    data = telemetry.chargeback(ctx(), month, csv_out)
    if ctx().options.json:
        emit(data)
        return
    table = Table(title=f"Custo por centro de custo · {data['month']}", title_justify="left")
    for col in ("centro de custo", "squads", "Nexo R$", "fora R$", "licenças R$", "total R$"):
        table.add_column(col, justify="right" if "R$" in col else "left")
    for r in data["rows"]:
        table.add_row(
            r["cost_center"],
            ", ".join(r["squads"]) or "—",
            *(f"{r[k]:.2f}" for k in ("nexo_brl", "outside_brl", "licenses_brl", "total_brl")),
        )
    console.print(table)
    console.print(f"Total: {render.brl(data['total_brl'])}" + (f" · CSV em {data['csv']}" if data.get("csv") else ""))


@cost_app.command("close")
def cost_close_cmd(month: Annotated[str, typer.Option("--month")]) -> None:
    from nexo.modules.finops.app import costs

    data = costs.close_month(ctx(), month)
    console.print(f"Mês {data['month']} fechado: {data['steps']} passo(s) · {brl(data['total_brl'])} alocados")


def metrics_cmd() -> None:  # `nexo metrics` é grupo (registrado em commands/merged.py)
    from nexo.modules.finops.app import metrics as finops_metrics

    report = finops_metrics.metrics(ctx())
    if ctx().options.json:
        emit(report.__dict__)
        return

    def pct(v: float | None) -> str:
        return "—" if v is None else f"{v * 100:.0f}%"

    def num(v: float | None, prefix: str = "", suffix: str = "") -> str:
        return "—" if v is None else f"{prefix}{v:.2f}{suffix}"

    table = Table(title=f"Métricas · {report.done}/{report.stories} história(s) concluída(s)", title_justify="left")
    table.add_column("grupo")
    table.add_column("métrica")
    table.add_column("valor")
    for group, name, value in (
        ("Eficácia", "E2E sem humano", pct(report.e2e_without_human)),
        ("Eficácia", "Primeira entrega certa (FPY)", pct(report.first_pass_yield)),
        ("Eficácia", "Tentativas médias", num(report.avg_attempts)),
        ("Eficácia", "Taxa de escalonamento", pct(report.escalation_rate)),
        ("Eficiência", "R$/história", num(report.brl_per_story, "R$ ")),
        ("Eficiência", "R$/ponto", num(report.brl_per_point, "R$ ")),
        ("Eficiência", "Tokens/ponto", num(report.tokens_per_point)),
        ("Eficiência", "Lead time", num(report.lead_time_h, suffix=" h")),
        ("Eficiência", "Cycle time", num(report.cycle_time_h, suffix=" h")),
        ("Eficiência", "Espera por humano", num(report.waiting_human_h, suffix=" h")),
        ("Planejamento", "Sem dúvida nova na execução", pct(report.planning_accuracy)),
    ):
        table.add_row(group, name, value)
    console.print(table)


budget_app = typer.Typer(help="Orçamento por dev", no_args_is_help=True)


app.add_typer(budget_app, name="budget", hidden=True)


@budget_app.command("show")
def budget_show_cmd(
    dev: Annotated[str | None, typer.Option("--dev")] = None,
    month: Annotated[str | None, typer.Option("--month")] = None,
) -> None:
    from nexo.modules.finops.app import costs

    costs.sync_limits(ctx(), month, force=True)
    status = costs.budget_status(ctx(), dev, month)
    if ctx().options.json:
        emit({**status.__dict__, "ratio": status.ratio})
        return
    limit = f"{brl(status.limit_brl)}" if status.limit_brl is not None else "sem limite"
    console.print(
        f"{dev or ctx().actor} · {month or costs.current_month()}: {brl(status.spent_brl)} de {limit}"
        + (f" ({status.ratio * 100:.0f}%)" if status.limit_brl else "")
    )
    for tool, value in status.by_tool.items():
        extra = f" / limite {brl(status.tool_limits[tool])}" if tool in status.tool_limits else ""
        console.print(f"  {tool}: {brl(value)}{extra}" + ("" if status.tool_ok(tool) else " [nexo.bad]esgotado[/]"))
    for tool, u in status.units.items():
        over = u["used"] - u["included"]
        console.print(
            f"  {tool}: {u['used']:.1f} de {u['included']:.0f} ACU inclusos"
            + (f" [nexo.warn](+{over:.1f} excedente)[/]" if over > 0 else f" (saldo {-over:.1f})")
        )


@budget_app.command("set")
def budget_set_cmd(
    limit: Annotated[float, typer.Option("--limit")],
    dev: Annotated[str | None, typer.Option("--dev")] = None,
    tool: Annotated[str, typer.Option("--for", help="Limite só para uma ferramenta")] = "",
    month: Annotated[str | None, typer.Option("--month")] = None,
) -> None:
    from nexo.modules.finops.app import costs

    costs.set_budget(ctx(), dev or ctx().actor, limit, tool, month)
    console.print(f"Orçamento de {dev or ctx().actor}{' · ' + tool if tool else ''}: {brl(limit)}")


@budget_app.command("watch")
def budget_watch_cmd(install: Annotated[bool, typer.Option("--install")] = False) -> None:
    """Retoma o que estava bloqueado quando a cota/orçamento libera (agendador do SO opcional)."""
    from nexo.modules.execution.app import watcher

    if install:
        path, instructions = watcher.install(ctx())
        console.print(f"Agendamento gerado em {path}\n{instructions}")
        return
    resumed = watcher.resume_ready(ctx())
    console.print(f"{len(resumed)} item(ns) retomado(s): {', '.join(resumed) or '—'}")


insights_app = typer.Typer(help="Insights do central", no_args_is_help=True)


app.add_typer(insights_app, name="insights", hidden=True)


@insights_app.command("list")
def insights_list_cmd(
    state: Annotated[
        str, typer.Option("--state", help="aberto | reconhecido | resolvido | descartado | all")
    ] = "aberto",
) -> None:
    """Insights do dashboard central visíveis para você (seu squad e o seu painel)."""
    from nexo.kernel.platform import telemetry

    items = telemetry.insights(ctx(), "" if state == "all" else state)
    if ctx().options.json:
        emit(items)
        return
    for i in items:
        console.print(f"[nexo.title]{i['id']}[/] [{i['severity']}] {i['title']} ({i['state']})", highlight=False)
        console.print(f"  ação: {i['action']} · dono sugerido: {i['owner']}", highlight=False)
    if not items:
        console.print("nenhum insight")


@insights_app.command("set")
def insights_set_cmd(
    insight_id: Annotated[str, typer.Argument()],
    state: Annotated[str, typer.Argument(help="reconhecido | resolvido | descartado")],
    reason: Annotated[str, typer.Option("--reason", help="Obrigatório para descartar")] = "",
) -> None:
    """Muda o estado de um insight (com motivo; descartado silencia o detector por 7 dias)."""
    from nexo.kernel.platform import telemetry

    data = telemetry.set_insight(ctx(), insight_id, state, reason)
    if ctx().options.json:
        emit(data)
        return
    console.print(f"{data['id']} → {data['state']}")
