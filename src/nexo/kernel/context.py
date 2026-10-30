"""Contexto de execução: caminhos, projeto atual, conexão local e auditoria."""

from __future__ import annotations

import getpass
import os
import sqlite3
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

import platformdirs

from nexo.store.db import connect
from nexo.store.events import EventStore

PROJECT_DIR = ".nexo"
PROJECT_MARKER = "nexo.yaml"  # nome reservado: nunca usar `config.yaml` nesta pasta


def nexo_home() -> Path:
    """Dados da máquina: `NX_HOME`, `NX_DATA_HOME` ou a pasta de dados do usuário no sistema."""
    env = os.environ.get("NX_HOME") or os.environ.get("NX_DATA_HOME")
    if env:
        # absoluto: hooks e servidor MCP rodam na pasta do agente
        return Path(env).expanduser().resolve()
    return Path(platformdirs.user_data_dir("nexo", appauthor=False))


def nexo_config_home() -> Path:
    """Configuração pessoal: `NX_HOME`, `NX_CONFIG_HOME` ou a pasta de configuração do usuário no sistema."""
    env = os.environ.get("NX_HOME") or os.environ.get("NX_CONFIG_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return Path(platformdirs.user_config_dir("nexo", appauthor=False))


def find_project_root(start: Path) -> Path | None:
    current = start.resolve()
    home = Path.home().resolve()
    for candidate in (current, *current.parents):
        if (candidate / PROJECT_DIR / PROJECT_MARKER).is_file():
            return candidate
        if (candidate / ".git").exists():
            break  # repositório git sem .nexo é um projeto próprio: não herda o projeto de uma pasta acima
        if candidate == home:
            break
    return None


def _current_project() -> Path | None:
    """Quem não clona código (PM, QA) trabalha no projeto escolhido em `nexo join`, dentro do repositório do time."""
    path = nexo_config_home() / "current-project.txt"
    if not path.is_file():
        return None
    target = Path(path.read_text(encoding="utf-8").strip())
    return target if (target / PROJECT_DIR / PROJECT_MARKER).is_file() else None


def find_git_root(start: Path) -> Path | None:
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


@dataclass
class Options:
    json: bool = False
    quiet: bool = False
    verbose: bool = False
    dry_run: bool = False
    lang: str | None = None
    profile: str | None = None
    tool: str | None = None
    model: str | None = None


@dataclass
class AppContext:
    cwd: Path = field(default_factory=Path.cwd)
    home: Path = field(default_factory=nexo_home)
    options: Options = field(default_factory=Options)

    @cached_property
    def project_root(self) -> Path | None:
        return find_project_root(self.cwd) or _current_project()

    @property
    def db_path(self) -> Path:
        return self.home / "nexo.db"

    @property
    def personal_config(self) -> Path:
        return nexo_config_home() / "config.yaml"

    @property
    def brain_dir(self) -> Path:
        return self.home / "brain"

    def project_config(self, root: Path | None = None) -> Path | None:
        base = root or self.project_root
        return base / PROJECT_DIR / PROJECT_MARKER if base else None

    @cached_property
    def conn(self) -> sqlite3.Connection:
        return connect(self.db_path)

    @cached_property
    def events(self) -> EventStore:
        from nexo.kernel.platform import telemetry

        return EventStore(self.conn, outbox=telemetry.enabled(self), context=lambda: telemetry.batch_context(self))

    @cached_property
    def actor(self) -> str:
        """Identidade local (usuário do SO). A identidade forte vem do tracker/GitHub.
        `NX_ACTOR` existe para automação e testes; a origem fica registrada na auditoria."""
        override = os.environ.get("NX_ACTOR")
        if override:
            return override
        try:
            return getpass.getuser()
        except (KeyError, OSError):
            return "unknown"

    def audit(self, type_: str, payload: dict[str, Any], ref: str | None = None) -> None:
        self.events.append(self.actor, type_, payload, ref=ref)
        from nexo.kernel import events

        events.publish(self, type_, payload, ref)

    def close(self) -> None:
        if "conn" in self.__dict__:
            self.conn.close()
