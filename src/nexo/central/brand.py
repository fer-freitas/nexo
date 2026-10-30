"""Marca do dashboard: `resources/brand.yaml` com a sobrescrita de `NX_BRAND` (caminho de um brand.yaml da org)."""

from __future__ import annotations

import functools
import logging
import os
from importlib import resources
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from nexo.kernel import brand as brand_domain
from nexo.kernel.brand import Brand
from nexo.kernel.errors import NexoError

log = logging.getLogger("nexo.collector")


def _yaml(text: str) -> dict[str, Any]:
    data = YAML(typ="safe").load(text)
    return data if isinstance(data, dict) else {}


@functools.cache
def _load(path: str) -> Brand:
    default = _yaml(resources.files("nexo.resources").joinpath("brand.yaml").read_text(encoding="utf-8"))
    if not path:
        return brand_domain.merge(default)
    try:
        return brand_domain.merge(default, _yaml(Path(path).read_text(encoding="utf-8")), path)
    except (OSError, ValueError, NexoError) as exc:  # marca inválida não derruba o dashboard
        log.warning("marca ignorada (%s): %s", path, getattr(exc, "message", exc))
        return brand_domain.merge(default)


def current() -> Brand:
    return _load(os.environ.get("NX_BRAND", ""))
