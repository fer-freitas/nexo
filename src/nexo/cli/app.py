"""Aplicação Typer raiz da CLI: console, opções globais, contexto da execução e banner."""

from __future__ import annotations

import contextlib
import json
import sys
from typing import Any

import typer
from rich.console import Console
from rich.theme import Theme, ThemeStackError
from typer._click.exceptions import UsageError  # Typer ≥ 0.20 embute o Click

from nexo.cli import render
from nexo.kernel.brand import terminal_styles
from nexo.kernel.context import AppContext, Options
from nexo.modules.setup.app import banner as banner_app
from nexo.modules.setup.app import brand as brand_app

THEME = Theme(terminal_styles(brand_app.default()))  # estilos `nexo.*`; a marca da org entra em `apply_brand`
console = Console(highlight=False, soft_wrap=True, theme=THEME)
err_console = Console(stderr=True, highlight=False, soft_wrap=True, theme=THEME)


_brand_applied: dict[str, str] = {}


def apply_brand(context: AppContext) -> None:
    """Cores da marca da org (cérebro ou `NX_BRAND`) no console. Marca inválida avisa e segue com o padrão."""
    from nexo.kernel.errors import NexoError

    path = brand_app.override_path(context.brain_dir)
    try:
        key = f"{path}:{path.stat().st_mtime_ns}" if path else ""
    except OSError:
        key = str(path)
    if key == _brand_applied.get("key", ""):  # o console roda vários comandos: aplica uma vez só
        return
    _brand_applied["key"] = key
    try:
        styles = terminal_styles(brand_app.load(context.brain_dir))
    except NexoError as exc:
        err_console.print(f"[nexo.warn]marca ignorada: {exc.message}[/]")
        return
    for target in (console, err_console):
        with contextlib.suppress(ThemeStackError):
            target.pop_theme()  # tira a marca anterior (se houver); o tema padrão fica na base
        target.push_theme(Theme(styles))


def _output_closed() -> None:
    """Quem lê a saída fechou antes (`nexo ... | head`, `grep -q`): encerra com sucesso e em silêncio, como as CLIs
    Unix. O Rich, por padrão, sai com código 1 (no Windows o cano fechado ainda virava OSError; achado no CI)."""
    import os

    with contextlib.suppress(OSError, ValueError):
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    raise SystemExit(0)


console.on_broken_pipe = _output_closed  # type: ignore[method-assign]

GLOBAL_FLAGS = {"--json": "json", "--quiet": "quiet", "--verbose": "verbose", "--dry-run": "dry_run"}
GLOBAL_VALUES = {"--lang": "lang", "--profile": "profile", "--tool": "tool", "--model": "model"}
UNAUDITED = {"help", "version", None}

_state: dict[str, AppContext] = {}


def ctx() -> AppContext:
    return _state["ctx"]


def split_globals(argv: list[str]) -> tuple[Options, list[str]]:
    """Extrai opções globais de qualquer posição da linha de comando."""
    opts = Options()
    rest: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            rest.extend(argv[i:])
            break
        name, _, inline = arg.partition("=")
        if arg in GLOBAL_FLAGS:
            setattr(opts, GLOBAL_FLAGS[arg], True)
        elif name in GLOBAL_VALUES:
            if inline:
                value = inline
            elif i + 1 < len(argv):
                i += 1
                value = argv[i]
            else:
                raise UsageError(f"{name} requer um valor")
            setattr(opts, GLOBAL_VALUES[name], value)
        else:
            rest.append(arg)
        i += 1
    return opts, rest


def emit(data: Any) -> None:
    console.print_json(json.dumps(data, ensure_ascii=False, default=str))


app = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    invoke_without_command=True,
    context_settings={"help_option_names": []},
    pretty_exceptions_enable=False,
)


@app.callback()
def root(context: typer.Context) -> None:
    if context.invoked_subcommand is None:
        if ctx().options.quiet:
            return
        if not ctx().options.json and sys.stdin.isatty() and sys.stdout.isatty():
            from nexo.cli import session

            raise typer.Exit(session.start(console, ctx().options))
        info = banner_app.collect(ctx())
        if ctx().options.json:
            emit(info.__dict__)
        else:
            render.banner(console, info)
