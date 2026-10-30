"""artefatos pelos templates, DoR, cascata org/squad/projeto, tracker, bug, as-built e publicação."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from nexo.kernel.runtime import http
from nexo.kernel.runtime.resources import read_text
from tests.conftest import Runner
from tests.fakes.http_fake import FakeCloud
from tests.integration.test_story import SPEC, backlog, run_json


def full_spec() -> dict[str, Any]:
    spec = copy.deepcopy(SPEC)
    patch = spec["done"]["structured"]["spec_patch"]
    patch.update(
        {
            "requirements": [
                {
                    "id": "RF-1",
                    "title": "Versão",
                    "statement": "O sistema deve informar a versão",
                    "priority": "must",
                    "source": "PRD",
                    "acceptance": ["AC-1"],
                    "rules": ["RN-1"],
                },
            ],
            "rules": [{"id": "RN-1", "text": "Versão segue SemVer", "example": "0.1.0"}],
            "epic": {
                "id": "E-1",
                "title": "Operação observável",
                "outcome": "SRE sabe o que está no ar",
                "metrics": [{"name": "MTTR", "baseline": "4h", "target": "1h"}],
                "sponsor": "Diretoria",
            },
            "design": {
                "context": "Serviço Python",
                "solution": "Função version()",
                "authz": "sem acesso externo",
                "observability": [{"flow": "version", "logs": "info", "metrics": "contador", "traces": "span"}],
                "rollout": "direto",
                "rollback": "reverter commit",
                "alternatives": [{"name": "endpoint HTTP", "summary": "rota /version", "reason": "fora de escopo"}],
            },
            "decisions": [
                {
                    "title": "Versão a partir do pacote",
                    "context": "fonte única",
                    "decision": "ler do pyproject",
                    "consequences": "sem duplicação",
                }
            ],
        }
    )
    return spec


def plan(nexo: Runner, scenario, spec: dict[str, Any], extra: list[dict[str, Any]] | None = None, b=None) -> Path:  # type: ignore[no-untyped-def]
    scenario(
        [
            {"tool": "claude", "events": [spec]},
            {"tool": "claude", "events": [b or backlog(["src/svc/version.py"], ["README.md"])]},
            *(extra or []),
        ]
    )
    assert nexo("plan", "versão")[0] == 0
    return next((Path.cwd() / ".nexo" / "specs").glob("F-1-*"))


def org_file(nexo_home: Path, rel: str, text: str) -> None:
    path = nexo_home / "brain" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_feature_documents_from_templates(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    folder = plan(nexo, scenario, full_spec())
    names = {p.relative_to(folder).as_posix() for p in folder.rglob("*.md")}
    assert {"spec.md", "feature.md", "requisitos.md", "requisito-tecnico.md", "epic.md"} <= names
    assert "adr/0001-versao-a-partir-do-pacote.md" in names
    sdd = (folder / "spec.md").read_text(encoding="utf-8")
    assert "## 5. Critérios de aceite" in sdd and "Requisitos: RF-1" in sdd and "Prontidão: **100%**" in sdd
    assert "- RF-1 → critérios AC-1" in (folder / "requisitos.md").read_text(encoding="utf-8")
    assert "MTTR**: 4h → 1h" in (folder / "epic.md").read_text(encoding="utf-8")
    assert "- Requisitos funcionais: `requisitos.md`" in (folder / "feature.md").read_text(encoding="utf-8")
    assert "descartada porque fora de escopo" in (folder / "requisito-tecnico.md").read_text(encoding="utf-8")
    assert json.loads(nexo("--json", "plan", "status", "F-1")[1])["ready"] is True


def test_requirement_without_acceptance_blocks_readiness(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    spec = full_spec()
    spec["done"]["structured"]["spec_patch"]["requirements"][0]["acceptance"] = []
    plan(nexo, scenario, spec)
    status = json.loads(nexo("--json", "plan", "status", "F-1")[1])
    assert status["ready"] is False and status["score"] == 99
    assert any(m["key"] == "requirements_trace" and "RF-1" in m["hint"] for m in status["missing"])
    code, _, err = nexo("plan", "approve", "F-1")
    assert code == 1 and "Requisitos must/should" in err


def test_org_requires_documents_and_changes_template_without_release(
    project: Path,
    nexo: Runner,
    scenario,
    nexo_home: Path,  # type: ignore[no-untyped-def]
) -> None:
    org_file(nexo_home, "org/config.yaml", "planning:\n  required_documents: [sdd, technical-design]\n")
    sdd = read_text("templates/sdd.md").replace(
        "## 12. Premissas e dúvidas",
        "## 12. Conformidade\nRevisado pelo DPO: {{data_class}}\n\n## 13. Premissas e dúvidas",
    )
    org_file(nexo_home, "templates/sdd.md", sdd)
    folder = plan(nexo, scenario, copy.deepcopy(SPEC) | {})  # sem design técnico
    assert "## 12. Conformidade" in (folder / "spec.md").read_text(encoding="utf-8")
    status = json.loads(nexo("--json", "plan", "status", "F-1")[1])
    assert status["ready"] is False and any(m["key"].startswith("template:technical-design") for m in status["missing"])
    listed = {t["type"]: t["origin"] for t in json.loads(nexo("--json", "template", "list")[1])}
    assert listed["sdd"] == "org" and listed["story"] == "embutido"


def test_squad_and_project_overrides_respect_locked_sections(
    project: Path,
    nexo: Runner,
    scenario,
    nexo_home: Path,  # type: ignore[no-untyped-def]
) -> None:
    nexo("config", "set", "squad", "pagamentos", "--project")
    story = read_text("templates/story.md").replace("## Contexto", "## Valor de negócio\n{{so_that}}\n\n## Contexto")
    org_file(nexo_home, "squads/pagamentos/templates/story.md", story)
    no_ac = "---\nnexo_template: story\nrequired_sections: [narrativa]\n---\n# {{id}}\n\n## Narrativa\n{{as_a}}\n"
    (project / ".nexo" / "templates").mkdir(parents=True)
    (project / ".nexo" / "templates" / "task.md").write_text(no_ac.replace("story", "task"), encoding="utf-8")
    (project / ".nexo" / "templates" / "story.md").write_text(no_ac, encoding="utf-8")  # remove seção travada
    b = backlog(["src/svc/version.py"], ["README.md"])
    b["done"]["structured"]["stories"][0].update(
        {"as_a": "SRE", "i_want": "ver a versão", "so_that": "saber o que roda"}
    )
    plan(nexo, scenario, copy.deepcopy(SPEC), b=b)
    assert nexo("plan", "approve", "F-1")[0] == 0
    doc = next((Path.cwd() / ".nexo" / "specs").glob("F-1-*/backlog/US-1-1.md")).read_text(encoding="utf-8")
    assert "## Valor de negócio\nsaber o que roda" in doc and "## Critérios de aceite" in doc  # squad vale, projeto não
    rejected = [e for e in json.loads(nexo("--json", "logs", "-n", "300")[1]) if e["type"] == "template.rejected"]
    assert rejected and "seção travada removida: criterios-de-aceite" in rejected[0]["payload"]["problems"]
    assert json.loads(nexo("--json", "template", "validate", str(project / ".nexo/templates/story.md"))[1])["problems"]
    assert nexo("template", "validate", str(project / ".nexo/templates/story.md"))[0] == 1


def test_story_dor_sends_backlog_back_to_the_llm(
    project: Path,
    nexo: Runner,
    scenario,
    nexo_home: Path,  # type: ignore[no-untyped-def]
) -> None:
    org_file(nexo_home, "org/config.yaml", "planning:\n  required_documents: [sdd, story]\n")
    fixed = backlog(["src/svc/version.py"], ["README.md"])
    fixed["done"]["structured"]["stories"][0].update({"as_a": "SRE", "i_want": "versão", "so_that": "operar"})
    plan(nexo, scenario, copy.deepcopy(SPEC), extra=[{"tool": "claude", "events": [fixed]}])
    code, rep = run_json(nexo, "plan", "approve", "F-1")  # 1ª quebra sem narrativa → devolvida → 2ª ok
    assert code == 0 and "backlog" in rep, rep
    assert rep["backlog"]["stories"][0]["as_a"] == "SRE"
    preview = json.loads(nexo("--json", "template", "preview", "story", "--feature", "F-1", "--item", "US-1-1")[1])
    assert preview["missing"] == [] and "**Como** SRE, **quero** versão, **para** operar." in preview["text"]


@pytest.fixture
def wiki_cloud(nexo: Runner, memory_keyring) -> Iterator[FakeCloud]:  # type: ignore[no-untyped-def]
    fake = FakeCloud()
    http.set_transport(fake)
    memory_keyring.store[("nexo-cli", "tracker")] = fake.pat
    yield fake
    http.set_transport(None)
