"""`nexo brain lint|index|promote`, assinatura pelos arquivos rastreados e proposta por PR."""

from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError, PolicyViolation
from nexo.modules.knowledge.domain import notes as model

SKIP_DIRS = {".git", ".brain", ".obsidian", ".trash"}


def _git(root: Path, *args: str, check: bool = True) -> str:
    done = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )
    if check and done.returncode != 0:
        raise NexoError(f"git {args[0]} falhou: {done.stderr.strip()[:200]}")
    return done.stdout


def is_repo(root: Path) -> bool:
    return (root / ".git").exists()


def tracked_files(root: Path) -> list[str]:
    """No clone de trabalho, vale o que o Git rastreia: `.obsidian/` e notas soltas não entram."""
    if is_repo(root):
        return [p for p in _git(root, "ls-files").splitlines() if p.split("/", 1)[0] not in SKIP_DIRS]
    return sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and p.relative_to(root).parts[0] not in SKIP_DIRS
    )


def load_notes(root: Path, project: str | None = None) -> list[model.Note]:
    out = []
    for rel in tracked_files(root):
        if not rel.endswith(".md") or rel.split("/")[-1] == "README.md":
            continue
        if project is not None and not model.visible_to(rel, project):
            continue
        out.append(model.parse(rel, (root / rel).read_text(encoding="utf-8", errors="replace")))
    return out


def _project_classes(root: Path) -> dict[str, str]:
    from nexo.kernel.runtime import yamlfile

    folder = root / "projects"
    if not folder.is_dir():
        return {}
    return {
        f"project:{p.name}": str(yamlfile.load(p / "project.yaml").get("data_class", "restricted"))
        for p in folder.iterdir()
        if (p / "project.yaml").is_file()
    }


def lint(root: Path, today: date | None = None) -> dict[str, Any]:
    from nexo.kernel.security.redaction import find

    notes = load_notes(root)
    problems = model.lint(notes, today, _project_classes(root))
    for note in notes:
        kinds = {f.kind for f in find(note.body)} - {"email"}
        if kinds:
            problems.append(
                {"file": note.path, "level": "error", "message": f"dado sensível: {', '.join(sorted(kinds))}"}
            )
    errors = [p for p in problems if p["level"] == "error"]
    return {"notes": len(notes), "errors": len(errors), "warnings": len(problems) - len(errors), "problems": problems}


def index(ctx: AppContext, root: Path | None = None, project: str | None = None) -> dict[str, Any]:
    """Gera nós e arestas no cache local. Sempre derivado; nunca entra no Git."""
    source = root or ctx.brain_dir
    if not source.is_dir():
        raise NexoError("cérebro não encontrado: rode `nexo brain pull`")
    nodes, edges = model.nodes_and_edges(load_notes(source, project))
    out = ctx.home / "brain-index"
    out.mkdir(parents=True, exist_ok=True)
    (out / "nodes.jsonl").write_text(
        "".join(json.dumps(n, ensure_ascii=False, sort_keys=True) + "\n" for n in nodes), "utf-8", newline="\n"
    )
    (out / "edges.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n" for e in edges), "utf-8", newline="\n"
    )
    return {"nodes": len(nodes), "edges": len(edges), "folder": str(out)}


def require_clean_and_valid(root: Path) -> None:
    """Portão da assinatura: só assina árvore limpa e sem erro de lint. É aqui que o lint vale sem CI.

    Vale para o cérebro no formato com ID e links (`schema_version: 2`); o formato anterior passa por `migrate-notes`.
    """
    from nexo.kernel.runtime import yamlfile

    if int(yamlfile.load(root / "brain.yaml").get("schema_version", 1)) != 2:
        return
    if is_repo(root):
        dirty = [
            line
            for line in _git(root, "status", "--porcelain").splitlines()
            if line[3:].split("/", 1)[0] not in SKIP_DIRS
        ]
        if dirty:
            raise PolicyViolation(f"árvore do cérebro suja: faça commit antes de assinar ({len(dirty)} arquivo(s))")
    result = lint(root)
    if result["errors"]:
        first = "; ".join(f"{p['file']}: {p['message']}" for p in result["problems"] if p["level"] == "error")[:400]
        raise PolicyViolation(f"cérebro com {result['errors']} erro(s) de lint: {first}")


