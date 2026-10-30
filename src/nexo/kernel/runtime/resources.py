"""Recursos embutidos (padrões) com sobrescrita pelo nexo-brain."""

from __future__ import annotations

import copy
import functools
from importlib import resources
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


@functools.cache
def _embedded(rel: str) -> str:  # recursos embutidos não mudam durante a execução
    node = resources.files("nexo.resources")
    for part in rel.split("/"):
        node = node.joinpath(part)
    return node.read_text(encoding="utf-8")


def read_text(rel: str, brain_dir: Path | None = None) -> str:
    if brain_dir is not None:
        override = brain_dir / rel
        if override.is_file():
            return override.read_text(encoding="utf-8")
    return _embedded(rel)


def load_yaml(rel: str, brain_dir: Path | None = None) -> dict[str, Any]:
    return copy.deepcopy(_parse(rel, read_text(rel, brain_dir)))


@functools.lru_cache(maxsize=256)
def _parse(rel: str, text: str) -> dict[str, Any]:
    """interpreta cada texto uma vez (a chave inclui o conteúdo, então sobrescrita do cérebro vale na hora)."""
    data = YAML(typ="safe").load(text)
    if not isinstance(data, dict):
        raise ValueError(f"{rel}: esperado mapeamento YAML")
    return data


def embedded_dir(rel: str) -> Path:
    """Diretório real de um recurso embutido (instalação em disco ou PyInstaller)."""
    return Path(str(resources.files("nexo.resources").joinpath(rel)))
