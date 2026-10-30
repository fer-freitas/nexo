"""automações sem depender de ninguém digitar: DoR criada na aprovação do plano e piloto automático
(sincronizar, fechar o mês, varrer dependências, conferir o portão) com avisos nos próximos passos."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import Runner
from tests.integration.test_story import SPEC, backlog


def test_plan_approval_creates_the_dor_of_each_story(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    scenario(
        [
            {"tool": "claude", "events": [SPEC]},
            {"tool": "claude", "events": [backlog(["src/svc/version.py"], ["README.md"])]},
        ]
    )
    assert nexo("plan", "versão")[0] == 0
    code, out, err = nexo("plan", "approve", "F-1")
    assert code == 0, err
    refinement = next(project.glob(".nexo/specs/F-1-*/refinamento/US-1-1.yaml"))
    assert refinement.is_file() and "US-1-1" in refinement.read_text(encoding="utf-8")
    assert "refinamento (DoR) criado para US-1-1" in out + err
