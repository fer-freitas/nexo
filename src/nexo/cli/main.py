"""Ponto de entrada da CLI `nexo`: registra os comandos, os stubs de fases futuras e trata erros e auditoria."""

from __future__ import annotations

import contextlib
import errno
import os
import sys
import time
from typing import Any

import typer
from typer._click.exceptions import ClickException, UsageError  # Typer ≥ 0.20 embute o Click

from nexo.cli import look, surface
from nexo.cli.app import UNAUDITED, _state, app, apply_brand, console, ctx, emit, err_console, split_globals
from nexo.cli.commands import context as _context_commands  # noqa: F401 — importar registra os comandos
from nexo.cli.commands import (  # noqa: F401 — importar registra os comandos
    delivery,
    execution,
    finops,
    integrations,
    knowledge,
    merged,
    plan_more,
    planning,
    platform,
    roles,
)
from nexo.kernel.context import AppContext
from nexo.kernel.errors import ExitCode, NexoError, NotAvailableInPhase
from nexo.kernel.i18n import set_lang, t
from nexo.kernel.platform import config

EXT_VERBS = {
    "add": "Adiciona {one} (projeto, squad ou org)",
    "remove": "Remove {one}",
    "list": "Lista {many} em uso e de onde vêm",
    "show": "Mostra {one} e o conteúdo",
    "sync": "Sincroniza {many} com o cérebro",
}
EXT_NOUNS = {"skill": ("uma skill", "as skills"), "hook": ("um hook", "os hooks"), "tool": ("uma tool", "as tools")}
LEAF_HELP = {
    "program status": "Andamento da feature em vários repositórios",
    "approvals list": "Aprovações externas pendentes",
    "approvals sync": "Traz as decisões tomadas fora do terminal (Teams, formulário)",
    "cost budget show": "Orçamento e gasto do mês por dev",
    "cost budget set": "Define o orçamento de um dev, em reais",
    "brain pull": "Baixa a versão mais recente do cérebro (assinatura conferida)",
    "brain status": "Versão do cérebro em uso e se há atualização",
    "brain search": "Busca no cérebro: skills, padrões e lições",
    "brain propose": "Propõe uma mudança ao cérebro (abre um PR)",
    "brain init": "Cria o cérebro da org",
    "brain keygen": "Gera o par de chaves de assinatura do cérebro",
    "brain sign": "Assina uma versão do cérebro",
    "learn collect": "Coleta lições candidatas de PRs, bugs e incidentes",
    "learn review": "Revisa e aprova as lições candidatas",
    "learn status": "Lições pendentes e aprovadas",
    "learn patterns": "Padrões que se repetem nas lições",
    "plan session start": "Abre a sessão conjunta PM + tech lead + dev",
    "plan session close": "Fecha a sessão e gera o pacote para aprovação",
    "template list": "Lista os templates de artefatos",
    "template show": "Mostra um template",
    "template validate": "Confere um artefato contra o template",
    "guard status": "Mostra se a guarda do Nexo está instalada no Claude Code",
    "telemetry status": "Saúde do envio da telemetria: pendências e atraso",
    "isolation check": "Confere se o modo isolado do agente está pronto",
}


def _leaf_help(path: str) -> str:
    words = path.split()
    if words[0] in ("extensions", "admin") and len(words) > 2:
        words = words[1:]  # F51: nome fundido usa a descrição do caminho de origem
    canonical = " ".join(words)
    if canonical in LEAF_HELP:
        return LEAF_HELP[canonical]
    if canonical.startswith("budget "):
        return LEAF_HELP.get(f"cost {canonical}", "")
    if len(words) == 2 and words[0] in EXT_NOUNS and words[1] in EXT_VERBS:
        one, many = EXT_NOUNS[words[0]]
        return EXT_VERBS[words[1]].format(one=one, many=many)
    return ""


