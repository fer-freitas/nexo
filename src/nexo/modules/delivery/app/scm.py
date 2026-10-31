"""Repositório de código e PRs: só o GitHub. `scm.provider: github` e `scm.repo: dono/repositório`."""

from __future__ import annotations

from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.platform import config as platform_config
from nexo.kernel.secrets import credentials

PROVIDERS = ("github",)


def config(ctx: AppContext) -> dict[str, Any]:
    return dict(platform_config.resolved(ctx).values.get("scm") or {})


def enabled(ctx: AppContext) -> bool:
    cfg = config(ctx)
    return cfg.get("provider") in PROVIDERS and bool(cfg.get("repo"))


def _secret(name: str) -> str:
    value = credentials.get_secret(name)
    if not value:
        raise NexoError(f"credencial '{name}' ausente: rode `nexo login {name}`")
    return value


def build(ctx: AppContext) -> Any:
    """Adaptador do provedor configurado (NexoError se a configuração estiver incompleta)."""
    cfg = config(ctx)
    provider = str(cfg.get("provider") or "")
    if provider not in PROVIDERS or not cfg.get("repo"):
        raise NexoError(f"configure scm.provider ({' | '.join(PROVIDERS)}) e scm.repo no projeto")
    repo, remote = str(cfg["repo"]), str(cfg.get("remote", "origin"))
    from nexo.kernel import services
    from nexo.modules.delivery.adapters.scm.github import GitHubScm

    github_token = services.get("demands.tracker", "github_token")

    return GitHubScm(repo, github_token(ctx), remote)