def review_pr(root: Path, number: int) -> dict[str, Any]:
    """Sem CI, quem revisa roda isto: traz o PR para uma pasta temporária e aplica o mesmo lint da assinatura."""
    if not is_repo(root):
        raise NexoError(f"{root} não é um clone do cérebro")
    ref = f"refs/nexo/review/{number}"
    _git(root, "fetch", "-q", "origin", f"+refs/pull/{number}/head:{ref}")
    with tempfile.TemporaryDirectory(prefix="nexo-review-", ignore_cleanup_errors=True) as tmp:
        folder = Path(tmp) / "pr"
        _git(root, "-c", "core.longpaths=true", "worktree", "add", "-q", "--detach", str(folder), ref)
        try:
            return {"pr": number, "commit": _git(folder, "rev-parse", "HEAD").strip(), **lint(folder)}
        finally:
            _git(root, "worktree", "remove", "--force", str(folder), check=False)


def propose_pr(root: Path, source: Path, target: str, title: str) -> dict[str, Any]:
    """Abre branch e commit no clone de trabalho. O PR sai depois do push; sem permissão, vira issue."""
    if not is_repo(root):
        raise NexoError(f"{root} não é um clone do cérebro")
    if not source.is_file():
        raise NexoError(f"{source} não existe")
    from nexo.kernel.security.redaction import find

    text = source.read_text(encoding="utf-8", errors="replace")
    if {f.kind for f in find(text)} - {"email"}:
        raise PolicyViolation("o arquivo contém segredo ou dado pessoal; remova antes de propor")
    note = model.parse(target, text)
    candidate = root / target
    existed = candidate.is_file()
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(text, encoding="utf-8", newline="\n")
    _git(root, "add", target)
    problems = [
        p
        for p in model.lint([*load_notes(root)], None, _project_classes(root))
        if p["level"] == "error" and p["file"] == target
    ]
    if problems:
        _git(root, "reset", "-q", "HEAD", "--", target, check=False)
        if not existed:
            candidate.unlink()
        raise PolicyViolation(f"{target}: " + "; ".join(p["message"] for p in problems))
    slug = note.id or candidate.stem
    branch = f"nexo/proposta/{slug}"
    _git(root, "checkout", "-q", "-B", branch)
    _git(root, "commit", "-q", "-m", title)
    return {"branch": branch, "file": target}


def promote(root: Path, note_id: str) -> dict[str, Any]:
    """Conhecimento de projeto vira global só por PR, com revisão humana e varredura de dado sensível."""
    from nexo.kernel.security.redaction import find

    note = next((n for n in load_notes(root) if n.id == note_id), None)
    if note is None:
        raise NexoError(f"nota desconhecida: {note_id}")
    if not note.path.startswith("projects/"):
        raise NexoError(f"{note_id} já é global")
    text = (root / note.path).read_text(encoding="utf-8")
    if {f.kind for f in find(text)} - {"email"}:
        raise PolicyViolation(f"{note_id} contém dado sensível: não pode ir para o global")
    folder = note.path.split("/")[2] if len(note.path.split("/")) > 3 else "knowledge"
    target = f"{folder}/{note.stem}.md"
    new = text.replace(f"scope: project:{note.path.split('/')[1]}", "scope: global", 1)
    (root / target).parent.mkdir(parents=True, exist_ok=True)
    (root / target).write_text(new, encoding="utf-8", newline="\n")
    (root / note.path).unlink()
    branch = f"nexo/promover/{note_id}"
    _git(root, "checkout", "-q", "-B", branch)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", f"Promove {note_id} para o global")
    return {"id": note_id, "branch": branch, "target": target}


def migrate(root: Path, today: date | None = None) -> dict[str, Any]:
    """Dá frontmatter aos arquivos que ainda não têm, para o cérebro antigo passar a abrir bem no Obsidian."""
    changed = []
    for rel in tracked_files(root):
        if not rel.endswith(".md") or rel.split("/")[-1] == "README.md":
            continue
        path = root / rel
        text = path.read_text(encoding="utf-8", errors="replace")
        new = model.add_frontmatter(rel, text, today)
        if new != text:
            path.write_text(new, encoding="utf-8", newline="\n")
            changed.append(rel)
    from nexo.kernel.runtime import yamlfile

    data = dict(yamlfile.load(root / "brain.yaml"))
    yamlfile.dump(root / "brain.yaml", {**data, "schema_version": 2})
    return {"changed": changed}