def _fill_help(group: typer.Typer, prefix: str = "") -> None:
    """Comando sem docstring ganha a descrição do registro (a mesma da `nexo help`): nenhum fica sem descrição."""
    from nexo.cli.registry import COMMANDS

    summary = {c.path: c.summary_pt for c in COMMANDS}
    for info in group.registered_commands:
        name = info.name or (info.callback.__name__.replace("_cmd", "").replace("_", "-") if info.callback else "")
        path = f"{prefix} {name}".strip()
        if not info.help and not (info.callback and info.callback.__doc__):
            info.help = summary.get(path) or _leaf_help(path) or None
    for sub in group.registered_groups:
        path = f"{prefix} {sub.name}".strip()
        if sub.typer_instance is not None:
            if not sub.help and not sub.typer_instance.info.help and path in summary:
                sub.help = summary[path]
            _fill_help(sub.typer_instance, path)


_fill_help(app)


def _internal(args: list[str]) -> int:
    """Um comando de papel chama outro comando da CLI, com as mesmas regras."""
    from nexo.kernel.platform import flags

    context = _state["ctx"]
    flags.check_command(context, args)
    try:
        result = app(args=args, prog_name="nexo", standalone_mode=False)
    except typer.Exit as exc:
        return int(exc.exit_code)
    return int(result) if isinstance(result, int) else 0


surface.register_modules(app, surface.Tools(ctx, emit, console, err_console, _internal))


def _command_name(args: list[str]) -> str | None:
    return next((a for a in args if not a.startswith("-")), None)


def utf8_streams() -> None:
    """Saída redirecionada no Windows usa cp1252: o banner e os acentos exigem UTF-8 (senão UnicodeEncodeError)."""
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if encoding != "utf8" and hasattr(stream, "reconfigure"):
            with contextlib.suppress(ValueError, OSError):
                stream.reconfigure(encoding="utf-8", errors="replace")


def _telemetry_session_start(context: Any) -> None:
    """Início de sessão: envia pendências em segundo plano e avisa se a telemetria está atrasada."""
    from nexo.kernel.platform import observability, telemetry

    with contextlib.suppress(OSError):  # O-02: retenção das transcrições (uma vez por dia)
        stamp = context.home / "transcripts.purged"
        if not stamp.is_file() or time.time() - stamp.stat().st_mtime > 86400:
            observability.purge_transcripts(context)
            stamp.touch()

    try:
        telemetry.trigger(context)
        level, days = telemetry.delay(context)
    except NexoError:
        return
    if level != "ok" and not context.options.quiet:
        msg = "execuções de agente bloqueadas" if level == "block" else "sincronize logo"
        err_console.print(
            f"[nexo.warn]telemetria sem enviar há {days:.1f} dias — {msg} (`nexo admin telemetry flush`)[/]"
        )


