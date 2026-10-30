"""Contrato de módulo: tudo o que um módulo oferece está no `module.yaml` dele, sem tabelas centrais.

O carregador valida os manifestos, recusa dependência circular e comando repetido, e entrega à CLI e às flags o
catálogo pronto. Módulo externo entra pelo grupo de entry points `nexo.modules`, se estiver em `org/modules.yaml`.
"""

from __future__ import annotations

import functools
from importlib import metadata
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from ruamel.yaml import YAML

from nexo.kernel.errors import NexoError

STAGES = ("experimental", "beta", "ga", "deprecated")
FLOOR = frozenset(
    {
        "policy",
        "security_hooks",
        "redaction",
        "audit",
        "brain_signature",
        "identity",
        "hard_gates",
        "quality_gates",
        "killswitch",
        "budget_limit",
        "required_isolation",
    }
)
ROLES = ("pm", "qa", "tl", "dev", "all")
BUILTIN_ROOT = Path(__file__).resolve().parents[2]


class FlagDecl(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    title: str
    stage: Literal["experimental", "beta", "ga", "deprecated"] = "ga"
    default: bool = True
    area: str = ""
    commands: list[str] = Field(default_factory=list)
    where: Literal["cli", "central", "ambos"] = "cli"
    remove_after: str = ""
    squad_managed: bool = False


class CommandDecl(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    summary: str
    roles: list[Literal["pm", "qa", "tl", "dev", "all"]] = Field(default_factory=lambda: ["all"])
    kind: Literal["read", "write", "agent", "gate"] = "read"
    target: str = ""  # atalho: o comando de papel chama este comando interno
    flag: str = ""


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    version: str = "1.0.0"
    title: str
    requires: list[str] = Field(default_factory=list)
    optional: list[str] = Field(default_factory=list)
    ports_used: list[str] = Field(default_factory=list)
    ports_provided: list[str] = Field(default_factory=list)
    flags: list[FlagDecl] = Field(default_factory=list)
    commands: list[CommandDecl] = Field(default_factory=list)
    cli: str = ""  # `pacote:função` que registra os comandos próprios
    subscribe: str = ""  # `pacote:função` que recebe os eventos
    events: dict[str, list[str]] = Field(default_factory=dict)
    gates: list[str] = Field(default_factory=list)
    resources: dict[str, str] = Field(default_factory=dict)
    data_class_max: str = "restricted"


def parse(data: dict[str, Any], source: str) -> Manifest:
    try:
        return Manifest.model_validate(data)
    except ValueError as exc:
        raise NexoError(f"manifesto inválido em {source}: {exc}") from exc


def validate(manifests: list[Manifest]) -> None:
    """Regras do conjunto: ids e comandos únicos, dependências existentes e sem ciclo, nada do piso com flag."""
    ids = [m.id for m in manifests]
    repeated = sorted({i for i in ids if ids.count(i) > 1})
    if repeated:
        raise NexoError(f"módulo repetido: {', '.join(repeated)}")
    known = set(ids)
    seen_commands: dict[str, str] = {}
    seen_flags: dict[str, str] = {}
    for m in manifests:
        missing = [r for r in m.requires if r not in known]
        if missing:
            raise NexoError(f"{m.id} exige módulo inexistente: {', '.join(missing)}")
        for c in m.commands:
            if c.path in seen_commands:
                raise NexoError(f"comando repetido: {c.path} ({seen_commands[c.path]} e {m.id})")
            seen_commands[c.path] = m.id
        for f in m.flags:
            if f.key in FLOOR or f.key.split(".")[-1] in FLOOR:
                raise NexoError(f"{m.id}: {f.key} faz parte do piso e não pode ter flag")
            if f.key in seen_flags:
                raise NexoError(f"flag repetida: {f.key} ({seen_flags[f.key]} e {m.id})")
            seen_flags[f.key] = m.id
    by_id = {m.id: m for m in manifests}

    def visit(node: str, stack: tuple[str, ...]) -> None:
        if node in stack:
            raise NexoError("dependência circular entre módulos: " + " → ".join((*stack, node)))
        for dep in by_id[node].requires:
            visit(dep, (*stack, node))

    for m in manifests:
        visit(m.id, ())


def _read(path: Path) -> Manifest:
    data = YAML(typ="safe", pure=True).load(path.read_text(encoding="utf-8"))
    return parse(data or {}, str(path))


def _external() -> list[Manifest]:
    out = []
    for ep in metadata.entry_points(group="nexo.modules"):
        loaded = ep.load()
        out.append(parse(loaded() if callable(loaded) else loaded, f"entry point {ep.name}"))
    return out


@functools.lru_cache(maxsize=1)
def builtin() -> tuple[Manifest, ...]:
    files = sorted(BUILTIN_ROOT.glob("modules/*/module.yaml")) + sorted(BUILTIN_ROOT.glob("central/module.yaml"))
    manifests = [_read(p) for p in files]
    validate(manifests)
    return tuple(manifests)


def load(allowed_external: frozenset[str] = frozenset()) -> list[Manifest]:
    """Módulos de fábrica + externos aprovados pela org. Externo fora da lista é ignorado."""
    manifests = list(builtin())
    if allowed_external:
        manifests += [m for m in _external() if m.id in allowed_external]
        validate(manifests)
    return manifests


def module_of_flag(key: str) -> Manifest | None:
    return next((m for m in builtin() if any(f.key == key for f in m.flags)), None)


def dependents(module_id: str) -> list[str]:
    return [m.id for m in builtin() if module_id in m.requires]
