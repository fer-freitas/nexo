"""Serviços entre módulos: o kernel e os módulos pedem pelo nome, sem importar quem implementa.

Quem oferece declara em `services` no próprio manifesto. Se o módulo não estiver presente, quem pede recebe um erro
claro em vez de uma falha de importação.
"""

from __future__ import annotations

import functools
import importlib
from types import ModuleType
from typing import Any

from nexo.kernel.errors import NexoError


@functools.lru_cache(maxsize=1)
def _table() -> dict[str, str]:
    from nexo.kernel import modules

    return {name: target for m in modules.builtin() for name, target in m.services.items()}


def load(name: str) -> ModuleType:
    target = _table().get(name)
    if target is None:
        raise NexoError(f"serviço indisponível: {name} (o módulo que o oferece não está presente)")
    return importlib.import_module(target)  # nosemgrep -- alvo do manifesto embutido


def available(name: str) -> bool:
    return name in _table()


def get(name: str, attr: str) -> Any:
    return getattr(load(name), attr)
