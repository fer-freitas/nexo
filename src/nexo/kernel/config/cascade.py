"""Configuração em cascata org → squad → projeto → pessoal com chaves bloqueadas.

Uma chave listada em `locked` numa camada não pode ser sobrescrita pelas camadas seguintes.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from nexo.kernel.errors import PolicyViolation

LEVELS = ("default", "org", "squad", "project", "personal")
LOCKED_KEY = "locked"


@dataclass(frozen=True)
class Layer:
    level: str
    data: dict[str, Any]
    locked: frozenset[str] = frozenset()
    source: str = ""

    @staticmethod
    def from_mapping(level: str, raw: dict[str, Any] | None, source: str = "") -> Layer:
        if level not in LEVELS:
            raise ValueError(f"nível desconhecido: {level}")
        data = copy.deepcopy(raw or {})
        locked_raw = data.pop(LOCKED_KEY, []) or []
        if not isinstance(locked_raw, list) or not all(isinstance(k, str) for k in locked_raw):
            raise ValueError(f"'{LOCKED_KEY}' deve ser uma lista de chaves em {source or level}")
        return Layer(level, data, frozenset(locked_raw), source)


@dataclass
class Resolved:
    values: dict[str, Any] = field(default_factory=dict)
    origin: dict[str, str] = field(default_factory=dict)
    locked_by: dict[str, str] = field(default_factory=dict)

    def get(self, key: str) -> Any:
        return get_path(self.values, key)


def flatten(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in data.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten(v, f"{key}."))  # dicionário vazio não é valor: não esconde as camadas de baixo
        else:
            out[key] = v
    return out


def get_path(data: dict[str, Any], key: str) -> Any:
    node: Any = data
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(key)
        node = node[part]
    return node


def set_path(data: dict[str, Any], key: str, value: Any) -> None:
    parts = key.split(".")
    node = data
    for part in parts[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            node[part] = nxt
        node = nxt
    node[parts[-1]] = value


def unset_path(data: dict[str, Any], key: str) -> bool:
    """Remove a chave e os pais que ficarem vazios (um `{}` na camada esconderia o valor das camadas de baixo)."""
    parts = key.split(".")
    chain: list[dict[str, Any]] = [data]
    node: Any = data
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
        chain.append(node)
    if not (isinstance(node, dict) and parts[-1] in node):
        return False
    del node[parts[-1]]
    for depth in range(len(parts) - 1, 0, -1):
        parent, child = chain[depth - 1], parts[depth - 1]
        if isinstance(parent.get(child), dict) and not parent[child]:
            del parent[child]
        else:
            break
    return True


def _is_covered(key: str, locked: str) -> bool:
    """Uma trava em `a.b` cobre `a.b` e `a.b.*`; trava em `a.b.c` também impede sobrescrever `a.b` inteiro."""
    return key == locked or key.startswith(f"{locked}.") or locked.startswith(f"{key}.")


def _lock_for(key: str, locks: dict[str, str]) -> str | None:
    for locked, level in locks.items():
        if _is_covered(key, locked):
            return level
    return None


def resolve(layers: list[Layer]) -> Resolved:
    ordered = sorted(layers, key=lambda layer: LEVELS.index(layer.level))
    result = Resolved()
    locks: dict[str, str] = {}
    for layer in ordered:
        for key, value in flatten(layer.data).items():
            owner = _lock_for(key, locks)
            if owner is not None and owner != layer.level:
                continue  # camada inferior tentando sobrescrever chave bloqueada: ignorada
            set_path(result.values, key, copy.deepcopy(value))
            result.origin[key] = layer.level
        for key in layer.locked:
            locks.setdefault(key, layer.level)
    result.locked_by = dict(locks)
    return result


def violations(layers: list[Layer]) -> list[tuple[str, str, str]]:
    """Lista (chave, nível que tentou sobrescrever, nível que bloqueou) — para diagnóstico."""
    ordered = sorted(layers, key=lambda layer: LEVELS.index(layer.level))
    locks: dict[str, str] = {}
    found: list[tuple[str, str, str]] = []
    for layer in ordered:
        for key in flatten(layer.data):
            owner = _lock_for(key, locks)
            if owner is not None and owner != layer.level:
                found.append((key, layer.level, owner))
        for key in layer.locked:
            locks.setdefault(key, layer.level)
    return found


def ensure_can_set(layers: list[Layer], key: str, target_level: str) -> None:
    if target_level not in LEVELS:
        raise ValueError(f"nível desconhecido: {target_level}")
    target_idx = LEVELS.index(target_level)
    for layer in layers:
        if LEVELS.index(layer.level) >= target_idx:
            continue
        for locked in layer.locked:
            if _is_covered(key, locked):
                raise PolicyViolation(f"a chave '{key}' está bloqueada pela camada '{layer.level}' ({locked})")
