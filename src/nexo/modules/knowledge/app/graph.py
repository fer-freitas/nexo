"""Índice em grafo do código e do cérebro: montagem, cache e consultas.

O grafo fica em `.nexo/map/graph.json` (fora do git) com a chave do que o originou: commit atual, versão do
cérebro e estado dos arquivos alterados. Mudou qualquer um, é refeito na próxima consulta.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.platform import flags
from nexo.kernel.runtime import yamlfile
from nexo.kernel.workspace import codemap
from nexo.modules.knowledge.app import brain
from nexo.modules.knowledge.domain import graph as graph_domain
from nexo.modules.planning.app import discover

DOC_GLOBS = ("spec/adr/*.md", "docs/adr/*.md", ".nexo/specs/*/adr/*.md")
MAX_FILE_BYTES = 400_000


def _root(ctx: AppContext) -> Path:
    if ctx.project_root is None:
        raise NexoError("rode dentro de um repositório com o Nexo (nexo init)")
    return ctx.project_root


def _key(ctx: AppContext, root: Path) -> str:
    head = codemap._git(root, "rev-parse", "HEAD").strip()
    status = codemap._git(root, "status", "--porcelain", "--untracked-files=all")
    changed = []
    for line in status.splitlines():
        rel = line[3:].strip().strip('"')
        path = root / rel
        if rel.startswith(".nexo/map/"):
            continue
        changed.append(f"{rel}:{path.stat().st_mtime_ns if path.is_file() else '-'}")
    version = str(brain.status(ctx).get("version") or "")
    return hashlib.sha256("\n".join([head, version, *sorted(changed)]).encode()).hexdigest()[:24]


def _path(root: Path) -> Path:
    return discover.map_dir(root) / "graph.json"


def _collect(ctx: AppContext, root: Path) -> graph_domain.Graph:
    from nexo.kernel.workspace import specs

    code: dict[str, dict[str, Any]] = {}
    test_texts: dict[str, str] = {}
    for rel in codemap.working_files(root):
        lang = codemap.LANGS.get(Path(rel).suffix)
        path = root / rel
        if lang is None or rel.startswith(".nexo/") or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        _symbols, imports = codemap.parse(lang, text)
        is_test = codemap.is_test(rel)
        code[rel] = {"imports": imports, "is_test": is_test}
        if is_test:
            test_texts[rel] = text
    backlogs, incidents = [], []
    for folder in sorted(specs.specs_root(root).glob("F-*")):
        backlog = yamlfile.load(folder / "backlog.yaml") if (folder / "backlog.yaml").is_file() else None
        if backlog:
            backlogs.append(dict(backlog))
        if (folder / "incident.yaml").is_file():
            incidents.append(dict(yamlfile.load(folder / "incident.yaml")))
    docs: list[tuple[str, str, str]] = []
    for pattern in DOC_GLOBS:
        for p in sorted(root.glob(pattern)):
            docs.append(("adr", p.relative_to(root).as_posix(), p.read_text(encoding="utf-8", errors="replace")))
    for p in sorted(root.glob("docs/**/*.md")):  # documentação viva do writer
        rel = p.relative_to(root).as_posix()
        if "/adr/" not in f"/{rel}":
            docs.append(("doc", rel, p.read_text(encoding="utf-8", errors="replace")))
    knowledge = ctx.brain_dir / "knowledge"
    if knowledge.is_dir():
        for p in sorted(knowledge.rglob("*.md")):
            docs.append(
                ("knowledge", p.relative_to(ctx.brain_dir).as_posix(), p.read_text(encoding="utf-8", errors="replace"))
            )
    lessons = ctx.brain_dir / "lessons"
    if lessons.is_dir():
        for p in sorted(lessons.rglob("*.md")):
            if p.name != "README.md":
                text = p.read_text(encoding="utf-8", errors="replace")
                docs.append(("lesson", p.relative_to(ctx.brain_dir).as_posix(), text))
    return graph_domain.build(code, backlogs, incidents, docs, test_texts)


def load(ctx: AppContext, force: bool = False) -> tuple[graph_domain.Graph, dict[str, Any]]:
    """Grafo atual (do cache se nada mudou; senão refeito a partir do Git e do cérebro)."""
    flags.require(ctx, "graph")
    root = _root(ctx)
    key = _key(ctx, root)
    path = _path(root)
    if not force and path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("key") == key:
            return graph_domain.Graph.from_json(data["graph"]), {"key": key, "rebuilt": False}
    g = _collect(ctx, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    discover._ensure_gitignore(root)
    path.write_text(json.dumps({"key": key, "graph": g.to_json()}, ensure_ascii=False), encoding="utf-8")
    ctx.audit("graph.built", {"key": key, **g.stats()})
    return g, {"key": key, "rebuilt": True}


def build(ctx: AppContext, force: bool = True) -> dict[str, Any]:
    g, meta = load(ctx, force)
    return {**meta, "stats": g.stats()}


def impact(ctx: AppContext, paths: list[str], depth: int = 3) -> dict[str, Any]:
    g, meta = load(ctx)
    root = _root(ctx)
    rels = [_rel(root, p) for p in paths]
    unknown = [p for p in rels if graph_domain.file_id(p) not in g.nodes]
    return {**graph_domain.impact(g, rels, depth), "unknown": unknown, "rebuilt": meta["rebuilt"]}


def related(ctx: AppContext, paths: list[str]) -> dict[str, list[str]]:
    g, _ = load(ctx)
    return graph_domain.related(g, paths)


def context_for(ctx: AppContext, paths: list[str], budget: int = 1500) -> str:
    """Texto curto para o prompt do agente: só caminhos ligados aos arquivos da tarefa (o agente lê se precisar)."""
    if not paths or not flags.enabled(ctx, "graph"):
        return ""
    try:
        rel = related(ctx, paths)
    except (NexoError, OSError, ValueError):
        return ""
    titles = {
        "neighbors": "Código ligado (importa ou é usado)",
        "tests": "Testes que cobrem",
        "decisions": "Decisões (ADRs)",
        "lessons": "Lições do cérebro",
        "docs": "Documentação e conhecimento",
        "incidents": "Incidentes que passaram por aqui",
    }
    lines: list[str] = []
    for key, title in titles.items():
        if rel.get(key):
            lines.append(f"{title}: " + ", ".join(rel[key][:12]))
    text = "\n".join(lines)
    return text[:budget]


def _rel(root: Path, p: str) -> str:
    path = Path(p)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return path.as_posix()
    return path.as_posix().removeprefix("./")
