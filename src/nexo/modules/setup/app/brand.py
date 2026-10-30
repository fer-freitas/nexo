"""Marca em uso no console: o padrão embutido com a sobrescrita da org por cima.

Ordem: `NX_BRAND` (caminho de um brand.yaml) → `brand.yaml` do cérebro da org → padrão embutido. A sobrescrita
só traz o que muda; o resto herda do padrão (ver `resources/brand.yaml`).
"""

from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from nexo.kernel import brand as brand_domain
from nexo.kernel.brand import Brand
from nexo.kernel.errors import NexoError
from nexo.kernel.runtime import resources

FILE = "brand.yaml"


def _parse(text: str, source: str) -> dict[str, Any]:
    try:
        data = YAML(typ="safe").load(text)
    except Exception as exc:
        raise NexoError(f"{source}: YAML inválido ({exc})") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise NexoError(f"{source}: esperado um mapeamento YAML")
    return data


def override_path(brain_dir: Path | None = None) -> Path | None:
    env = os.environ.get("NX_BRAND")
    if env:
        return Path(env).expanduser()
    if brain_dir is not None and (brain_dir / FILE).is_file():
        return brain_dir / FILE
    return None


@functools.cache
def default() -> Brand:
    return brand_domain.merge(_parse(resources.read_text(FILE), FILE))


def load(brain_dir: Path | None = None) -> Brand:
    path = override_path(brain_dir)
    if path is None:
        return default()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise NexoError(f"não consegui ler a marca em {path}: {exc}") from exc
    return brand_domain.merge(_parse(resources.read_text(FILE), FILE), _parse(text, str(path)), str(path))
