"""Entrar no time: clona o repositório do time e deixa o projeto escolhido como atual."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from nexo.kernel.context import AppContext, nexo_config_home, nexo_home
from nexo.kernel.errors import NexoError
from nexo.kernel.runtime import yamlfile


def clone_dir(team: str) -> Path:
    return nexo_home() / "teams" / team


def join(ctx: AppContext, team: str, repo: str = "", project: str = "") -> dict[str, Any]:
    from nexo.kernel.platform import config as settings

    if not settings.resolved(ctx).values.get("squad"):
        settings.set_value(ctx, "squad", team, level="personal")  # o time é o ponto de partida; `nexo squad set` troca
    target = clone_dir(team)
    if not target.exists():
        if not repo:
            raise NexoError(f"informe --repo com o endereço do repositório do time {team}")
        target.parent.mkdir(parents=True, exist_ok=True)
        done = subprocess.run(
            ["git", "-c", "core.longpaths=true", "clone", "--quiet", repo, str(target)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env={**__import__("os").environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        if done.returncode != 0:
            raise NexoError(f"não foi possível clonar {repo}: {done.stderr.strip()[:300]}")
    folder = target / "projects"
    projects = sorted(p.name for p in folder.iterdir() if p.is_dir()) if folder.is_dir() else []
    chosen = project or (projects[0] if len(projects) == 1 else "")
    if chosen:
        root = folder / chosen
        marker = root / ".nexo" / "nexo.yaml"
        marker.parent.mkdir(parents=True, exist_ok=True)
        if not marker.is_file():
            yamlfile.dump(marker, {"project": chosen, "team": {"name": team, "repo": repo}})
        home = nexo_config_home()
        home.mkdir(parents=True, exist_ok=True)
        (home / "current-project.txt").write_text(str(root), encoding="utf-8")
    return {"team": team, "projects": projects, "current": chosen, "path": str(target)}


def init_team_repo(path: Path, name: str, mode: str, members: list[dict[str, Any]]) -> None:
    """Cria a estrutura de um repositório de time novo."""
    path.mkdir(parents=True, exist_ok=True)
    (path / ".gitattributes").write_text("* text eol=lf\n", encoding="utf-8", newline="\n")
    yamlfile.dump(path / "team.yaml", {"name": name, "mode": mode, "members": members})


def link(ctx: AppContext, team: str, repo: str) -> dict[str, Any]:
    """Liga o repositório de código ao time e leva o projeto para o clone do time."""
    if ctx.project_root is None:
        raise NexoError("rode dentro de um repositório do projeto (`nexo init`)")
    target = clone_dir(team)
    if not target.exists():
        join(ctx, team, repo)
    config = ctx.project_root / ".nexo" / "nexo.yaml"
    data = dict(yamlfile.load(config))
    data["team"] = {"name": team, "repo": repo}
    yamlfile.dump(config, data)
    project = str(data.get("project") or ctx.project_root.name)
    from nexo.kernel import services

    services.load("lineage.app").register_project(ctx.project_root)
    marker = target / "projects" / project / ".nexo" / "nexo.yaml"
    marker.parent.mkdir(parents=True, exist_ok=True)
    if not marker.is_file():
        yamlfile.dump(marker, {"project": project, "team": {"name": team, "repo": repo}})
    return {"team": team, "project": project, "path": str(target)}
