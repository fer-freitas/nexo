"""Tipos, constantes e funções da execução da história usados por mais de um módulo do pacote."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nexo.kernel.context import AppContext
from nexo.modules.planning.app.clarify import feature_for_story

UI = Callable[[str, dict[str, Any]], None]


TEST_HINTS = ("tests/", "test/", ".Tests/", ".test.", ".spec.", "test_")


REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["approve", "changes", "reject"]},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                    "file": {"type": "string"},
                    "line": {"type": "integer"},
                    "ac_ref": {"type": "string"},
                    "cwe": {"type": "string"},
                    "message": {"type": "string"},
                },
                "required": ["severity", "message"],
            },
        },
        "ac_coverage": {"type": "array", "items": {"type": "object"}},
        "spec_issue": {"type": "boolean"},
    },
    "required": ["verdict", "findings"],
}


TRIAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"class": {"type": "string", "enum": ["code", "spec", "env"]}, "rationale": {"type": "string"}},
    "required": ["class"],
}


def _noop(_: str, __: dict[str, Any]) -> None:
    return None


def is_test_file(path: str) -> bool:
    return any(h in path for h in TEST_HINTS)


def is_test_task(task: Any) -> bool:
    """Tarefa de testes do plano (`kind: test`): no test-first ela é do QA, que escreve os testes antes e os trava
    (o dev não pode alterá-los). Tarefa de dev que cria um arquivo de teste novo continua com o dev."""
    return getattr(task, "kind", "") == "test"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass
class StoryReport:
    story: str
    state: str
    reason: str = ""
    run_id: str | None = None
    branch: str | None = None
    worktree: str | None = None
    tasks: dict[str, str] = field(default_factory=dict)
    quality: list[dict[str, Any]] = field(default_factory=list)
    traceability: list[dict[str, Any]] = field(default_factory=list)
    gate: dict[str, Any] | None = None
    bugs: list[str] = field(default_factory=list)
    attempts: int = 0
    cost_brl: float | None = None
    cap_brl: float | None = None
    projection_brl: float | None = None
    estimate_brl: float | None = None
    cost_gate: dict[str, Any] | None = None


def is_story(ctx: AppContext, target: str) -> bool:
    root = ctx.project_root
    return bool(root and feature_for_story(root, target))


MANIFESTS = (
    "pyproject.toml",
    "uv.lock",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements.in",
    "requirements-dev.in",
    "package.json",
    "package-lock.json",
    "packages.lock.json",
    "Directory.Packages.props",
)


def _is_manifest(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name in MANIFESTS or name.endswith(".csproj")
