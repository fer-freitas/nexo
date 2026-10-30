"""Classificação de dados: a IA sugere, a pessoa decide (achado no uso real: o planejador marcou uma feature de
login como `restricted` sozinho e o planejamento travou sem rota)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from tests.conftest import Runner
from tests.integration.test_story import SPEC


def spec_with(data_class: str) -> dict[str, Any]:
    step = copy.deepcopy(SPEC)
    step["done"]["structured"]["spec_patch"]["data_class"] = data_class
    return step


def questions(project: Path) -> list[dict[str, Any]]:
    from ruamel.yaml import YAML

    folder = next(project.glob(".nexo/specs/F-1-*"))
    return list(
        (YAML(typ="safe").load((folder / "questions.yaml").read_text(encoding="utf-8")) or {}).get("questions") or []
    )


def test_ai_cannot_lock_the_feature_by_itself(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    scenario([{"tool": "claude", "events": [spec_with("restricted")]}])
    code, out, err = nexo("plan", "login com e-mail e senha")
    assert code == 0, out + err
    status = json.loads(nexo("--json", "plan", "status", "F-1")[1])
    assert status.get("data_class", "") != "restricted"
    held = [x for x in questions(project) if x.get("section") == "data_class"]
    assert held and held[0]["severity"] == "blocking" and "nenhuma ferramenta de IA" in held[0]["text"]


def test_same_vendors_is_accepted_and_answer_or_command_decides(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    scenario([{"tool": "claude", "events": [spec_with("restricted")]}, {"tool": "claude", "events": [SPEC]}])
    nexo("plan", "login com e-mail e senha")
    qid = next(x["id"] for x in questions(project) if x.get("section") == "data_class")
    code, out, err = nexo("plan", "answer", "F-1", f"{qid}=confidencial")
    assert code == 0, out + err
    folder = next(project.glob(".nexo/specs/F-1-*"))
    assert "data_class: internal" in (folder / "spec.yaml").read_text(encoding="utf-8")  # internal ≡ confidential
    code, out, _ = nexo("plan", "classify", "F-1", "restricted", "--reason", "dados de saúde")
    assert code == 0 and "nenhuma ferramenta de IA" in out
    code, out, err = nexo("plan", "feature", "F-1")
    assert code != 0 and "plan classify F-1" in out + err  # o erro diz como sair
    logs = json.loads(nexo("--json", "logs", "-n", "200")[1])
    assert any(e["type"] == "spec.data_class" and e["payload"]["to"] == "restricted" for e in logs)
