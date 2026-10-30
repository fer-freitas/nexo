"""comandos fundidos por assunto. Monta os grupos novos sobre as mesmas funções; o nome antigo continua
registrado (oculto) para não quebrar roteiros nem a documentação já publicada.

- `extensions skill|hook|tool` (antes `skill`, `hook`, `tool`)
- `cost usage|budget|pricing|estimate` (antes `usage`, `budget`, `pricing`, `estimate`)
- `metrics baseline` (antes `baseline`); `quality standards|perf` (antes `standards`, `perf`)
- `audit evidence` (antes `evidence`)
- `admin flags|killswitch|insights|telemetry|autonomy|gates|monitor|isolation`
- `inbox`: tudo o que espera por você (antes `plan inbox` e `approvals list`)
"""

from __future__ import annotations

import typer

from nexo.cli.app import app, console, ctx, emit
from nexo.cli.commands import delivery, execution, finops, knowledge, planning, platform

extensions_app = typer.Typer(help="Extensões do time: skills, hooks e tools (servidores MCP)", no_args_is_help=True)
extensions_app.add_typer(knowledge.skill_app, name="skill")
extensions_app.add_typer(execution.hook_app, name="hook")
extensions_app.add_typer(knowledge.tool_app, name="tool")
app.add_typer(extensions_app, name="extensions")

finops.cost_app.add_typer(finops.usage_app, name="usage")
finops.cost_app.add_typer(finops.budget_app, name="budget")
finops.cost_app.add_typer(finops.pricing_app, name="pricing")
finops.cost_app.command("estimate")(planning.estimate_cmd)

metrics_app = typer.Typer(help="Eficácia, eficiência e linha de base", invoke_without_command=True)


@metrics_app.callback()
def metrics_root(context: typer.Context) -> None:
    """Eficácia, eficiência e primeira entrega certa (sem subcomando); `metrics baseline` para a linha de base."""
    if context.invoked_subcommand is None:
        finops.metrics_cmd()


metrics_app.command("baseline")(planning.baseline_cmd)
app.add_typer(metrics_app, name="metrics")

execution.quality_app.add_typer(execution.standards_app, name="standards")
execution.quality_app.add_typer(planning.perf_app, name="perf")
platform.audit_app.add_typer(delivery.evidence_app, name="evidence")

admin_app = typer.Typer(help="Administração do Nexo (org, central e segurança)", no_args_is_help=True)
for name, group in (
    ("flags", platform.flags_app),
    ("killswitch", platform.killswitch_app),
    ("insights", finops.insights_app),
    ("telemetry", platform.telemetry_app),
    ("autonomy", execution.autonomy_app),
    ("gates", execution.gates_app),
    ("monitor", platform.monitor_app),
    ("isolation", platform.isolation_app),
):
    admin_app.add_typer(group, name=name)


@admin_app.command("autopilot")
def autopilot_cmd(force: bool = typer.Option(False, "--force", help="roda todas as tarefas agora")) -> None:
    """O que o Nexo faz sozinho: sincronizar, fechar o mês, varrer dependências, conferir o portão."""
    from nexo.modules.setup.app import autopilot

    data = autopilot.run(ctx(), force=force)
    if ctx().options.json:
        emit(data)
        return
    console.print(f"feito: {', '.join(data['done']) or 'nada vencido'}")
    for name, error in data["errors"].items():
        console.print(f"[nexo.warn]{name}: {error}[/]")
    for alert in data.get("alerts", []):
        console.print(f"  • {alert['label']}  [nexo.muted]nexo {alert['command']}[/]")


app.add_typer(admin_app, name="admin")


@app.command("inbox")
def inbox_cmd() -> None:
    """O que espera por você: partes do plano do seu papel, dúvidas, DoR, portões e o que escalou."""
    from nexo.modules.setup.app import pending

    data = pending.inbox(ctx())
    if ctx().options.json:
        emit(data)
        return
    roles = ", ".join(data["roles"]) or "sem papel definido"
    console.print(f"{data['who']} · {roles}")
    if not data["items"]:
        console.print("  [nexo.good]nada esperando por você[/]")
    for i, item in enumerate(data["items"], start=1):
        detail = f" [nexo.muted]— {item['detail']}[/]" if item["detail"] else ""
        console.print(f"  [nexo.accent]{i}[/]. {item['label']}{detail}  [nexo.muted]nexo {item['command']}[/]")
