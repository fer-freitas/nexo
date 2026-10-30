"""CLI por papel: `nexo pm|qa|tl|dev`. Os atalhos vêm dos manifestos e viram o comando interno antes de executar.

Assim flags, auditoria e testes valem igual para os dois nomes, e uma operação tem uma só implementação.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import typer

from nexo.kernel import modules

ROLES = {"pm": "PM", "qa": "QA", "tl": "Tech lead", "dev": "Dev"}


def aliases() -> dict[str, str]:
    return {c.path: c.target for m in modules.builtin() for c in m.commands if c.target}


def translate(args: list[str]) -> list[str]:
    """`pm reject F-1 --reason x` → `plan reject F-1 --part requisitos --reason x`."""
    if len(args) < 2:
        return args
    target = aliases().get(" ".join(args[:2]))
    if target is None:
        return args
    words = target.split()
    rest = list(args[2:])
    out: list[str] = []
    for word in words:
        if word == "{0}":
            if not rest:
                return args[:2] + ["--help"]
            out.append(rest.pop(0))
        else:
            out.append(word)
    return out + rest


def commands_for(role: str | None = None) -> list[modules.CommandDecl]:
    rows = [c for m in modules.builtin() for c in m.commands]
    if role is None:
        return rows
    return [c for c in rows if role in c.roles]


@dataclass
class Tools:
    """O que um módulo recebe para registrar comandos, sem importar a aplicação da CLI."""

    ctx: Callable[[], Any]
    emit: Callable[[Any], None]
    console: Any
    err_console: Any
    internal: Callable[[list[str]], int]


def role_app(name: str, inbox: Callable[[str], None]) -> typer.Typer:
    group = typer.Typer(help=f"Comandos de {ROLES[name]}", no_args_is_help=False)

    @group.callback(invoke_without_command=True)
    def show(context: typer.Context) -> None:
        if context.invoked_subcommand is None:
            inbox(name)

    return group


def register_modules(root: typer.Typer, tools: Tools) -> None:
    for manifest in modules.builtin():
        if not manifest.cli:
            continue
        module, _, name = manifest.cli.partition(":")
        getattr(importlib.import_module(module), name)(root, tools)
