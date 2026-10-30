"""`nexo demo cli`: um repositório descartável, com uma ideia já registrada e um agente simulado."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from nexo.kernel.errors import NexoError
from nexo.kernel.runtime.resources import embedded_dir
from nexo.kernel.runtime.yamlfile import dump

WALKTHROUGH = (
    ("nexo pm", "o que está com a PM"),
    ("nexo pm refine F-1", "a IA simulada enriquece a spec"),
    ("nexo pm approve F-1", "a PM aprova os requisitos"),
    ("nexo tl approve F-1", "o tech lead aprova o desenho e as histórias nascem"),
    ("nexo tl assign F-1 --dev os:voce --qa os:voce", "o tech lead indica quem faz"),
    ("nexo board", "as demandas e com quem cada uma está"),
    ("nexo qa plan US-1-1", "o QA liga cada critério a um cenário de teste"),
    ("nexo qa approve US-1-1", "o QA aprova o plano de teste"),
    ("nexo dev approve US-1-1", "o dev aprova o plano e a história fica pronta"),
    ("nexo dev run US-1-1", "testes, implementação, qualidade e portão"),
    ("nexo tl gate US-1-1", "o porquê do portão"),
    ("nexo approve US-1-1", "a decisão humana libera a entrega"),
    ('nexo try "cria um arquivo"', "teste livre, sem demanda"),
)


def _nexo() -> list[str]:
    from nexo.kernel import services

    return list(services.get("execution.agents", "nexo_command")())


def env_for(base: Path) -> dict[str, str]:
    actor = "voce"
    return {
        **os.environ,
        "NX_HOME": str(base / "home"),
        "NX_FAKE_SCENARIO": str(base / "scenario.yaml"),
        "NX_ACTOR": actor,
        "GIT_AUTHOR_NAME": "Demo",
        "GIT_AUTHOR_EMAIL": "demo@exemplo.invalid",
        "GIT_COMMITTER_NAME": "Demo",
        "GIT_COMMITTER_EMAIL": "demo@exemplo.invalid",
        "PYTHONIOENCODING": "utf-8",
    }


def _copy_project(target: Path) -> None:
    source = embedded_dir("selftest") / "python-svc"
    for path in source.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts:
            rel = path.relative_to(source).as_posix().replace("gitignore.txt", ".gitignore")
            dest = target / rel.removesuffix(".txt")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(path.read_bytes())


def _recipe() -> dict[str, Any]:
    """A receita curta da demonstração: dispensa as ferramentas que o `nexo setup` instala de verdade."""
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


def prepare(folder: Path | None = None) -> dict[str, Any]:
    """Prepara tudo numa pasta só. Nada toca o histórico, o custo nem as configurações reais da pessoa."""
    base = folder or Path(tempfile.mkdtemp(prefix="nexo-demo-"))
    if folder is not None and folder.exists() and any(folder.iterdir()):
        raise NexoError(f"{folder} não está vazia")
    base.mkdir(parents=True, exist_ok=True)
    project = base / "projeto"
    project.mkdir()
    _copy_project(project)
    shutil.copyfile(embedded_dir("demo") / "scenario.yaml", base / "scenario.yaml")
    home = base / "home"
    dump(home / "brain" / "recipes" / "python.yaml", _recipe())
    dump(home / "brain" / "recipes" / "common.yaml", {"stages": []})
    dump(
        home / "brain" / "org" / "config.yaml",
        {
            "identity": {"mode": "os"},
            "gates": {"allow_self_approval": True},
            "planning": {"role_flow": {"enabled": True, "joint_session": "never", "allow_same_person": True}},
        },
    )
    env = env_for(base)

    def run(*args: str) -> None:
        done = subprocess.run(
            args, cwd=project, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
        )
        if done.returncode != 0:
            raise NexoError(f"{' '.join(args[:3])}: {(done.stderr or done.stdout).strip()[:300]}")

    run("git", "init", "-q", "-b", "main")
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", "projeto de exemplo")
    nexo = _nexo()
    run(*nexo, "init")
    dump(
        project / ".nexo" / "team" / "team.yaml",
        {"mode": "solo", "tl": "os:voce", "members": [{"id": "os:voce", "roles": ["pm", "tl", "qa", "dev"]}]},
    )
    run(*nexo, "squad", "set", "sq-demo")
    run(*nexo, "context", "init", "--standalone", "--what", "Serviço de exemplo", "--users", "Times internos")
    run(*nexo, "pm", "idea", "Consultar a versão implantada")
    run(*nexo, "pm", "decide", "IDEA-1", "build", "--reason", "Reduzir o tempo de diagnóstico em incidentes")
    run(*nexo, "pm", "split", "IDEA-1", "--feature", "Endpoint de versão")
    return {
        "folder": str(base),
        "project": str(project),
        "pytest": importlib.util.find_spec("pytest") is not None,
        "walkthrough": [{"command": c, "what": w} for c, w in WALKTHROUGH],
    }


def console(base: Path) -> int:
    return subprocess.run([*_nexo(), "console"], cwd=base / "projeto", env=env_for(base), check=False).returncode
