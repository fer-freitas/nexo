"""O que mudou entre dois manifestos: diff para o PR, ligação não declarada e contrato incompatível."""

from __future__ import annotations

from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from nexo.modules.lineage.domain.model import normalize

DIRECTIONS = ("publishes", "consumes", "calls", "exposes")
VERB = {"publishes": "publica em", "consumes": "consome de", "calls": "chama", "exposes": "expõe"}


def links_of(manifest: dict[str, Any]) -> set[tuple[str, str, str]]:
    return {
        (str(comp.get("id", "")), direction, str(link.get("channel", "")))
        for comp in manifest.get("components") or []
        for direction in DIRECTIONS
        for link in comp.get(direction) or []
    }


def manifest_diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, list[str]]:
    before, after = links_of(old), links_of(new)

    def text(row: tuple[str, str, str]) -> str:
        return f"{row[0]} {VERB[row[1]]} {row[2]}"

    return {"added": [text(r) for r in sorted(after - before)], "removed": [text(r) for r in sorted(before - after)]}


def undeclared(manifest: dict[str, Any], declared: dict[str, Any] | None) -> list[str]:
    """Só vale para quem declara `.nexo/component.yaml`: o que o código faz e o arquivo não diz é desvio."""
    blocks = (declared or {}).get("components") or []
    if not blocks:
        return []
    known = {
        normalize(str(item.get("kind", "kafka")), str(item["name"]))[0]
        for block in blocks
        for direction in ("publishes", "consumes")
        for item in block.get(direction) or []
    }
    return sorted(
        f"{cid} {VERB[direction]} {channel}"
        for cid, direction, channel in links_of(manifest)
        if direction in ("publishes", "consumes") and channel not in known
    )


def _contract(text: str) -> dict[str, Any]:
    try:
        data = YAML(typ="safe", pure=True).load(text) or {}
    except YAMLError:
        return {}
    return data if isinstance(data, dict) and "asyncapi" in data else {}


def _major(contract: dict[str, Any]) -> str:
    return str((contract.get("info") or {}).get("version") or "").split(".")[0]


def _messages(channel: Any) -> set[str]:
    if not isinstance(channel, dict):
        return set()
    names = set(channel.get("messages") or {})
    for side in ("publish", "subscribe"):
        message = (channel.get(side) or {}).get("message") or {}
        if isinstance(message, dict):
            names.add(str(message.get("name") or message.get("$ref", "")).rsplit("/", 1)[-1])
    return names - {""}


def contract_breaks(old_tree: dict[str, str], new_tree: dict[str, str]) -> list[str]:
    """Regras simples e previsíveis: arquivo, canal ou mensagem que some, ou versão maior que muda."""
    out: list[str] = []
    for path, text in sorted(old_tree.items()):
        old = _contract(text)
        if not old:
            continue
        if path not in new_tree:
            out.append(f"{path}: contrato removido")
            continue
        new = _contract(new_tree[path])
        if not new:
            out.append(f"{path}: contrato ilegível depois da mudança")
            continue
        if _major(old) != _major(new):
            out.append(f"{path}: versão maior mudou de {_major(old) or '?'} para {_major(new) or '?'}")
        before, after = dict(old.get("channels") or {}), dict(new.get("channels") or {})
        for name, channel in sorted(before.items()):
            if name not in after:
                out.append(f"{path}: canal {name} removido")
                continue
            for message in sorted(_messages(channel) - _messages(after[name])):
                out.append(f"{path}: mensagem {message} removida de {name}")
    return out
