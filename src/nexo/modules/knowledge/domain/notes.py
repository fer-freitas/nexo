"""Cada arquivo do cérebro como um nó: frontmatter, links tipados e as regras que deixam o grafo confiável.

Tudo determinístico. `nodes_and_edges` gera o índice que a busca usa hoje e que o grafo vai usar depois; o Git é a
fonte, o índice é sempre derivado. O nome do arquivo é o ID porque o Obsidian resolve `[[link]]` pelo nome.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

RELATIONS = frozenset(
    {"links_to", "derived_from", "supersedes", "applies_to", "depends_on", "contradicts", "part_of", "mentions"}
)
TYPES = frozenset(
    {"lesson", "decision", "knowledge", "runbook", "prompt", "standard", "skill", "recipe", "template", "note", "index"}
)
STATUSES = frozenset({"draft", "active", "deprecated"})
DATA_RANK = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
FRONT = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.S)
WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
GENERATED = "_generated"


@dataclass
class Note:
    path: str
    meta: dict[str, Any] = field(default_factory=dict)
    body: str = ""
    parse_error: str = ""

    @property
    def id(self) -> str:
        if self.path.endswith("SKILL.md"):
            return str((self.meta.get("metadata") or {}).get("id") or self.meta.get("name") or "")
        return str(self.meta.get("id") or "")

    @property
    def stem(self) -> str:
        return self.path.rsplit("/", 1)[-1].removesuffix(".md")


def parse(path: str, text: str) -> Note:
    text = text.removeprefix("\ufeff").replace("\r\n", "\n")
    match = FRONT.match(text)
    if not match:
        return Note(path, {}, text)
    try:
        meta = YAML(typ="safe", pure=True).load(match.group(1)) or {}
    except YAMLError as exc:
        return Note(path, {}, text[match.end() :], f"frontmatter inválido: {str(exc)[:100]}")
    if not isinstance(meta, dict):
        return Note(path, {}, text[match.end() :], "frontmatter inválido: precisa ser um mapa")
    return Note(path, meta, text[match.end() :])


def wikilinks(note: Note) -> list[str]:
    return sorted({m.strip() for m in WIKILINK.findall(note.body)})


def typed_links(note: Note) -> list[dict[str, str]]:
    return [
        {"rel": str(x.get("rel", "")), "to": str(x.get("to", ""))}
        for x in note.meta.get("links") or []
        if isinstance(x, dict)
    ]


def _scope_of(path: str) -> str:
    parts = path.split("/")
    return f"project:{parts[1]}" if parts[0] == "projects" and len(parts) > 1 else "global"


def _resolves(target: str, ids: set[str], stems: set[str], paths: set[str]) -> bool:
    clean = target.split(":", 1)[-1] if target.startswith(("skill:", "component:")) else target
    if target.startswith("component:"):
        return True  # componente mora na linhagem, não no cérebro
    return clean in ids or clean in stems or f"{clean}.md" in paths or clean in paths


def lint(
    notes: list[Note], today: date | None = None, project_class: dict[str, str] | None = None
) -> list[dict[str, str]]:
    """Problemas do cérebro. `error` barra o PR e a assinatura; `warning` só avisa."""
    problems: list[dict[str, str]] = []
    today = today or date.today()

    def add(note: Note, level: str, message: str) -> None:
        problems.append({"file": note.path, "level": level, "message": message})

    ids = {n.id for n in notes if n.id}
    stems = {n.stem for n in notes}
    paths = {n.path for n in notes}
    seen_ids: dict[str, str] = {}
    seen_stems: dict[str, str] = {}
    for note in notes:
        if note.parse_error:
            add(note, "error", note.parse_error)
            continue
        generated = GENERATED in note.path.split("/")
        is_skill = note.path.endswith("SKILL.md")
        if not generated and not is_skill and not note.meta:
            add(note, "error", "sem frontmatter")
            continue
        if not note.meta:
            continue
        if is_skill:
            if not note.meta.get("name") or not note.meta.get("description"):
                add(note, "error", "skill precisa de name e description")
            elif str(note.meta["name"]) != note.path.split("/")[-2]:
                add(note, "error", f"name {note.meta['name']} difere da pasta")
        else:
            nid = note.id
            if not nid:
                add(note, "error", "sem id")
            elif not ID.match(nid):
                add(note, "error", f"id inválido: {nid}")
            elif nid != note.stem:
                add(note, "error", f"id {nid} difere do nome do arquivo (o Obsidian resolve pelo nome)")
            kind = str(note.meta.get("type", ""))
            if kind not in TYPES:
                add(note, "error", f"type inválido: {kind or '(vazio)'}")
            status = str(note.meta.get("status", "active"))
            if status not in STATUSES:
                add(note, "error", f"status inválido: {status}")
            scope = str(note.meta.get("scope", ""))
            expected = _scope_of(note.path)
            if scope != expected:
                add(note, "error", f"scope {scope or '(vazio)'} não bate com a pasta (esperado {expected})")
            if nid:
                if nid in seen_ids:
                    add(note, "error", f"id repetido: {nid} (também em {seen_ids[nid]})")
                seen_ids[nid] = note.path
        lowered = note.stem.lower()
        if not is_skill:
            if lowered in seen_stems and seen_stems[lowered] != note.path:
                add(
                    note,
                    "error",
                    f"nome repetido, mesmo só por maiúscula: {note.stem} (também em {seen_stems[lowered]})",
                )
            seen_stems[lowered] = note.path
        data_class = str(note.meta.get("data_class", "internal"))
        if data_class not in DATA_RANK:
            add(note, "error", f"data_class inválida: {data_class}")
        elif (limit := (project_class or {}).get(_scope_of(note.path))) and DATA_RANK[data_class] > DATA_RANK.get(
            limit, 3
        ):
            add(note, "error", f"data_class {data_class} acima da do projeto ({limit})")
        for link in wikilinks(note):
            if not _resolves(link, ids, stems, paths):
                add(note, "error", f"link quebrado: [[{link}]]")
        for typed in typed_links(note):
            if typed["rel"] not in RELATIONS:
                add(note, "error", f"relação desconhecida: {typed['rel'] or '(vazia)'}")
            if not _resolves(typed["to"], ids, stems, paths):
                add(note, "error", f"link quebrado: {typed['to']}")
        verified = note.meta.get("verified_at")
        if verified:
            try:
                age = (today - date.fromisoformat(str(verified))).days
                if age > 365:
                    add(note, "warning", f"conferido há {age} dias: {note.meta.get('invalidate_when', 'reveja')}")
            except ValueError:
                add(note, "error", f"verified_at inválido: {verified}")
    return problems


def nodes_and_edges(notes: list[Note]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Índice determinístico: mesma versão do cérebro, mesmo resultado, em qualquer sistema."""
    nodes = []
    edges = []
    by_key: dict[str, str] = {}
    for note in notes:
        key = note.id or note.stem
        by_key.setdefault(key, note.path)
        by_key.setdefault(note.stem, note.path)
    for note in sorted(notes, key=lambda n: n.path):
        key = note.id or note.stem
        nodes.append(
            {
                "id": key,
                "path": note.path,
                "type": str(note.meta.get("type") or ("skill" if note.path.endswith("SKILL.md") else "note")),
                "scope": _scope_of(note.path),
                "title": str(note.meta.get("title") or note.meta.get("name") or key),
                "tags": sorted(str(t) for t in note.meta.get("tags") or []),
                "status": str(note.meta.get("status", "active")),
            }
        )
        for target in wikilinks(note):
            edges.append({"from": key, "rel": "links_to", "to": target})
        for link in typed_links(note):
            edges.append({"from": key, "rel": link["rel"], "to": link["to"]})
    edges.sort(key=lambda e: (e["from"], e["rel"], e["to"]))
    return nodes, edges


