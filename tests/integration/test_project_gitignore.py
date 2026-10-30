"""O que o Nexo grava dentro do projeto: o que é local ou gerado fica fora do git; o resto é versionado."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tests.conftest import Runner


def ignored(root: Path, rel: str) -> bool:
    return subprocess.run(["git", "check-ignore", "-q", rel], cwd=root, check=False).returncode == 0


def test_local_folders_ignored_and_project_records_versioned(project: Path) -> None:
    for rel in (
        ".nexo/runs/run-1/story.json",
        ".nexo/worktrees/T-1/x.py",
        ".nexo/quality/latest/quality-report.json",
        ".nexo/quality/standards/standards.sarif",
        ".nexo/deps/scan.json",
        ".nexo/evals/out/E-1/result.json",
        ".nexo/tracker/outbox.csv",
        ".nexo/evidencias/F-1/dados.json",
        ".nexo/tasks/US-1-1-QA.yaml",
        ".nexo/tasks/US-1-1-FIX2.yaml",
        ".nexo/tasks/T-1-1-INT.yaml",
    ):
        assert ignored(project, rel), rel
    for rel in (
        ".nexo/nexo.yaml",
        ".nexo/specs/F-1-x/spec.md",
        ".nexo/tasks/T-1-1.yaml",
        ".nexo/bugs/B-1.yaml",
        ".nexo/standards.yaml",
        ".nexo/evals/golden/E-1.yaml",
        ".nexo/metrics/baseline.json",
    ):
        assert not ignored(project, rel), rel


def test_old_project_gets_missing_lines_and_keeps_its_own(project: Path, nexo: Runner) -> None:
    path = project / ".nexo/.gitignore"
    path.write_text("runs/\nworktrees/\nmap/\n*.tmp\nminha-pasta/\n", encoding="utf-8")  # versão antiga + do time
    doctor = json.loads(nexo("--json", "doctor")[1])
    check = next(c for c in doctor["checks"] if c["name"] == "gitignore")
    assert check["status"] == "warn" and "quality/" in check["detail"]
    assert nexo("init")[0] == 0
    text = path.read_text(encoding="utf-8")
    assert "minha-pasta/" in text and "quality/" in text and text.count("runs/") == 1
    assert nexo("init")[0] == 0 and path.read_text(encoding="utf-8") == text  # idempotente
