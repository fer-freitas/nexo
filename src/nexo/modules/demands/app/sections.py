"""Seções do refinamento, cada uma com um dono e um hash: invalidar só o que mudou.

Funcional e técnica ficam na feature; testes e implementação, na história. Mudanças aceitas por proposta ficam em
`changes/`, entram no hash da seção e seguem com a spec congelada para o agente.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.roles import part_digests
from nexo.kernel.runtime import yamlfile
from nexo.kernel.workspace import specs
from nexo.modules.demands.app import core
from nexo.modules.demands.domain import model as m


def _plain(data: Any) -> Any:
    """Fim de linha e BOM não mudam o conteúdo: sem isso, Windows e macOS teriam hashes diferentes."""
    if isinstance(data, str):
        return data.replace("\r\n", "\n").removeprefix("\ufeff")
    if isinstance(data, dict):
        return {k: _plain(v) for k, v in data.items()}
    if isinstance(data, list | tuple):
        return [_plain(v) for v in data]
    return data


def _sha(data: Any) -> str:
    text = json.dumps(_plain(data), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def changes_path(ctx: AppContext, section: str, demand: str) -> Path:
    return core.store(ctx).path("changes", demand, f"{section}.yaml")


def accepted_changes(ctx: AppContext, section: str, demand: str) -> list[dict[str, Any]]:
    path = changes_path(ctx, section, demand)
    return list(yamlfile.load(path).get("changes") or []) if path.is_file() else []


def add_change(ctx: AppContext, section: str, demand: str, change: dict[str, Any]) -> None:
    path = changes_path(ctx, section, demand)
    current = accepted_changes(ctx, section, demand)
    yamlfile.dump(path, {"changes": [*current, change]})


def _feature_of(d: m.Demand, demands: dict[str, m.Demand]) -> m.Demand | None:
    if d.type == "feature":
        return d
    node = demands.get(d.parent)
    while node is not None and node.type != "feature":
        node = demands.get(node.parent)
    return node


def part_digest(ctx: AppContext, d: m.Demand, part: str) -> str:
    """Hash atual da parte. Normaliza o fim de linha, para ser igual no Windows e no macOS."""
    demands = core.load(ctx, sync=False)
    root = core.root_of(ctx)
    if part == "testes":
        path = core.store(ctx).path("sections", d.id, "testes.yaml")
        return _sha({"plan": yamlfile.load(path) if path.is_file() else {}, "c": accepted_changes(ctx, "testes", d.id)})
    if part == "plano":
        tasks = sorted((x.id, x.title) for x in demands.values() if x.parent == d.id and x.type == "task")
        return _sha({"tasks": tasks, "c": accepted_changes(ctx, "implementacao", d.id)})
    feature = _feature_of(d, demands)
    if feature is None:
        return ""
    folder = specs.feature_dir(root, feature.id)
    digests = part_digests(specs.load_spec(folder), None)
    section = "funcional" if part == "requisitos" else "tecnica"
    return _sha({"base": digests.get(part, ""), "c": accepted_changes(ctx, section, feature.id)})


def snapshot(ctx: AppContext, story: str) -> dict[str, Any]:
    """O que foi aprovado para a história: hashes e mudanças aceitas. É o conteúdo da spec congelada."""
    d, demands = core.get(ctx, story, sync=False)
    feature = _feature_of(d, demands)
    fid = feature.id if feature else ""
    return {
        "story": d.id,
        "feature": fid,
        "digests": {p: part_digest(ctx, d, p) for p in ("requisitos", "design", "testes", "plano")},
        "changes": {
            "funcional": accepted_changes(ctx, "funcional", fid),
            "tecnica": accepted_changes(ctx, "tecnica", fid),
            "testes": accepted_changes(ctx, "testes", d.id),
            "implementacao": accepted_changes(ctx, "implementacao", d.id),
        },
        "provisional": [p.id for p in d.open_premises],
    }
