"""Camada de harness (§20): os casos rodam de verdade, com agente simulado, em cópias descartáveis."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import Runner


@pytest.mark.parametrize(
    "case", ["handoff-desatualizado", "parada-e-retomada", "bloqueio-parcial", "revisao-somente-leitura"]
)
def test_harness_case_passes(project: Path, nexo: Runner, case: str) -> None:
    code, out, err = nexo("--json", "eval", "harness", "--case", case)
    assert code == 0, out + err
    assert json.loads(out)[0]["ok"] is True


def test_unknown_case_is_refused(project: Path, nexo: Runner) -> None:
    code, _, err = nexo("eval", "harness", "--case", "nao-existe")
    assert code != 0 and "caso desconhecido" in err
