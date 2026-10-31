"""`validate`, `confirm`, `discover` e o que a linhagem diz sobre um branch: diff no PR, desvio e portão."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.runtime import yamlfile
from nexo.kernel.workspace.specs import project_settings
from nexo.modules.lineage.adapters import aws, gitread
from nexo.modules.lineage.app import lineage
from nexo.modules.lineage.domain import changes as model
from nexo.modules.lineage.domain import discovery
from nexo.modules.lineage.domain import model as m

COMPONENT_FILE = ".nexo/component.yaml"


def validate(ctx: AppContext) -> dict[str, Any]:
    """Confere todos os manifestos desta máquina, como cada leitor faz antes de usar."""
    graph = lineage.consolidate(ctx)
    return {"components": len(graph.components), "invalid": graph.invalid}


def _root(ctx: AppContext) -> Path:
    if ctx.project_root is None:
        raise NexoError("rode dentro de um repositório do projeto")
    return ctx.project_root


def unresolved(ctx: AppContext) -> list[dict[str, Any]]:
    return [dict(u) for u in lineage.scan(ctx).get("unresolved") or []]


def confirm(ctx: AppContext, where: str, name: str, direction: str, kind: str, component: str = "") -> dict[str, Any]:
    """Uma pessoa resolve o que o scanner não conseguiu. Fica no repositório, então passa por PR como o resto."""
    if direction not in ("publishes", "consumes"):
        raise NexoError("direção: publishes ou consumes")
    root = _root(ctx)
    known = {f"{u.get('file')}:{u.get('line')}" for u in unresolved(ctx)}
    if where not in known:
        raise NexoError(f"{where} não está entre os não resolvidos; rode `nexo lineage confirm` para ver a lista")
    path = root / COMPONENT_FILE
    data = dict(yamlfile.load(path)) if path.is_file() else {}
    wanted = component or lineage.repo_slug(root)
    blocks = list(data.get("components") or [])
    block = next((b for b in blocks if b.get("name") == wanted), None)
    if block is None:
        block = {"name": wanted}
        blocks.append(block)
    items = list(block.get(direction) or [])
    if not any(i.get("name") == name for i in items):
        items.append({"kind": kind, "name": name})
    block[direction] = items
    data["components"] = blocks
    data["confirmed"] = sorted({*(data.get("confirmed") or []), where})
    path.parent.mkdir(parents=True, exist_ok=True)
    yamlfile.dump(path, data)
    ctx.audit("lineage.confirmed", {"where": where, "channel": name, "direction": direction})
    return {"file": COMPONENT_FILE, "component": wanted, "direction": direction, "channel": name}


def _save_discovery(ctx: AppContext, data: dict[str, Any], source: str) -> dict[str, Any]:
    from nexo.kernel import services

    root = _root(ctx)
    problems = m.validate_manifest(data, str(data["team"]))
    if problems:
        raise NexoError("descoberta inválida: " + "; ".join(problems))
    settings = project_settings(root)
    store = services.get("demands.store", "Store")(settings.team_dir(), settings.project, settings.shared)
    store.pull()
    path = settings.team_dir() / "lineage" / "discovery" / f"{source}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    yamlfile.dump(path, {**data, "generated_at": datetime.now(UTC).isoformat(timespec="seconds")})
    sent = store.push(f"nexo: descoberta {source} da linhagem")
    ctx.audit("lineage.discovered", {"source": source, "components": len(data["components"])})
    return {"source": source, "components": len(data["components"]), **sent}


def discover_aws(ctx: AppContext) -> dict[str, Any]:
    found = aws.inventory()
    team = project_settings(_root(ctx)).team_name or "local"
    return _save_discovery(
        ctx, discovery.from_aws(team, found["subscriptions"], found["mappings"], found["services"]), "aws"
    )


def discover_kafka(ctx: AppContext, source: Path) -> dict[str, Any]:
    """Recebe o que o time de Kafka exporta: `{"groups": {"grupo": ["tópico"]}, "acls": [...], "schemas": [...]}`,
    ou só `{"grupo": ["tópico"]}`. A credencial do cluster não entra no Nexo."""
    if not source.is_file():
        raise NexoError(f"{source} não existe")
    data = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise NexoError('formato esperado: {"groups": {"grupo": ["tópico"]}, "acls": [], "schemas": []}')
    full = isinstance(data.get("groups"), dict) or "acls" in data or "schemas" in data
    groups = dict(data.get("groups") or {}) if full else data
    acls = list(data.get("acls") or []) if full else []
    schemas = list(data.get("schemas") or []) if full else []
    team = project_settings(_root(ctx)).team_name or "local"
    return _save_discovery(ctx, discovery.from_kafka(team, groups, acls, schemas), "kafka")


def branch_report(ctx: AppContext, root: Path, head: str = "HEAD") -> dict[str, Any]:
    base = gitread.default_ref(root)
    old_tree, new_tree = gitread.tree(root, base), gitread.tree(root, head)
    new = lineage.scan(ctx, root, head)
    declared_text = new_tree.get(COMPONENT_FILE)
    declared = None
    if declared_text:
        from ruamel.yaml import YAML

        declared = YAML(typ="safe", pure=True).load(declared_text) or None
    return {
        "diff": model.manifest_diff(lineage.scan(ctx, root, base), new),
        "undeclared": model.undeclared(new, declared),
        "breaks": model.contract_breaks(old_tree, new_tree),
    }


def pr_lines(ctx: AppContext, root: Path, head: str = "HEAD") -> tuple[list[str], list[str]]:
    """(linhas para o corpo do PR, motivos que seguram o merge automático). Falha aqui nunca segura a entrega."""
    from nexo.kernel.platform import flags

    if not flags.enabled(ctx, "lineage.scan"):
        return [], []
    try:
        report = branch_report(ctx, root, head)
    except (NexoError, OSError) as exc:
        ctx.audit("lineage.report_failed", {"error": str(exc)[:200]})
        return [], []
    lines = [f"- Linhagem: agora {item}" for item in report["diff"]["added"]]
    lines += [f"- Linhagem: deixou de {item}" for item in report["diff"]["removed"]]
    lines += [f"- Linhagem, desvio: {item} não está declarado em `{COMPONENT_FILE}`" for item in report["undeclared"]]
    breaks = [f"contrato incompatível, decisão humana: {item}" for item in report["breaks"]]
    lines += [f"- ATENÇÃO: {item}" for item in breaks]
    for item in report["undeclared"]:
        ctx.audit("lineage.deviation", {"link": item})
    return lines, breaks if flags.enabled(ctx, "lineage.gate") else []


def neighbor_names(ctx: AppContext, component: str) -> dict[str, list[str]]:
    """Para o agente: só nomes, sem caminho, arquivo ou evidência."""
    graph = lineage.consolidate(ctx)
    found = m.neighbors(graph, lineage._resolve(graph, component))
    return {side: sorted({str(row.get("component", "")) for row in rows}) for side, rows in found.items()}
