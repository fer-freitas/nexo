"""Achado no teste real com o GitHub: job que nem começou (orçamento do Actions esgotado) aparecia como CI reprovado
e dispararia correção de código. Agora é falha de infraestrutura: não entra nas falhas de código."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from nexo.kernel.runtime import http
from nexo.modules.delivery.adapters.scm.github import GitHubScm

RUNS = {
    "check_runs": [
        {"id": 1, "name": "quality", "conclusion": "failure", "output": {"annotations_count": 1}},
        {"id": 2, "name": "build", "conclusion": "failure", "output": {"annotations_count": 0}},
        {"id": 3, "name": "lint", "conclusion": "success", "output": {}},
    ]
}
NOTES = {1: [{"message": "The job was not started because an Actions budget is preventing further use."}]}


@pytest.fixture
def github() -> Iterator[GitHubScm]:
    def transport(method: str, url: str, headers: dict[str, str], data: bytes | None) -> http.Response:
        if url.endswith("/check-runs"):
            return http.Response(200, RUNS)
        if "/check-runs/" in url and url.endswith("/annotations"):
            return http.Response(200, NOTES.get(int(url.split("/check-runs/")[1].split("/")[0]), []))
        return http.Response(404, {"message": "não simulado"})

    http.set_transport(transport)
    yield GitHubScm("empresa/repo", "token")
    http.set_transport(None)


def test_not_started_job_is_infrastructure_not_code(github: GitHubScm) -> None:
    assert github.failed_checks("abc") == ["build"]  # falha de código de verdade
    assert github.blocked_checks("abc") == ["quality"]  # nem rodou: avisa, sem correção automática
