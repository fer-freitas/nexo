"""Modernização de legado em ondas (F27): roteiro, módulos afetados, ordem por dependência e progresso.

Um roteiro (`playbooks/<nome>.yaml`, embutido ou do cérebro) diz o que procurar (`detect`, regex), em que arquivos
(`include`, globs), o que fazer (`instructions`, para o agente) e como provar que terminou (nenhuma ocorrência do
padrão antigo). O plano é determinístico: arquivos afetados → módulos (pasta) → ordem pelo grafo de imports (quem é
usado vem antes) → ondas. A IA só entra na execução de cada tarefa.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

MAX_FILES_PER_TASK = 8


@dataclass(frozen=True)
class Playbook:
    name: str
    title: str
    detect: str
    include: tuple[str, ...] = ("**/*",)
    instructions: str = ""
    points: int = 3

    @property
    def pattern(self) -> re.Pattern[str]:
        return re.compile(self.detect, re.M)

    @staticmethod
    def from_mapping(name: str, data: dict[str, Any]) -> Playbook:
        detect = str(data.get("detect") or "")
        if not detect:
            raise ValueError(f"roteiro {name}: 'detect' é obrigatório")
        re.compile(detect)  # valida a expressão
        return Playbook(
            name=name,
            title=str(data.get("title") or name),
            detect=detect,
            include=tuple(str(g) for g in data.get("include") or ("**/*",)),
            instructions=str(data.get("instructions") or ""),
            points=int(data.get("points") or 3),
        )


def matches_include(path: str, include: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(path, g) or fnmatch.fnmatch(PurePosixPath(path).name, g) for g in include)


def occurrences(text: str, pattern: re.Pattern[str]) -> int:
    return sum(1 for _ in pattern.finditer(text))


def module_of(path: str) -> str:
    parent = str(PurePosixPath(path).parent)
    return "." if parent in ("", ".") else parent


@dataclass
class Module:
    name: str
    files: list[str] = field(default_factory=list)
    count: int = 0  # ocorrências do padrão antigo
    depends_on: set[str] = field(default_factory=set)


def modules(affected: dict[str, int], imports: dict[str, list[str]]) -> dict[str, Module]:
    """Agrupa arquivos afetados por pasta e liga módulos pelos imports (módulo importado precisa migrar antes)."""
    out: dict[str, Module] = {}
    for path, count in sorted(affected.items()):
        m = out.setdefault(module_of(path), Module(module_of(path)))
        m.files.append(path)
        m.count += count
    tokens = {name: {PurePosixPath(name).name, name.replace("/", ".")} for name in out if name != "."}
    for path in affected:
        mine = module_of(path)
        for imp in imports.get(path, []):
            for other, names in tokens.items():
                if other != mine and any(
                    imp == n or imp.startswith(n + ".") or imp.endswith("." + n) or imp == n.split(".")[-1]
                    for n in names
                ):
                    out[mine].depends_on.add(other)
    return out


def waves(mods: dict[str, Module]) -> list[list[str]]:
    """Ordem topológica em ondas; ciclo entre módulos cai numa onda só (migram juntos)."""
    remaining = {k: set(v.depends_on) & set(mods) for k, v in mods.items()}
    out: list[list[str]] = []
    while remaining:
        ready = sorted(k for k, deps in remaining.items() if not deps)
        if not ready:  # ciclo: o resto vai junto
            ready = sorted(remaining)
        out.append(ready)
        for k in ready:
            remaining.pop(k)
        for deps in remaining.values():
            deps.difference_update(ready)
    return out


def chunks(files: list[str], size: int = MAX_FILES_PER_TASK) -> list[list[str]]:
    return [files[i : i + size] for i in range(0, len(files), size)] or [[]]


def progress(before: int, now: int) -> float:
    if before <= 0:
        return 100.0
    return round(100.0 * max(0, before - now) / before, 1)
