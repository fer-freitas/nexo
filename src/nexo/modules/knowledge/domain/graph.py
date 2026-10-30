"""Índice em grafo do código e do cérebro.

O Git continua a fonte oficial; o grafo é derivado dele (e da versão do cérebro) e refeito quando algum dos dois
muda. Nós: arquivos, testes, histórias, critérios, tarefas, incidentes, lições e ADRs. Arestas:

- `imports` arquivo → arquivo (resolvido a partir dos imports de Python, JS/TS e C#);
- `tests` teste → arquivo (o teste importa o arquivo); `verifies` teste → história/critério (cita o id);
- `touches` tarefa → arquivo; `part_of` tarefa → história; `covers` história → critério;
- `affects` incidente → arquivo; `mentions` lição/ADR → arquivo (cita o caminho ou o nome do arquivo).

Serve para análise de impacto (quem depende do que mudou e o que verifica isso) e para mandar ao agente só o que
está ligado à tarefa. Tudo determinístico, sem IA.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
ID_IN_TEXT = re.compile(r"\b(US-\d+-\d+)\b")
AC_IN_TEXT = re.compile(r"(?<![A-Za-z0-9])AC[-_]?(\d+)(?!\d)")


@dataclass
class Graph:
    nodes: dict[str, dict[str, str]] = field(default_factory=dict)
    edges: set[tuple[str, str, str]] = field(default_factory=set)  # (origem, relação, destino)

    def add(self, node: str, kind: str, label: str = "") -> str:
        self.nodes.setdefault(node, {"kind": kind, "label": label or node.split(":", 1)[-1]})
        return node

    def link(self, src: str, rel: str, dst: str) -> None:
        if src != dst and src in self.nodes and dst in self.nodes:
            self.edges.add((src, rel, dst))

    def out(self, node: str, rel: str | None = None) -> set[str]:
        return {d for s, r, d in self.edges if s == node and (rel is None or r == rel)}

    def into(self, node: str, rel: str | None = None) -> set[str]:
        return {s for s, r, d in self.edges if d == node and (rel is None or r == rel)}

    def to_json(self) -> dict[str, Any]:
        return {"nodes": self.nodes, "edges": sorted([list(e) for e in self.edges])}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Graph:
        return cls(dict(data.get("nodes") or {}), {(str(a), str(b), str(c)) for a, b, c in data.get("edges") or []})

    def stats(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for n in self.nodes.values():
            out[n["kind"]] = out.get(n["kind"], 0) + 1
        out["edges"] = len(self.edges)
        return out


def file_id(path: str) -> str:
    return f"file:{path}"


def resolve_import(importer: str, name: str, files: set[str], by_suffix: dict[str, list[str]]) -> list[str]:
    """Arquivos do repositório que um import aponta (vazio para bibliotecas externas)."""
    if name.startswith("."):  # JS/TS relativo
        base = PurePosixPath(importer).parent
        parts: list[str] = []
        for piece in (base / name).parts:
            if piece == "..":
                if parts:
                    parts.pop()
            elif piece != ".":
                parts.append(piece)
        target = "/".join(parts)
        candidates = [target, *(target + e for e in JS_EXTS), *(f"{target}/index{e}" for e in JS_EXTS)]
        return [c for c in candidates if c in files][:1]
    if importer.endswith(".py"):
        dotted = name.replace(".", "/")
        return (by_suffix.get(dotted + ".py") or by_suffix.get(dotted + "/__init__.py") or [])[:1]
    if importer.endswith(".cs"):  # namespace → arquivos .cs da pasta de mesmo caminho
        folder = name.replace(".", "/")
        return sorted(f for f in files if f.endswith(".cs") and str(PurePosixPath(f).parent).endswith(folder))
    return []


def _suffix_index(files: Iterable[str]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for f in files:
        parts = f.split("/")
        for i in range(len(parts)):
            index.setdefault("/".join(parts[i:]), []).append(f)
    return index


def build(
    code: dict[str, dict[str, Any]],
    backlogs: list[dict[str, Any]] | None = None,
    incidents: list[dict[str, Any]] | None = None,
    docs: list[tuple[str, str, str]] | None = None,  # (tipo lesson|adr, caminho, texto)
    test_texts: dict[str, str] | None = None,
) -> Graph:
    """Monta o grafo. `code`: caminho → {"imports": [...], "is_test": bool}; `backlogs`: {"feature", "stories",
    "tasks"} como no backlog.yaml; `incidents`: {"id", "files"}; `test_texts`: conteúdo dos testes (ids citados)."""
    backlogs, incidents, docs = backlogs or [], incidents or [], docs or []
    g = Graph()
    files = set(code)
    for path, info in code.items():
        g.add(file_id(path), "test" if info.get("is_test") else "file")
    index = _suffix_index(files)
    for path, info in code.items():
        for name in info.get("imports") or []:
            for target in resolve_import(path, str(name), files, index):
                rel = "tests" if info.get("is_test") and not code[target].get("is_test") else "imports"
                g.link(file_id(path), rel, file_id(target))
    story_acs: dict[str, set[str]] = {}
    for bl in backlogs:
        feature = str(bl.get("feature") or "")
        for st in bl.get("stories") or []:
            sid = g.add(f"story:{st['id']}", "story", f"{st['id']} {st.get('title', '')}".strip())
            story_acs[str(st["id"])] = {str(a) for a in st.get("ac_refs") or []}
            for ac in st.get("ac_refs") or []:
                g.link(sid, "covers", g.add(f"ac:{feature}/{ac}", "ac", f"{feature} {ac}"))
        for t in bl.get("tasks") or []:
            tid = g.add(f"task:{t['id']}", "task", f"{t['id']} {t.get('title', '')}".strip())
            g.link(tid, "part_of", f"story:{t.get('story')}")
            for f in t.get("files_hint") or []:
                if f in files:
                    g.link(tid, "touches", file_id(f))
    feature_of = {str(st["id"]): str(bl.get("feature") or "") for bl in backlogs for st in bl.get("stories") or []}
    for path, text in (test_texts or {}).items():
        if file_id(path) not in g.nodes:
            continue
        for story in set(ID_IN_TEXT.findall(text)):
            if f"story:{story}" not in g.nodes:
                continue
            g.link(file_id(path), "verifies", f"story:{story}")
            for n in {f"AC-{int(m)}" for m in AC_IN_TEXT.findall(text)} & story_acs.get(story, set()):
                g.link(file_id(path), "verifies", f"ac:{feature_of[story]}/{n}")
    for inc in incidents:
        iid = g.add(f"incident:{inc['id']}", "incident", f"{inc['id']} {inc.get('service', '')}".strip())
        for f in inc.get("files") or []:
            g.link(iid, "affects", file_id(str(f)))
    names: dict[str, list[str]] = {}
    for f in files:
        base = PurePosixPath(f).name
        if len(base) >= 8 and "." in base:
            names.setdefault(base, []).append(f)
    for kind, path, text in docs:
        did = g.add(f"{kind}:{path}", kind)
        for f in files:
            if f in text:
                g.link(did, "mentions", file_id(f))
        for base, owners in names.items():
            if len(owners) == 1 and re.search(rf"(?<![\w/.-]){re.escape(base)}(?![\w-])", text):
                g.link(did, "mentions", file_id(owners[0]))
    return g


def impact(g: Graph, paths: list[str], depth: int = 3) -> dict[str, list[str]]:
    """O que pode quebrar se esses arquivos mudarem: dependentes (até `depth` níveis), testes, histórias, critérios,
    incidentes e decisões ligados."""
    start = {file_id(p) for p in paths if file_id(p) in g.nodes}
    dependents: set[str] = set()
    frontier = set(start)
    for _ in range(depth):
        nxt = {s for n in frontier for s in g.into(n, "imports")} - dependents - start
        dependents |= nxt
        frontier = nxt
    touched = start | dependents
    tests = {s for n in touched for s in g.into(n, "tests")} | {n for n in touched if g.nodes[n]["kind"] == "test"}
    tasks = {s for n in touched for s in g.into(n, "touches")}
    stories = {d for t in tasks for d in g.out(t, "part_of")} | {d for t in tests for d in g.out(t, "verifies")}
    acs = {d for s in stories if s.startswith("story:") for d in g.out(s, "covers")}
    return {
        "changed": sorted(_label(g, n) for n in start),
        "dependents": sorted(_label(g, n) for n in dependents if g.nodes[n]["kind"] == "file"),
        "tests": sorted(_label(g, n) for n in tests),
        "stories": sorted(_label(g, n) for n in stories if n.startswith("story:")),
        "criteria": sorted(_label(g, n) for n in acs),
        "incidents": sorted(_label(g, s) for n in touched for s in g.into(n, "affects")),
        "decisions": sorted(
            {
                _label(g, s)
                for n in touched
                for s in g.into(n, "mentions")
                if s.startswith(("adr:", "lesson:", "doc:", "knowledge:"))
            }
        ),
    }


def related(g: Graph, paths: list[str]) -> dict[str, list[str]]:
    """Contexto de uma tarefa: vizinhos diretos dos arquivos previstos (o que importam e quem os usa), testes,
    decisões, lições e incidentes que citam esses arquivos."""
    start = {file_id(p) for p in paths if file_id(p) in g.nodes}
    neighbors = {d for n in start for d in g.out(n, "imports")} | {s for n in start for s in g.into(n, "imports")}
    tests = {s for n in start for s in g.into(n, "tests")}
    docs = {s for n in start for s in g.into(n, "mentions")}
    return {
        "neighbors": sorted(_label(g, n) for n in neighbors - start),
        "tests": sorted(_label(g, n) for n in tests),
        "decisions": sorted(_label(g, n) for n in docs if n.startswith("adr:")),
        "lessons": sorted(_label(g, n) for n in docs if n.startswith("lesson:")),
        "docs": sorted(_label(g, n) for n in docs if n.startswith(("doc:", "knowledge:"))),
        "incidents": sorted(_label(g, s) for n in start for s in g.into(n, "affects")),
    }


def _label(g: Graph, node: str) -> str:
    kind = g.nodes[node]["kind"]
    return (
        node.split(":", 1)[1]
        if kind in ("file", "test", "adr", "lesson", "doc", "knowledge")
        else g.nodes[node]["label"]
    )
