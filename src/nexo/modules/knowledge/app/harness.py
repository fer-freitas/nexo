"""Casos de harness: o que o motor faz quando o trabalho para no meio ou o agente sai do combinado.

Cada caso roda numa cópia descartável, com agente simulado e sem custo. Prova o comportamento do Nexo, não o do
modelo. O caso compara o estado real dos arquivos, em vez de confiar no que o agente relatou.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexo.kernel import services
from nexo.kernel.runtime.resources import copy_sample_project
from nexo.kernel.runtime.yamlfile import dump

TASK = {
    "title": "Adicionar função version()",
    "kind": "backend",
    "stack": "python",
    "description": "Criar src/svc/version.py com version() retornando 0.1.0",
    "acceptance": ["version() retorna 0.1.0"],
    "files_hint": ["src/svc/version.py"],
}
GOOD = "def version() -> str:\n    return '0.1.0'\n"
LIMITED = {"rate_limited": {"reset_in": 600}}
DONE = {"progress": {"done": ["feito"], "percent": 100}}


def _edit(path: str, content: str = GOOD) -> dict[str, Any]:
    return {"edit": {"path": path, "content": content}}


def _recipe() -> dict[str, Any]:
    return {
        "stack": "python",
        "stages": [
            {
                "id": "unit",
                "kind": "unit",
                "tool": sys.executable,
                "run": [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--junitxml={out}/junit.xml"],
                "parser": "junit",
                "report": "{out}/junit.xml",
                "required": True,
            }
        ],
    }


class Lab:
    """Um repositório e uma pasta do Nexo descartáveis. Nada toca a configuração real da pessoa."""

    def __init__(self, base: Path, steps: list[dict[str, Any]], tasks: tuple[str, ...] = ("T-1",)) -> None:
        self.base = base
        self.project = base / "projeto"
        self.home = base / "home"
        self.project.mkdir(parents=True)
        copy_sample_project(self.project)
        dump(base / "scenario.yaml", {"tools": {"claude": {}}, "steps": steps})
        dump(self.home / "brain" / "recipes" / "python.yaml", _recipe())
        dump(self.home / "brain" / "recipes" / "common.yaml", {"stages": []})
        dump(self.home / "brain" / "org" / "config.yaml", {"identity": {"mode": "os"}, "guard": {"auto": False}})
        self.env = {
            **os.environ,
            "NX_HOME": str(self.home),
            "NX_FAKE_SCENARIO": str(base / "scenario.yaml"),
            "NX_ACTOR": "harness",
            "GIT_AUTHOR_NAME": "Harness",
            "GIT_AUTHOR_EMAIL": "harness@exemplo.invalid",
            "GIT_COMMITTER_NAME": "Harness",
            "GIT_COMMITTER_EMAIL": "harness@exemplo.invalid",
            "PYTHONIOENCODING": "utf-8",
        }
        self.git("init", "-q")
        self.nexo("init")
        for task in tasks:
            dump(self.project / ".nexo" / "tasks" / f"{task}.yaml", TASK)
        self.git("add", "-A")
        self.git("commit", "-qm", "init")

    def git(self, *args: str, cwd: Path | None = None) -> str:
        done = subprocess.run(
            ["git", *args],
            cwd=cwd or self.project,
            env=self.env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
        return done.stdout

    def nexo(self, *args: str) -> tuple[int, dict[str, Any], str]:
        command = list(services.get("execution.agents", "nexo_command")())
        done = subprocess.run(
            [*command, "--json", *args],
            cwd=self.project,
            env=self.env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        try:
            data = json.loads(done.stdout or "{}")
        except ValueError:
            data = {}
        return done.returncode, data if isinstance(data, dict) else {}, done.stderr

    def renew_quota(self) -> None:
        db = sqlite3.connect(self.home / "nexo.db")
        try:
            db.execute("UPDATE route_state SET limited_until = ?", (time.time() - 1,))
            db.execute("UPDATE runs SET blocked_until = ?", (time.time() - 1,))
            db.commit()
        finally:
            db.close()

    def worktree(self) -> Path:
        db = sqlite3.connect(self.home / "nexo.db")
        try:
            return Path(db.execute("SELECT worktree FROM runs ORDER BY started_at DESC").fetchone()[0])
        finally:
            db.close()

    def fingerprint(self) -> str:
        """Conteúdo do checkout: mudou um byte, muda o resultado."""
        digest = hashlib.sha256()
        for path in sorted(p for p in self.project.rglob("*") if p.is_file()):
            parts = path.relative_to(self.project).parts
            if parts[0] in (".git", ".nexo") or "__pycache__" in parts:
                continue
            digest.update("/".join(parts).encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()


def _stale_handoff(base: Path) -> tuple[bool, str]:
    """O repasse antigo não sabe de um commit feito depois da parada. Quem retoma precisa receber o estado real."""
    lab = Lab(
        base,
        [
            {"tool": "claude", "events": [_edit("parcial.py", "x = 1\n"), LIMITED]},
            {"tool": "claude", "expect_prompt": "depois-da-parada.py", "events": [_edit("src/svc/version.py"), DONE]},
        ],
    )
    _code, first, _err = lab.nexo("run", "task", "T-1")
    if first.get("state") != "Bloqueado":
        return False, f"esperava parar por cota, ficou em {first.get('state')}"
    worktree = lab.worktree()
    (worktree / "depois-da-parada.py").write_text("y = 2\n", encoding="utf-8")
    lab.git("add", "-A", cwd=worktree)
    lab.git("commit", "-qm", "mudança feita depois da parada", cwd=worktree)
    lab.renew_quota()
    _code, second, err = lab.nexo("resume", "T-1")
    if second.get("state") != "Concluido":
        return False, f"a retomada não usou o estado real: {second.get('reason') or err}"[:200]
    return True, "a retomada recebeu o commit feito depois da parada"


def _stop_and_resume(base: Path) -> tuple[bool, str]:
    lab = Lab(
        base,
        [
            {"tool": "claude", "events": [_edit("parcial.py", "x = 1\n"), LIMITED]},
            {"tool": "claude", "events": [_edit("src/svc/version.py"), DONE]},
        ],
    )
    _code, first, _err = lab.nexo("run", "task", "T-1")
    if first.get("state") != "Bloqueado":
        return False, f"esperava parar por cota, ficou em {first.get('state')}"
    _code, early, _err = lab.nexo("resume", "T-1")
    if "aguardando cota" not in str(early.get("reason")):
        return False, "retomou antes de a cota renovar"
    lab.renew_quota()
    _code, last, err = lab.nexo("resume", "T-1")
    if last.get("state") != "Concluido":
        return False, f"não concluiu depois de retomar: {last.get('reason') or err}"[:200]
    if not (Path(str(last["worktree"])) / "parcial.py").is_file():
        return False, "o trabalho anterior à parada se perdeu"
    return True, "esperou a cota, retomou e manteve o trabalho anterior"


def _partial_block(base: Path) -> tuple[bool, str]:
    """Uma tarefa bloqueada não pode travar a outra, nem ser dada como pronta por causa dela."""
    question = {"clarification": {"question": "Qual a assinatura?", "blocking": True}}
    lab = Lab(
        base,
        [
            {"tool": "claude", "task": "T-1", "events": [_edit("so-do-t1.py", "x = 1\n"), question]},
            {"tool": "claude", "task": "T-2", "events": [_edit("src/svc/version.py"), DONE]},
        ],
        ("T-1", "T-2"),
    )
    _code, one, _err = lab.nexo("run", "task", "T-1")
    _code, two, err = lab.nexo("run", "task", "T-2")
    if one.get("state") == "Concluido":
        return False, "a tarefa com pergunta em aberto foi dada como pronta"
    if two.get("state") != "Concluido":
        return False, f"a tarefa livre travou junto: {two.get('reason') or err}"[:200]
    if (Path(str(two["worktree"])) / "so-do-t1.py").exists():
        return False, "o trabalho da tarefa bloqueada vazou para a outra"
    return True, f"T-1 ficou em {one.get('state')} e T-2 concluiu separada"


def _read_only_review(base: Path) -> tuple[bool, str]:
    """Agente que escreve numa etapa de leitura: o Nexo recusa o resultado, e a comparação confirma o que houve."""
    lab = Lab(base, [{"tool": "claude", "events": [_edit("invadido.py", "x = 1\n")]}])
    before = lab.fingerprint()
    code, _data, err = lab.nexo("context", "enrich", "--yes")
    changed = lab.fingerprint() != before
    if code == 0 or "só de leitura" not in err:
        return False, "o resultado de quem escreveu numa etapa de leitura foi aceito"
    if not changed:
        return False, "o simulador não chegou a escrever: o caso não provou nada"
    return True, "a escrita foi detectada e o resultado, recusado"


CASES: dict[str, tuple[str, Callable[[Path], tuple[bool, str]]]] = {
    "handoff-desatualizado": ("Handoff desatualizado", _stale_handoff),
    "parada-e-retomada": ("Parada e retomada", _stop_and_resume),
    "bloqueio-parcial": ("Bloqueio parcial", _partial_block),
    "revisao-somente-leitura": ("Revisão somente leitura", _read_only_review),
}


@dataclass
class CaseResult:
    id: str
    title: str
    ok: bool
    detail: str
    seconds: float


def run(only: list[str] | None = None) -> list[CaseResult]:
    results = []
    for cid in only or list(CASES):
        title, case = CASES[cid]
        start = time.monotonic()
        base = Path(tempfile.mkdtemp(prefix="nexo-harness-"))
        try:
            ok, detail = case(base)
        except (OSError, subprocess.CalledProcessError, KeyError, ValueError, sqlite3.Error) as exc:
            ok, detail = False, f"o caso não chegou ao fim: {exc}"[:200]
        finally:
            shutil.rmtree(base, ignore_errors=True)
        results.append(CaseResult(cid, title, ok, detail, round(time.monotonic() - start, 1)))
    return results
