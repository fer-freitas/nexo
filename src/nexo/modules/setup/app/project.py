"""`nexo init` e informações do projeto."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexo.kernel.context import PROJECT_DIR, AppContext, find_git_root
from nexo.kernel.quality.stack import detect_stacks
from nexo.kernel.runtime import yamlfile

SKIP_DIRS = {
    ".git",
    "node_modules",
    "bin",
    "obj",
    ".venv",
    "venv",
    "dist",
    "build",
    "__pycache__",
    PROJECT_DIR,
}
MAX_DEPTH = 4
MAX_FILES = 20_000

# Fica no git: nexo.yaml, specs/, tasks/ do plano, bugs/, extensions/, standards.yaml, mappings/, templates/,
# evals/golden/ e metrics/baseline.json. Fora (local ou gerado de novo a qualquer momento):
GITIGNORE_LINES = (
    "runs/",  # execuções locais
    "worktrees/",  # worktrees dos agentes
    "map/",  # mapa e grafo do código (cache)
    "quality/",  # relatórios gerados por `nexo quality run` e `nexo quality standards check`
    "deps/",  # varredura de dependências
    "evals/out/",  # saídas das avaliações
    "tracker/",  # troca de arquivos com a ferramenta de gestão (pode ter dados do cliente)
    "evidencias/",  # pacotes de evidência (regenerados e conferidos a partir da trilha)
    "tasks/*-QA*.yaml",  # tarefas internas da execução da história
    "tasks/*-FIX*.yaml",
    "tasks/*-CHAR*.yaml",
    "tasks/*-DOC*.yaml",
    "tasks/*-INT.yaml",
    "*.tmp",
    "tmp/",  # rascunho dos agentes
)
GITIGNORE = (
    "# Gerado pelo Nexo: o que é local ou gerado de novo (o resto de .nexo/ vai para o git)\n"
    + "\n".join(GITIGNORE_LINES)
    + "\n"
)


def missing_gitignore(root: Path) -> list[str]:
    path = root / PROJECT_DIR / ".gitignore"
    present = {ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()} if path.is_file() else set()
    return [ln for ln in GITIGNORE_LINES if ln not in present]


def ensure_gitignore(root: Path) -> list[str]:
    """Cria ou completa `.nexo/.gitignore` só com as linhas que faltam (o que o time acrescentou fica)."""
    path = root / PROJECT_DIR / ".gitignore"
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(GITIGNORE, encoding="utf-8")
        return list(GITIGNORE_LINES)
    missing = missing_gitignore(root)
    if missing:
        text = path.read_text(encoding="utf-8")
        sep = "" if text.endswith("\n") or not text else "\n"
        path.write_text(text + sep + "\n".join(missing) + "\n", encoding="utf-8")
    return missing


@dataclass(frozen=True)
class InitResult:
    root: Path
    created: bool
    stacks: list[str]


def list_files(root: Path) -> list[str]:
    found: list[str] = []
    base_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        depth = len(current.parts) - base_depth
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and depth < MAX_DEPTH]
        for name in filenames:
            found.append((current / name).relative_to(root).as_posix())
            if len(found) >= MAX_FILES:
                return found
    return found


def _read(root: Path) -> Any:
    def read(rel: str) -> str:
        try:
            return (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    return read


def init_project(ctx: AppContext, path: Path | None = None) -> InitResult:
    root = (path or find_git_root(ctx.cwd) or ctx.cwd).resolve()
    config_path = root / PROJECT_DIR / "nexo.yaml"
    stacks = detect_stacks(list_files(root), _read(root))
    if config_path.is_file():
        data = yamlfile.load(config_path)
        if data.get("stacks") != stacks:
            data["stacks"] = stacks
            yamlfile.dump(config_path, data)
        added = ensure_gitignore(root)  # projeto antigo: completa o .gitignore do Nexo
        ctx.audit("project.init", {"root": str(root), "created": False, "stacks": stacks, "gitignore": added})
        return InitResult(root, False, stacks)
    new_data: dict[str, Any] = {
        "project": root.name,
        "squad": "",
        "stacks": stacks,
        "data_class": "confidential",
        "tracker": {"kind": "local"},
    }
    yamlfile.dump(config_path, new_data)
    ensure_gitignore(root)
    ctx.audit("project.init", {"root": str(root), "created": True, "stacks": stacks})
    return InitResult(root, True, stacks)


def project_summary(ctx: AppContext) -> dict[str, Any] | None:
    cfg = ctx.project_config()
    if cfg is None:
        return None
    data = yamlfile.load(cfg)
    return {
        "name": data.get("project") or (ctx.project_root.name if ctx.project_root else ""),
        "squad": data.get("squad") or "",
        "stacks": data.get("stacks") or [],
        "tracker": (data.get("tracker") or {}).get("kind", "local"),
        "data_class": data.get("data_class", "confidential"),
    }
