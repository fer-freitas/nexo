"""Comandos de plataforma.

ajuda, versão, projeto, diagnóstico, configuração, auditoria, login, ciclo de vida, telemetria, isolamento, monitor,
flags, kill switch e guarda.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.table import Table

from nexo import __version__
from nexo.cli import render, surface
from nexo.cli.app import app, console, ctx, emit, err_console
from nexo.cli.registry import COMMANDS
from nexo.kernel.errors import ExitCode, NexoError
from nexo.kernel.i18n import t
from nexo.kernel.platform import config
from nexo.modules.setup.app import banner as banner_app
from nexo.modules.setup.app import doctor, selfmanage
from nexo.modules.setup.app import project as platform_project


@app.command("help", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def help_cmd(context: typer.Context) -> None:
    words = list(context.args)
    if ctx().options.json:
        items = COMMANDS if not words else [s for s in COMMANDS if s.path.startswith(" ".join(words))]
        emit(
            [
                {"command": s.path, "args": s.args, "alias_of": s.alias_of or None}
                for s in items
                if not s.hidden or words
            ]
        )
        return
    if not words or words[0] == "--role":
        render.help_all(console, words[1] if len(words) > 1 else "")
        return
    asked = " ".join(words)
    declared = [c for c in surface.commands_for() if c.path == asked or c.path.startswith(asked + " ")]
    if declared:
        for c in declared:
            console.print(f"nexo {c.path}", style="nexo.title", markup=False, highlight=False)
            console.print(f"  {c.summary}", markup=False, highlight=False)
            if c.target:
                console.print(f"  equivale a: nexo {c.target}", style="nexo.muted", markup=False, highlight=False)
        return
    specs = [s for s in COMMANDS if s.path == asked or s.path.startswith(asked + " ")]
    if not specs:
        err_console.print(t("help.unknown", name=" ".join(words)))
        raise typer.Exit(ExitCode.USAGE)
    render.help_for(console, specs)


@app.command("painel", hidden=True)  # nome antigo (painel agora é só o dashboard web)
@app.command("console")
def console_cmd() -> None:
    """abre o console mesmo fora de um terminal (roteiros, testes e o autoteste de ponta a ponta)."""
    from nexo.cli import session

    raise typer.Exit(session.start(console, ctx().options))


@app.command("selftest")
def selftest_cmd(
    tool: Annotated[str, typer.Option("--agent", help="claude | devin | copilot ...")] = "claude",
    github: Annotated[str, typer.Option("--github", help="repositório dedicado (dono/nexo-selftest)")] = "",
    folder: Annotated[Path | None, typer.Option("--dir", help="pasta do projeto descartável")] = None,
    keep: Annotated[bool, typer.Option("--keep", help="não fecha o PR nem apaga a branch no fim")] = False,
) -> None:
    """fluxo do dia a dia de ponta a ponta com as ferramentas reais, num projeto descartável."""
    from nexo.cli.selftest import SelfTest

    raise typer.Exit(SelfTest(console, tool, github, folder, keep).run())


@app.command("version")
def version_cmd() -> None:
    brain = banner_app.brain_version(ctx().brain_dir)
    if ctx().options.json:
        emit({"nexo": __version__, "brain": brain, "mode": selfmanage.install_mode()})
    else:
        console.print(f"nexo {__version__} · brain {brain or '—'} · {selfmanage.install_mode()}")


@app.command("init")
def init_cmd(
    path: Annotated[Path | None, typer.Argument()] = None,
    adopt: Annotated[str | None, typer.Option("--adopt", help="Migra specs de um workspace para este projeto")] = None,
) -> None:
    result = platform_project.init_project(ctx(), path)
    if adopt:
        from nexo.modules.planning.app.plan import adopt_workspace

        moved = adopt_workspace(ctx(), adopt, result.root)
        if not ctx().options.json:
            console.print(f"Workspace '{adopt}' migrado: {len(moved)} item(ns)")
    if ctx().options.json:
        emit({"root": str(result.root), "created": result.created, "stacks": result.stacks})
        return
    key = "init.done" if result.created else "init.exists"
    console.print(t(key, path=result.root))
    console.print(t("init.stacks", stacks=", ".join(result.stacks) or "—"))


@app.command("doctor")
def doctor_cmd() -> None:
    checks = doctor.run_checks(ctx())
    bad = doctor.problems(checks)
    if ctx().options.json:
        emit({"ok": not bad, "checks": [c.as_dict() for c in checks]})
    else:
        table = Table(title=t("doctor.title"), show_lines=False)
        for col in ("", "item", "categoria", "detalhe"):
            table.add_column(col)
        icons = {"ok": "[nexo.good]✔[/]", "warn": "[nexo.warn]![/]", "fail": "[nexo.bad]✖[/]"}
        for c in checks:
            table.add_row(icons[c.status], c.name + (" *" if c.required else ""), c.category, c.detail)
        console.print(table)
        console.print(t("doctor.ok") if not bad else f"[nexo.bad]{t('doctor.problems', count=len(bad))}[/]")
    if bad:
        raise typer.Exit(ExitCode.ERROR)


config_app = typer.Typer(help="Configuração em cascata", no_args_is_help=True)


app.add_typer(config_app, name="config")


def _level(project: bool) -> str:
    return "project" if project else "personal"


@config_app.command("get")
def config_get(key: str) -> None:
    res = config.resolved(ctx())
    try:
        value = res.get(key)
    except KeyError:
        err_console.print(t("config.not_found", key=key))
        raise typer.Exit(ExitCode.ERROR) from None
    origin = res.origin.get(key, "composto")
    locked = next((lvl for k, lvl in res.locked_by.items() if key == k or key.startswith(k + ".")), None)
    if ctx().options.json:
        emit({"key": key, "value": value, "origin": origin, "locked_by": locked})
    else:
        suffix = f"  [nexo.muted]({origin}{', bloqueada por ' + locked if locked else ''})[/]"
        console.print(f"{key} = {json.dumps(value, ensure_ascii=False)}{suffix}")


@config_app.command("set")
def config_set(
    key: str,
    value: str,
    project: Annotated[bool, typer.Option("--project", help="Grava na camada do projeto")] = False,
) -> None:
    stored = config.set_value(ctx(), key, value, _level(project))
    if ctx().options.json:
        emit({"key": key, "value": stored, "level": _level(project)})
    else:
        console.print(t("config.set", key=key, value=stored, level=_level(project)))


@config_app.command("unset")
def config_unset(
    key: str,
    project: Annotated[bool, typer.Option("--project")] = False,
) -> None:
    if not config.unset_value(ctx(), key, _level(project)):
        err_console.print(t("config.not_found", key=key))
        raise typer.Exit(ExitCode.ERROR)
    console.print(t("config.unset", key=key, level=_level(project)))


@config_app.command("list")
def config_list() -> None:
    from nexo.kernel.config.cascade import flatten

    res = config.resolved(ctx())
    flat = flatten(res.values)
    if ctx().options.json:
        emit({k: {"value": v, "origin": res.origin.get(k)} for k, v in sorted(flat.items())})
        return
    table = Table()
    for col in ("chave", "valor", "origem", "bloqueio"):
        table.add_column(col)
    for k, v in sorted(flat.items()):
        locked = next((lvl for lk, lvl in res.locked_by.items() if k == lk or k.startswith(lk + ".")), "")
        table.add_row(k, json.dumps(v, ensure_ascii=False), res.origin.get(k, ""), locked)
    console.print(table)


audit_app = typer.Typer(help="Auditoria", no_args_is_help=True)


app.add_typer(audit_app, name="audit")


@audit_app.command("verify")
def audit_verify() -> None:
    result = ctx().events.verify()
    if ctx().options.json:
        emit(result.__dict__)
    elif result.ok:
        console.print(f"[nexo.good]{t('audit.ok', count=result.checked)}[/]")
    else:
        err_console.print(f"[nexo.bad]{t('audit.broken', seq=result.broken_at, reason=result.reason)}[/]")
    if not result.ok:
        raise typer.Exit(ExitCode.INTEGRITY_FAILURE)


@audit_app.command("export")
def audit_export_cmd(
    story: Annotated[str | None, typer.Option("--story", help="Item (ex.: US-1-1 ou F-1) e seus derivados")] = None,
    since: Annotated[str | None, typer.Option("--from", help="AAAA-MM-DD")] = None,
    until: Annotated[str | None, typer.Option("--to", help="AAAA-MM-DD (inclusive)")] = None,
    out: Annotated[Path | None, typer.Option("--out", help="Arquivo JSONL de saída")] = None,
) -> None:
    """Exporta a trilha de auditoria com os hashes da cadeia (JSON Lines), para auditoria ou SIEM."""
    from nexo.modules.delivery.app import evidence

    rows = evidence.audit_export(ctx(), story, since, until)
    text = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)
    if out is not None:
        out.write_text(text, encoding="utf-8")
        if not ctx().options.json:
            console.print(f"{len(rows)} evento(s) em {out}")
            return
    if ctx().options.json:
        emit(rows)
    elif out is None:
        console.print(text, markup=False, highlight=False, end="")


guard_app = typer.Typer(help="Guarda do Nexo nas sessões de IA abertas fora do Nexo", no_args_is_help=True)


app.add_typer(guard_app, name="guard")


def _print_guard(data: dict[str, Any]) -> None:
    if ctx().options.json:
        emit(data)
        return
    claude = data["claude"]
    console.print(
        f"Claude Code: {'[nexo.good]guarda instalada[/]' if claude['installed'] else '[nexo.warn]sem guarda[/]'}"
        f" ({claude['settings']})"
    )
    for tool in ("copilot", "devin"):
        console.print(f"{tool}: [nexo.muted]{data[tool]['note']}[/]")
    if data["required"] and not claude["installed"]:
        console.print("[nexo.bad]a org exige a guarda: rode nexo guard install[/]")


@guard_app.command("install")
def guard_install_cmd() -> None:
    """Instala a guarda no Claude Code do usuário: a política de segurança vale também fora do Nexo."""
    from nexo.modules.setup.app import guard

    _print_guard(guard.install(ctx()))


@guard_app.command("uninstall")
def guard_uninstall_cmd() -> None:
    """Remove só a guarda do Nexo das configurações do Claude Code (o resto fica como estava)."""
    from nexo.modules.setup.app import guard

    _print_guard(guard.uninstall(ctx()))


@guard_app.command("status")
def guard_status_cmd() -> None:
    from nexo.modules.setup.app import guard

    _print_guard(guard.status(ctx()))


@app.command("logs")
def logs_cmd(
    ref: Annotated[str | None, typer.Argument()] = None,
    limit: Annotated[int, typer.Option("--limit", "-n")] = 50,
    trace: Annotated[str | None, typer.Option("--trace", help="Replay redigido de uma execução (O-02)")] = None,
    reason: Annotated[str, typer.Option("--reason", help="Motivo da leitura do replay (auditado)")] = "",
) -> None:
    if trace:
        from nexo.kernel.platform import observability

        steps = observability.trace(ctx(), trace, reason)
        if ctx().options.json:
            emit(steps)
            return
        for st in steps:
            console.print(
                f"[nexo.title]{st['step']}[/] {st['role']} · {st['tool']}/{st['model']} · {st['outcome']}",
                highlight=False,
            )
            for ev in st["events"]:
                info = {k: v for k, v in ev.items() if k not in ("at", "kind")}
                console.print(
                    f"  {ev['kind']}: {json.dumps(info, ensure_ascii=False)[:200]}", markup=False, highlight=False
                )
        return
    events = ctx().events.recent(limit=limit, ref=ref)
    if ctx().options.json:
        emit([{"seq": e.seq, "at": e.at, "actor": e.actor, "type": e.type, "payload": e.payload} for e in events])
        return
    table = Table(expand=True)
    table.add_column("#", no_wrap=True)
    table.add_column("quando (UTC)", no_wrap=True)
    table.add_column("quem", no_wrap=True)
    table.add_column("evento", no_wrap=True)
    table.add_column("detalhe", overflow="ellipsis", no_wrap=not ctx().options.verbose, ratio=1)
    for e in events:
        detail = json.dumps(e.payload, ensure_ascii=False)
        table.add_row(str(e.seq), e.at[:19].replace("T", " "), e.actor, e.type, detail)
    console.print(table)


@app.command("install")
def install_cmd(to: Annotated[Path | None, typer.Option("--to")] = None) -> None:
    dest = selfmanage.install(ctx(), to)
    console.print(t("install.done", home=ctx().home))
    if dest is not None:
        console.print(t("install.copied", dest=dest, dir=dest.parent))


@app.command("update")
def update_cmd(manifest: Annotated[str | None, typer.Option("--manifest")] = None) -> None:
    source = manifest or str(config.resolved(ctx()).values.get("update", {}).get("manifest", ""))
    if not source:
        if selfmanage.install_mode() == "package":
            console.print(t("update.pip"))
            return
        raise NexoError("nenhum manifesto de atualização configurado (config: update.manifest)")
    result = selfmanage.update(ctx(), source)
    if result.get("package_mode"):
        console.print(t("update.pip"))
    elif result["updated"]:
        console.print(t("update.done", version=result["version"]))
    else:
        console.print(t("update.uptodate", version=result["version"]))


@app.command("uninstall")
def uninstall_cmd(
    purge: Annotated[bool, typer.Option("--purge")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
) -> None:
    if purge and not yes and not typer.confirm(t("uninstall.confirm", home=ctx().home)):
        purge = False
    result = selfmanage.uninstall(ctx(), purge)
    if result["mode"] == "package":
        console.print(t("uninstall.pip"))
    else:
        console.print(t("uninstall.done"))
    if result["purged"]:
        console.print(t("uninstall.purged", home=result["purged"]))


@app.command("login")
def login_cmd(provider: str) -> None:
    from nexo.modules.planning.app.plan import store_credential

    if provider == "central":  # fluxo de código de dispositivo, sem colar token
        from nexo.kernel.platform import telemetry

        def show(uri: str, code: str) -> None:
            console.print(
                f"Abra [nexo.accent]{uri}[/], entre com sua conta corporativa e informe o código [nexo.title]{code}[/]"
            )

        data = telemetry.login_central(ctx(), show)
        console.print(f"Dispositivo {data['device_id'][:8]}… registrado; o token é renovado sozinho.")
        return
    allowed: tuple[str, ...] = (
        "figma",
        "tracker",
        "github",
        "teams",
        "slack",
        "smtp",
        "collector",
        "confluence",
        "servicenow",
        "anthropic",
        "openai",
        "azure_openai",
        "llm_gateway",  # chaves das APIs das LLMs
        "bedrock",  # ACCESS_KEY:SECRET[:SESSION_TOKEN] ou chave de API do Bedrock
        "gemini_api",
        "gitlab",  # repositório
        "bitbucket",
        "jsm",  # Jira Service Management (sem ela, vale a credencial do Jira)
        "google_chat",  # webhook do espaço
    )
    if provider.startswith("mcp_"):  # credencial de um MCP oficial do catálogo
        from nexo.modules.mcp.app import catalog as mcp_catalog

        allowed = (*allowed, *(mcp_catalog.secret_name(s) for s in mcp_catalog.catalog()))
    from nexo.modules.execution.app import agents as agents_app

    if provider in agents_app.TOOLS:  # login da ferramenta de IA pelo comando oficial dela
        code, after = agents_app.login(ctx(), provider)
        console.print(f"{provider}: {after}")
        raise typer.Exit(0 if code == 0 and after != "sem login" else 1)
    if provider not in allowed:
        raise NexoError(
            f"provedor desconhecido: {provider} (use {', '.join(allowed)}; ferramentas de IA: "
            f"{', '.join(agents_app.TOOLS)})"
        )
    token = typer.prompt(f"Token de {provider}", hide_input=True)
    if not token.strip():
        raise NexoError("token vazio")
    store_credential(ctx(), provider, token.strip())
    console.print(f"Credencial de {provider} guardada no keyring do sistema.")


telemetry_app = typer.Typer(help="Telemetria", no_args_is_help=True)


app.add_typer(telemetry_app, name="telemetry", hidden=True)


def _print_flush(data: dict[str, Any]) -> None:
    if ctx().options.json:
        emit(data)
        return
    line = (
        f"{data['sent']} registro(s) enviados · aceitos {data['accepted']} · já recebidos {data['duplicate']}"
        f" · recusados {data['rejected']} · pendentes {data['pending']}"
    )
    console.print(line + (f" · [nexo.bad]erro: {data['error']}[/]" if data.get("error") else ""))


@telemetry_app.command("flush")
def telemetry_flush_cmd() -> None:
    """Envia agora a outbox pendente e os traces (o envio normal é automático, em segundo plano)."""
    from nexo.kernel.platform import observability, telemetry

    spans = observability.export(ctx()) if observability.pending(ctx()) else None
    if spans is not None and not telemetry.enabled(ctx()):
        if ctx().options.json:
            emit({"spans": spans})
        else:
            console.print(f"{spans['exported']} span(s) enviados ao APM · pendentes {spans['pending']}")
        return
    _print_flush(telemetry.flush(ctx()))


@telemetry_app.command("push", hidden=True)
def telemetry_push_cmd() -> None:
    from nexo.kernel.platform import telemetry

    _print_flush(telemetry.push(ctx()))


@telemetry_app.command("show")
def telemetry_show_cmd(
    pending: Annotated[bool, typer.Option("--pending", help="Só o que ainda vai sair")] = False,
    limit: Annotated[int, typer.Option("--limit")] = 20,
) -> None:
    """Exatamente o que foi e o que será enviado ao coletor (transparência, LGPD art. 9)."""
    from nexo.kernel.platform import telemetry

    rows = telemetry.show(ctx(), pending, limit)
    if ctx().options.json:
        emit(rows)
        return
    for r in rows:
        console.print(f"[nexo.title]{r['type']}[/] ({r['kind']}, {r['status']}) {r['at']}", highlight=False)
        console.print(json.dumps(r["payload"], ensure_ascii=False), markup=False, highlight=False)
    if not rows:
        console.print("nada na outbox")


@telemetry_app.command("status")
def telemetry_status_cmd() -> None:
    from nexo.kernel.platform import telemetry

    data = telemetry.health(ctx())
    if ctx().options.json:
        emit(data)
        return
    state = "ligada" if data["enabled"] else "desligada"
    console.print(
        f"telemetria {state} · contrato {data['contract']} · pendentes {data['pending']}"
        f" (mais antiga há {data['oldest_pending_days']} dias) · dispositivo "
        + ("registrado" if data["registered"] else "não registrado (`nexo login central`)")
    )
    if data["ignored_settings"]:
        console.print(f"[nexo.warn]ignorado (só vale da org/squad): {', '.join(data['ignored_settings'])}[/]")


isolation_app = typer.Typer(help="Modo isolado do agente", no_args_is_help=True)


app.add_typer(isolation_app, name="isolation", hidden=True)


@isolation_app.command("setup")
def isolation_setup_cmd() -> None:
    """Gera o proxy de saída (lista de permissões) e a rede interna para rodar o agente em contêiner."""
    from nexo.kernel.platform import isolation

    data = isolation.setup(ctx())
    if ctx().options.json:
        emit(data)
        return
    console.print(f"proxy de saída em {data['folder']} ({len(data['domains'])} domínios permitidos)")
    console.print(f"suba com: docker compose -f {data['folder']}/egress.compose.yml up -d")


@isolation_app.command("check")
def isolation_check_cmd() -> None:
    from nexo.kernel.platform import isolation

    problems = isolation.check(ctx())
    if ctx().options.json:
        emit({"problems": problems})
    else:
        console.print("\n".join(problems) or "modo isolado pronto (ou não exigido)")
    if problems:
        raise typer.Exit(1)


monitor_app = typer.Typer(help="Monitor sintético", no_args_is_help=True)


app.add_typer(monitor_app, name="monitor", hidden=True)


@monitor_app.command("synthetic")
def monitor_synthetic_cmd(
    route: Annotated[list[str] | None, typer.Option("--route", help="Só estas rotas (id ou ferramenta/modelo)")] = None,
    no_block: Annotated[
        bool, typer.Option("--no-block", help="Não bloqueia a rota localmente em caso de falha")
    ] = False,
) -> None:
    """Tarefa de referência mínima em cada CLI/modelo; formato divergente → alerta e bloqueio da rota."""
    from nexo.modules.setup.app import monitor

    report = monitor.synthetic(ctx(), route or None, False if no_block else None)
    if ctx().options.json:
        emit(report)
    else:
        for p in report["probes"]:
            mark = {"ok": "[nexo.good]✔[/]", "fail": "[nexo.bad]✖[/]", "skipped": "[nexo.muted]–[/]"}[p["status"]]
            detail = f" · {p['kind']}: {p['detail']}" if p["status"] != "ok" else f" · {p['seconds']} s"
            console.print(f"{mark} {p['route']}{detail}", highlight=False)
    if not report["ok"]:
        raise typer.Exit(1)


flags_app = typer.Typer(help="Feature flags", no_args_is_help=True)


app.add_typer(flags_app, name="flags")


@flags_app.command("list")
def flags_list_cmd() -> None:
    """Estado de cada funcionalidade para o seu squad e de onde vem (central, cérebro ou padrão)."""
    from nexo.kernel.platform import flags

    data = flags.status(ctx())
    if ctx().options.json:
        emit(data)
        return
    table = Table(title=f"Feature flags · squad {data['squad'] or '—'}", title_justify="left")
    for col in ("flag", "funcionalidade", "estado", "fonte"):
        table.add_column(col)
    for f in data["flags"]:
        state = "[nexo.good]ligada[/]" if f["enabled"] else "[nexo.bad]desligada[/]"
        table.add_row(f["key"], f["title"], state, f["source"])
    console.print(table)


@flags_app.command("set")
def flags_set_cmd(
    key: Annotated[str, typer.Argument()],
    value: Annotated[str, typer.Argument(help="on | off | inherit")],
    reason: Annotated[str, typer.Option("--reason")] = "",
    squad: Annotated[str | None, typer.Option("--squad", help="Só para este squad (padrão: empresa inteira)")] = None,
    local: Annotated[bool, typer.Option("--local", help="Só nesta máquina; local só desliga")] = False,
) -> None:
    """Liga ou desliga remotamente (central): empresa inteira ou um squad; exige papel admin ou time do Nexo."""
    from nexo.kernel.platform import flags

    mapping = {"on": True, "off": False, "inherit": None}
    if value not in mapping:
        raise NexoError("valor: on | off | inherit")
    if local:
        if value == "on":
            raise NexoError("local só desliga: para ligar, use o portal ou o cérebro")
        emit(flags.set_local(ctx(), key, value == "off"))
        return
    data = flags.set_remote(ctx(), key, mapping[value], squad, reason)
    if ctx().options.json:
        emit(data)
        return
    console.print(f"{key}: {value} ({'squad ' + squad if squad else 'empresa'})")


@flags_app.command("explain")
def flags_explain_cmd(key: str) -> None:
    """Valor, fonte vencedora e o que cada fonte diz."""
    from nexo.kernel.platform import flags

    emit(flags.explain(ctx(), key))


module_app = typer.Typer(help="Módulos do Nexo", no_args_is_help=True)
app.add_typer(module_app, name="module")


@module_app.command("list")
def module_list_cmd() -> None:
    """Módulos, versão, estado e dependências."""
    from nexo.kernel import modules

    rows: list[dict[str, Any]] = [
        {"id": m.id, "version": m.version, "title": m.title, "requires": m.requires, "flags": len(m.flags)}
        for m in modules.builtin()
    ]
    if ctx().options.json:
        emit(rows)
        return
    for r in rows:
        console.print(f"{r['id']:<14} {r['version']:<8} {r['title']}  (requer: {', '.join(r['requires']) or '—'})")


killswitch_app = typer.Typer(help="Kill switch", no_args_is_help=True)


app.add_typer(killswitch_app, name="killswitch", hidden=True)


@killswitch_app.command("status")
def killswitch_status_cmd() -> None:
    """O que está bloqueado agora (cérebro, central e bloqueios locais do monitor) e se esta instalação pode rodar."""
    from nexo.kernel.platform import killswitch

    data = killswitch.status(ctx())
    if ctx().options.json:
        emit(data)
        return
    if data["blocked_here"]:
        console.print(f"[nexo.bad]esta instalação está bloqueada: {data['blocked_here']}[/]", highlight=False)
    empty = True
    for label, key in (
        ("ferramentas", "tools"),
        ("modelos", "models"),
        ("versões do Nexo", "nexo_versions"),
        ("versões do cérebro", "brain_versions"),
    ):
        for name, why in data[key].items():
            empty = False
            console.print(f"{label}: [nexo.title]{name}[/] — {why}", highlight=False)
    console.print("nada bloqueado" if empty else f"fontes: {', '.join(data['sources'])}")
