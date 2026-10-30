"""`nexo setup`: confere e instala o que é preciso para rodar. Nada é instalado sem confirmação ou `--yes`."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.platform import config
from nexo.kernel.runtime import system, toolenv

# Os identificadores de pacote ainda não foram conferidos no Windows da empresa, onde a instalação pode ser bloqueada.
PACKAGES: dict[str, dict[str, list[str]]] = {
    "git": {"brew": ["brew", "install", "git"], "winget": ["winget", "install", "--id", "Git.Git", "-e"]},
    "gitleaks": {
        "brew": ["brew", "install", "gitleaks"],
        "winget": ["winget", "install", "--id", "Gitleaks.Gitleaks", "-e"],
    },
    "claude": {
        "brew": ["brew", "install", "--cask", "claude-code"],
        "winget": ["winget", "install", "--id", "Anthropic.ClaudeCode", "-e"],
    },
    "copilot": {"npm": ["npm", "install", "-g", "@github/copilot"]},
    "devin": {},
}
PROVISIONED = ("pip-audit", "semgrep")
AGENTS = ("claude", "copilot", "devin")


@dataclass
class Item:
    name: str
    why: str
    required: bool
    present: bool
    how: list[str] = field(default_factory=list)
    done: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _installer(name: str) -> list[str]:
    options = PACKAGES.get(name) or {}
    for manager in ("winget",) if sys.platform == "win32" else ("brew",):
        if manager in options and shutil.which(manager):
            return options[manager]
    if "npm" in options and shutil.which("npm"):
        return options["npm"]
    return []


def _stack_tools(ctx: AppContext) -> list[str]:
    """O que a receita das stacks deste repositório exige."""
    from nexo.kernel import services

    root = ctx.project_root
    if root is None or not services.available("quality.runner"):
        return []
    missing = services.get("quality.runner", "missing_required_tools")
    brain = ctx.brain_dir if ctx.brain_dir.is_dir() else None
    out: list[str] = []
    for stack in config.resolved(ctx).values.get("stacks") or []:
        out += [t for t in missing(str(stack), root, brain) if t not in out]
    return out


def plan(ctx: AppContext, agents: list[str] | None = None) -> list[Item]:
    items = [
        Item("git", "checkpoints e pastas de trabalho", True, bool(system.which("git")), _installer("git")),
        Item("gitleaks", "o portão não valida sem ele", True, bool(system.which("gitleaks")), _installer("gitleaks")),
    ]
    for tool in _stack_tools(ctx):
        if tool not in {i.name for i in items}:
            items.append(Item(tool, "exigido pela receita deste repositório", True, False, _installer(tool)))
    for tool in PROVISIONED:
        items.append(
            Item(
                tool, "segurança, em ambiente próprio", False, toolenv.path_for(tool).is_file(), ["(ambiente próprio)"]
            )
        )
    frozen = bool(getattr(sys, "frozen", False))
    items.append(
        Item(
            "central",
            "para `nexo demo site` e `nexo central up`",
            False,
            frozen or importlib.util.find_spec("uvicorn") is not None,
            [sys.executable, "-m", "pip", "install", "nexo[central]"] if not frozen else [],
        )
    )
    wanted = list(agents or [])
    for tool in AGENTS:
        present = bool(system.which(tool))
        if present or tool in wanted:
            items.append(Item(tool, "ferramenta de IA", tool in wanted, present, _installer(tool)))
    return items


def _install(item: Item) -> str:
    if item.name in PROVISIONED:
        _found, problem = toolenv.ensure(item.name)
        return problem or "instalado"
    if not item.how:
        return "instale manualmente: não há instalador disponível nesta máquina"
    done = subprocess.run(item.how, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if done.returncode != 0:
        return f"falhou: {(done.stderr or done.stdout).strip()[:200]}"
    return "instalado"


def apply(ctx: AppContext, items: list[Item], approve: Any) -> list[Item]:
    """`approve(item)` decide cada instalação. Um item que falha não impede os outros."""
    for item in items:
        if item.present or not approve(item):
            continue
        item.done = _install(item)
        item.present = item.done == "instalado"
        ctx.audit("setup.install", {"tool": item.name, "result": item.done[:120]})
    return items
