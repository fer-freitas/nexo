"""Comandos `nexo lineage`: de onde vem e para onde vai cada componente."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from nexo.kernel.errors import ExitCode, NexoError
from nexo.modules.lineage.app import changes, lineage


def register(root: typer.Typer, tools: Any) -> None:
    ctx, emit, console = tools.ctx, tools.emit, tools.console
    app = typer.Typer(help="Linhagem de componentes", no_args_is_help=True)
    root.add_typer(app, name="lineage")

    @app.command("scan")
    def scan_cmd() -> None:
        """Gera o manifesto de origin/main, sem publicar."""
        emit(lineage.scan(ctx()))

    @app.command("publish")
    def publish_cmd() -> None:
        """Publica no repositório do time."""
        emit(lineage.publish(ctx()))

    @app.command("sync")
    def sync_cmd() -> None:
        """Traz os repositórios dos outros times, só leitura."""
        emit(lineage.sync_teams(ctx()))

    @app.command("show")
    def show_cmd(component: str) -> None:
        """De onde vem e para onde vai, com o estado de cada ligação."""
        data = lineage.show(ctx(), component)
        if ctx().options.json:
            emit(data)
            return
        console.print(f"{data['component']}  {data.get('runtime', '')}", markup=False)
        console.print("ENTRADAS")
        for r in data["entries"] or []:
            console.print(
                f"  {r['channel']:<36} ◄ {r['component']:<30} {r['state']}  {r.get('message', '')}", markup=False
            )
        console.print("SAÍDAS")
        for r in data["outputs"] or []:
            console.print(
                f"  {r['channel']:<36} ► {r['component']:<30} {r['state']}  {r.get('message', '')}", markup=False
            )

    @app.command("upstream")
    def upstream_cmd(component: str, depth: Annotated[int, typer.Option("--depth")] = 5) -> None:
        """Quem vem antes."""
        emit(lineage.walk(ctx(), component, "upstream", depth))

    @app.command("downstream")
    def downstream_cmd(component: str, depth: Annotated[int, typer.Option("--depth")] = 5) -> None:
        """Quem vem depois."""
        emit(lineage.walk(ctx(), component, "downstream", depth))

    @app.command("impact")
    def impact_cmd(target: str) -> None:
        """Quem pode ser afetado: consumidores conhecidos, nunca "sem consumidores"."""
        data = lineage.impact(ctx(), target)
        if ctx().options.json:
            emit(data)
            return
        for r in data["known_consumers"]:
            console.print(f"  {r['component']} ({r['channel']}, {r['state']})", markup=False)
        if not data["known_consumers"]:
            console.print("Nenhum consumidor conhecido.")
        console.print(data["note"])

    @app.command("coverage")
    def coverage_cmd() -> None:
        """O que falta para ficar completa."""
        data = lineage.coverage(ctx())
        if ctx().options.json:
            emit(data)
            return
        console.print(f"Cobertura: {data['confirmed']} de {data['channels']} canais confirmados ({data['percent']}%)")
        for r in data["pending"]:
            console.print(f"  {r['channel']}: {r['state']}", markup=False)
        if data["unresolved"]:
            console.print(f"{data['unresolved']} ligação(ões) não resolvida(s)")
        console.print(f"Última passada do agente: {data['agents']['last_run'] or 'nunca'}")

    @app.command("export")
    def export_cmd(obsidian: Annotated[Path, typer.Option("--obsidian", help="Pasta fora do Git")]) -> None:
        """Gera as notas com links para o Obsidian."""
        emit(lineage.export_obsidian(ctx(), obsidian))

    @app.command("notify")
    def notify_cmd(target: str, summary: Annotated[str, typer.Option("--summary", help="O que mudou")]) -> None:
        """Avisa os times consumidores conhecidos, por issue."""
        emit(lineage.notify_consumers(ctx(), target, summary))

    @app.command("validate")
    def validate_cmd() -> None:
        """Confere os manifestos desta máquina. Um adulterado é ignorado na leitura e aparece aqui."""
        data = changes.validate(ctx())
        emit(data)
        if data["invalid"]:
            raise typer.Exit(ExitCode.GATE)

    @app.command("confirm")
    def confirm_cmd(
        where: Annotated[str, typer.Argument(help="arquivo:linha do não resolvido")] = "",
        channel: Annotated[str, typer.Option("--channel", help="Nome do canal")] = "",
        direction: Annotated[str, typer.Option("--direction", help="publishes ou consumes")] = "publishes",
        kind: Annotated[str, typer.Option("--kind", help="kafka, sns, sqs ou http")] = "kafka",
        component: Annotated[str, typer.Option("--component", help="Componente; padrão: o repositório")] = "",
    ) -> None:
        """Sem argumentos, lista o que o scanner não resolveu. Com eles, grava a confirmação de uma pessoa."""
        if not where:
            emit(changes.unresolved(ctx()))
            return
        if not channel:
            raise NexoError("informe --channel")
        emit(changes.confirm(ctx(), where, channel, direction, kind, component))

    discover = typer.Typer(help="Lê a infraestrutura, só por comando de quem tem permissão", no_args_is_help=True)
    app.add_typer(discover, name="discover")

    @discover.command("aws")
    def discover_aws_cmd() -> None:
        """Assinaturas SNS→SQS, gatilhos de Lambda e serviços ECS, sem o ID da conta."""
        emit(changes.discover_aws(ctx()))

    @discover.command("kafka")
    def discover_kafka_cmd(
        source: Annotated[Path, typer.Option("--from-file", help='JSON exportado: {"grupo": ["tópico"]}')],
    ) -> None:
        """Grupos de consumidores viram nós não identificados até o time dono se apresentar."""
        emit(changes.discover_kafka(ctx(), source))

    agent = typer.Typer(help="Gatilho automático na máquina", no_args_is_help=True)
    app.add_typer(agent, name="agent")

    @agent.command("install")
    def agent_install() -> None:
        """Instala a tarefa de usuário e o hook local. Não precisa de administrador."""
        emit(lineage.install_agent(ctx()))

    @agent.command("uninstall")
    def agent_uninstall() -> None:
        """Remove a tarefa e o hook."""
        emit(lineage.uninstall_agent(ctx()))

    @agent.command("status")
    def agent_status() -> None:
        """Última passada e último erro."""
        emit(lineage.agent_state(ctx()))

    @agent.command("run")
    def agent_run(repo: Annotated[Path | None, typer.Option("--repo")] = None) -> None:
        """Uma passada. Se nada mudou, termina sem custo."""
        emit(lineage.run_agent(ctx(), repo))
