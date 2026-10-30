"""Repositório de código e PRs: um adaptador por provedor, todos no mesmo formato normalizado.

`scm.provider`: github | gitlab | bitbucket | azure. `scm.repo`: owner/repo (GitHub), grupo/projeto (GitLab),
workspace/repositorio (Bitbucket) ou o nome do repositório (Azure Repos, com `scm.org_url` e `scm.project`, que caem
no `tracker.azure` quando ausentes). GitLab próprio: `scm.base_url`.
"""

from __future__ import annotations

from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.platform import config as platform_config
from nexo.kernel.secrets import credentials

PROVIDERS = ("github", "gitlab", "bitbucket", "azure")


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
    if provider == "github":
        from nexo.kernel import services
        from nexo.modules.delivery.adapters.scm.github import GitHubScm

        github_token = services.get("demands.tracker", "github_token")

        return GitHubScm(repo, github_token(ctx), remote)
    if provider == "gitlab":
        from nexo.modules.delivery.adapters.scm.gitlab import GitLabScm

        return GitLabScm(repo, _secret("gitlab"), remote, str(cfg.get("base_url") or "https://gitlab.com"))
    if provider == "bitbucket":
        from nexo.modules.delivery.adapters.scm.bitbucket import BitbucketScm

        return BitbucketScm(repo, _secret("bitbucket"), remote)
    from nexo.kernel import services
    from nexo.modules.delivery.adapters.scm.azure import AzureReposScm

    azure_bearer = services.get("demands.tracker", "azure_bearer")
    tracker_config = services.get("demands.tracker", "tracker_config")

    az = dict(tracker_config(ctx).get("azure") or {})
    org, project = cfg.get("org_url") or az.get("org_url"), cfg.get("project") or az.get("project")
    if not org or not project:
        raise NexoError("Azure Repos exige scm.org_url e scm.project (ou tracker.azure)")
    bearer = azure_bearer(ctx)
    return AzureReposScm(str(org), str(project), repo, bearer or _secret("tracker"), remote, bearer=bool(bearer))
