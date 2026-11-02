"""De qual squad a pessoa participa. Custo, métricas e flags por squad dependem disso."""

from __future__ import annotations

from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.platform import config
from nexo.kernel.runtime import yamlfile
from nexo.kernel.workspace.specs import project_settings


def current(ctx: AppContext) -> dict[str, str]:
    res = config.resolved(ctx)
    name = str(res.values.get("squad") or "")
    return {"squad": name, "source": str(res.origin.get("squad", "")) if name else ""}


def known(ctx: AppContext) -> list[str]:
    """Os squads que o cérebro conhece, o do repositório do time e o seu. Não é uma lista fechada."""
    names: set[str] = {current(ctx)["squad"]}
    folder = ctx.brain_dir / "squads"
    if folder.is_dir():
        names |= {p.name for p in folder.iterdir() if p.is_dir()}
    if ctx.project_root is not None:
        settings = project_settings(ctx.project_root)
        team_file = settings.team_dir() / "team.yaml"
        if team_file.is_file():
            names.add(str(yamlfile.load(team_file).get("squad") or settings.team_name or ""))
    return sorted(n for n in names if n)


def set_squad(ctx: AppContext, name: str, project: bool = False) -> dict[str, str]:
    """Por padrão vale para a pessoa, em todos os repositórios. `project` é o padrão de quem ainda não informou."""
    clean = name.strip()
    if not clean or any(c.isspace() or c in "/\\" for c in clean):
        raise NexoError("use um nome de squad sem espaço nem barra, como sq-pagamentos")
    if project and ctx.project_root is None:
        raise NexoError("--project precisa de um repositório preparado (`nexo init`)")
    config.set_value(ctx, "squad", clean, level="project" if project else "personal")
    ctx.audit("squad.set", {"squad": clean, "level": "project" if project else "personal"})
    out = {"squad": clean, "level": "repositório" if project else "pessoa"}
    now = current(ctx)
    if now["squad"] != clean:  # o valor pessoal vale mais que o padrão do repositório
        out["aviso"] = (
            f"para você continua valendo {now['squad']} (fonte: {now['source']}); este é o padrão de quem não informou"
        )
    return out


def state(ctx: AppContext) -> dict[str, Any]:
    return {**current(ctx), "known": known(ctx)}