def visible_to(path: str, project: str) -> bool:
    """Isolamento: o agente lê o global e o próprio projeto. O filtro vem antes da busca e do grafo."""
    parts = path.replace("\\", "/").split("/")
    return parts[0] != "projects" or (len(parts) > 1 and parts[1] == project)


def add_frontmatter(path: str, text: str, today: date | None = None) -> str:
    """Migração: dá identidade a um arquivo que ainda não tem. Quem escolheu o nome do arquivo escolheu o ID."""
    if FRONT.match(text) or path.endswith("SKILL.md") or GENERATED in path.split("/"):
        return text
    stem = path.rsplit("/", 1)[-1].removesuffix(".md")
    clean = re.sub(r"[^a-z0-9-]+", "-", stem.lower()).strip("-")
    kind = next(
        (k for prefix, k in _FOLDER_TYPE if path.startswith(prefix)),
        "note",
    )
    title = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), stem)
    head = [
        "---",
        f"id: {clean}",
        f"type: {kind}",
        f'title: "{title.replace(chr(34), chr(39))}"',
        f"scope: {_scope_of(path)}",
        "status: active",
        "data_class: internal",
        f"verified_at: {(today or date.today()).isoformat()}",
        "---",
        "",
    ]
    return "\n".join(head) + text


_FOLDER_TYPE = (
    ("lessons/", "lesson"),
    ("decisions/", "decision"),
    ("knowledge/", "knowledge"),
    ("prompts/", "prompt"),
    ("standards/", "standard"),
    ("templates/", "template"),
    ("recipes/", "recipe"),
)


def _adjacent(edges: list[dict[str, Any]]) -> dict[str, set[str]]:
    near: dict[str, set[str]] = {}
    for edge in edges:
        a, b = str(edge["from"]), str(edge["to"])
        near.setdefault(a, set()).add(b)
        near.setdefault(b, set()).add(a)
    return near


def neighbors(edges: list[dict[str, Any]], start: str, depth: int = 2) -> dict[str, int]:
    """O que se liga à nota em até `depth` saltos, com a distância. A ordem é sempre a mesma."""
    near = _adjacent(edges)
    found: dict[str, int] = {start: 0}
    frontier = [start]
    for distance in range(1, depth + 1):
        frontier = sorted({n for f in frontier for n in near.get(f, ()) if n not in found})
        found.update(dict.fromkeys(frontier, distance))
    found.pop(start)
    return found


def path(edges: list[dict[str, Any]], start: str, end: str) -> list[str]:
    """O caminho mais curto entre duas notas, ou vazio se não houver."""
    near = _adjacent(edges)
    came: dict[str, str] = {start: start}
    frontier = [start]
    while frontier and end not in came:
        nexts = []
        for node in frontier:
            for other in sorted(near.get(node, ())):
                if other not in came:
                    came[other] = node
                    nexts.append(other)
        frontier = nexts
    if end not in came:
        return []
    out = [end]
    while out[-1] != start:
        out.append(came[out[-1]])
    return out[::-1]
