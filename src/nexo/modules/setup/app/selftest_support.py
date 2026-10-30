"""Apoio do `nexo selftest`: projeto de exemplo embutido, leitura do plano e PR no GitHub, na camada de app."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

SAMPLE = "selftest/python-svc"


def sample_dir() -> Path:
    from nexo.kernel.runtime.resources import embedded_dir

    return embedded_dir(SAMPLE)


def github_token() -> str:
    """Só leitura do cofre do sistema (o autoteste nunca grava credencial)."""
    from nexo.kernel.secrets import credentials

    return credentials.get_secret("github") or ""


def open_questions(root: Path, feature: str) -> list[Any]:
    from nexo.kernel.spec.questions import open_questions as pending
    from nexo.kernel.workspace import specs

    return pending(specs.load_questions(specs.feature_dir(root, feature)))


def stories(root: Path, feature: str) -> list[str]:
    from nexo.kernel.workspace import specs

    backlog = specs.load_backlog(specs.feature_dir(root, feature))
    return [s.id for s in backlog.stories] if backlog else []


def refinement(root: Path, feature: str, story: str) -> Path:
    from nexo.kernel.workspace import specs

    return specs.feature_dir(root, feature) / "refinamento" / f"{story}.yaml"


def find_pr(repo: str, branch: str) -> dict[str, Any] | None:
    from nexo.kernel import services

    GitHubScm = services.get("delivery.github", "GitHubScm")

    return cast("dict[str, Any] | None", GitHubScm(repo, github_token()).find_pr(branch))


def close_pr(repo: str, number: int) -> None:
    from nexo.kernel import services

    GitHubScm = services.get("delivery.github", "GitHubScm")

    GitHubScm(repo, github_token()).update_pr(number, {"state": "closed"})


def last_agent_error() -> str:
    """Último erro de agente registrado (ex.: "login do Claude expirou"), para o relatório dizer a causa."""
    from nexo.kernel.context import AppContext, Options

    ctx = AppContext(options=Options())
    try:
        row = ctx.conn.execute(
            "SELECT tool, model, message FROM steps WHERE outcome NOT IN ('success', 'running') "
            "AND message <> '' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
    except Exception:  # banco ainda sem execuções
        return ""
    finally:
        ctx.close()
    return f"{row['tool']}/{row['model']}: {str(row['message'])[:160]}" if row else ""
