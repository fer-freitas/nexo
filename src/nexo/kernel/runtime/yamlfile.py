"""Leitura/escrita de YAML preservando comentários."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

# o mesmo arquivo é lido dezenas de vezes por execução (config, backlog, tarefas). Ler os bytes é barato;
# interpretar o YAML não. O cache é pela impressão digital do conteúdo, então nunca devolve um dado velho.
_PARSED: dict[str, Any] = {}
_PARSED_MAX = 512


def _yaml() -> YAML:
    y = YAML(typ="rt")
    y.default_flow_style = False
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    raw = path.read_bytes()
    key = hashlib.blake2b(raw, digest_size=16).hexdigest()
    cached = _PARSED.get(key)
    if cached is None:
        data = _yaml().load(raw.decode("utf-8"))
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ValueError(f"{path}: esperado um mapeamento YAML")
        cached = _plain(data)
        if len(_PARSED) >= _PARSED_MAX:
            _PARSED.clear()
        _PARSED[key] = cached
    return copy.deepcopy(cached)


def dump(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        _yaml().dump(data, fh)
    tmp.replace(path)


def parse_scalar(raw: str) -> Any:
    value = YAML(typ="safe").load(raw)
    return raw if value is None and raw.strip() not in {"null", "~", ""} else value


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value