def main(argv: list[str] | None = None) -> int:
    utf8_streams()
    raw = list(sys.argv[1:] if argv is None else argv)
    try:
        opts, args = split_globals(raw)
    except UsageError as exc:
        err_console.print(f"{t('error.prefix')}: {exc.message}")
        return ExitCode.USAGE
    if args[:1] in (["--help"], ["-h"]):
        args = ["help", *args[1:]]
    args = surface.translate(args)
    context = AppContext(options=opts)
    _state["ctx"] = context
    apply_brand(context)
    from nexo.cli.shared import _ui
    from nexo.kernel.feedback import set_feedback

    set_feedback(_ui)  # F52: toda chamada de IA mostra que está trabalhando
    lang = opts.lang
    if lang is None:
        try:
            lang = str(config.resolved(context).values.get("lang", "pt-BR"))
        except (OSError, ValueError):
            lang = "pt-BR"
    set_lang(lang)
    name = _command_name(args)
    started = time.monotonic()
    code = ExitCode.OK
    try:
        if name not in UNAUDITED:
            context.audit("command.start", {"argv": raw, "cwd": str(context.cwd)})
        if name not in (*UNAUDITED, "brain", "uninstall", "hook", "mcp", "console", None):
            from nexo.modules.knowledge.app import brain as knowledge_brain

            if not look.IN_CONSOLE["on"] and knowledge_brain.auto_pull_due(context):  # no console, pergunta-se
                knowledge_brain.spawn_background_pull(context)
            if name != "telemetry":
                _telemetry_session_start(context)
        from nexo.kernel.platform import flags, observability

        if name not in UNAUDITED:
            flags.check_command(context, args)  # F17: funcionalidade desligada por feature flag
        with observability.span(context, f"nexo {name}", command=name) as command_span:
            try:
                result = app(args=args, prog_name="nexo", standalone_mode=False)
            except (NexoError, typer.Exit, typer.Abort, UsageError, ClickException):
                raise
            except Exception as exc:
                if isinstance(exc, OSError) and closed_pipe(exc):
                    silence_stdout()  # quem lê a saída fechou antes (`| head`, `grep -q`): não é erro
                    result = ExitCode.OK
                else:
                    report = observability.crash_report(context, exc, name or "")
                    err_console.print(
                        f"[nexo.bad]{t('error.prefix')}:[/] erro inesperado ({report['type']}). Um "
                        "relatório sanitizado "
                        "foi salvo e segue com a telemetria para o time do Nexo."
                    )
                    if os.environ.get("NX_DEBUG") == "1":
                        raise
                    result = ExitCode.ERROR
            code = int(result) if isinstance(result, int) else ExitCode.OK
            command_span.set(exit_code=code)
    except NexoError as exc:
        if isinstance(exc, NotAvailableInPhase):
            err_console.print(f"[nexo.warn]{t('phase.not_available', command=exc.command, phase=exc.phase)}[/]")
        else:
            err_console.print(f"[nexo.bad]{t('error.prefix')}:[/] {exc.message}")
        code = exc.exit_code
    except typer.Exit as exc:
        code = exc.exit_code
    except typer.Abort:
        code = ExitCode.ERROR
    except UsageError as exc:
        exc.show(file=sys.stderr)
        code = ExitCode.USAGE
    except ClickException as exc:
        exc.show(file=sys.stderr)
        code = exc.exit_code
    finally:
        look.stop_active()
        if name not in UNAUDITED and context.home.exists():
            # uninstall --purge remove o banco; nesse caso não há onde registrar
            with contextlib.suppress(Exception):
                context.audit(
                    "command.end",
                    {"command": name, "exit_code": code, "ms": int((time.monotonic() - started) * 1000)},
                )
        if context.home.exists():
            from nexo.kernel.platform import observability

            with contextlib.suppress(Exception):  # traces do comando vão para o spool (exportados em segundo plano)
                observability.spool(context)
        if name not in (*UNAUDITED, "telemetry", "uninstall") and context.home.exists():
            from nexo.kernel.platform import telemetry

            with contextlib.suppress(Exception):  # fim do comando: o que ficou na outbox sai em segundo plano
                telemetry.trigger(context)
        context.close()
    return code


def closed_pipe(exc: OSError) -> bool:
    """Quem lê a saída fechou antes (`nexo ... | head`, `grep -q`). No Windows chega como OSError(EINVAL)."""
    if isinstance(exc, BrokenPipeError) or exc.errno == errno.EPIPE:
        return True
    return sys.platform == "win32" and exc.errno == errno.EINVAL and _stdout_broken()


def _stdout_broken() -> bool:
    try:
        sys.stdout.flush()
    except OSError:
        return True
    return False


def silence_stdout() -> None:
    with contextlib.suppress(OSError, ValueError):
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())


def run() -> None:
    try:
        code = main()
        sys.stdout.flush()
    except OSError as exc:  # saída fechada no flush final: encerra em silêncio, como as CLIs Unix
        if not closed_pipe(exc):
            raise
        silence_stdout()
        code = 0
    sys.exit(code)
