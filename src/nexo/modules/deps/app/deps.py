"""Dependências e vulnerabilidades em escala: varredura, plano e execução sem IA quando não há quebra.

`nexo deps scan` lê as ferramentas da stack e grava `.nexo/deps/scan.json`. `nexo deps plan` transforma os achados
numa feature (F-n) com uma história por atualização e tarefas `dependency`: o Nexo aplica a atualização com o
gerenciador da stack, o pipeline de qualidade roda os testes e a IA só entra se algo quebrar (ciclo de correção).
Versão maior tem portão humano. A decisão de gerar o plano fica na trilha (portão `deps`).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.platform import config, flags, identity
from nexo.kernel.workspace import specs
from nexo.modules.deps.adapters import scan as dependencies_scan
from nexo.modules.deps.domain.deps import DepFinding, as_dict, bump_command, from_dict, plan

SCAN_FILE = Path(".nexo") / "deps" / "scan.json"


def _root(ctx: AppContext) -> Path:
    if ctx.project_root is None:
        raise NexoError("rode dentro de um projeto Nexo (nexo init)")
    return ctx.project_root


def scan(ctx: AppContext, run: dependencies_scan.Runner | None = None) -> dict[str, Any]:
    flags.require(ctx, "deps")
    root = _root(ctx)
    managers = dependencies_scan.detect(root)
    if not managers:
        raise NexoError("nenhum gerenciador de dependências encontrado (pyproject.toml, package.json ou .csproj)")
    findings = dependencies_scan.scan(root, run)
    data = {
        "scanned_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "managers": managers,
        "findings": [as_dict(f) for f in findings],
    }
    path = root / SCAN_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    security = [f for f in findings if f.kind == "security"]
    ctx.audit(
        "deps.scanned",
        {
            "managers": managers,
            "security": len(security),
            "outdated": len(findings) - len(security),
            "critical": sum(1 for f in security if f.worst == "critical"),
        },
    )
    return data


def _load(root: Path) -> list[DepFinding]:
    path = root / SCAN_FILE
    if not path.is_file():
        raise NexoError("rode nexo deps scan antes do plano")
    return [from_dict(d) for d in json.loads(path.read_text(encoding="utf-8"))["findings"]]


def plan_feature(ctx: AppContext, major: bool = False) -> dict[str, Any]:
    """Cria a feature de atualização (spec, backlog e tarefas) pronta para `nexo run feature`."""
    flags.require(ctx, "deps")
    from nexo.kernel import services

    Planner = services.get("planning.plan", "Planner")
    from nexo.kernel.backlog import Backlog, BacklogTask, Story
    from nexo.kernel.spec.model import Requirement, Spec

    root = _root(ctx)
    cfg = dict(config.resolved(ctx).values.get("deps") or {})
    include = tuple(str(x) for x in cfg.get("include") or ("patch", "minor"))
    groups = plan(_load(root), include, major)
    if not groups:
        return {"feature": None, "stories": [], "message": "nada a atualizar pela política atual"}
    planner = Planner(ctx)
    fid = specs.next_feature_id(root)
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    title = f"Dependências e vulnerabilidades {day}"
    folder = specs.create_feature_dir(root, fid, title)
    n = fid.split("-")[1]
    stories: list[Story] = []
    tasks: list[BacklogTask] = []
    requirements: list[Requirement] = []
    for i, group in enumerate(groups, start=1):
        sid = f"US-{n}-{i}"
        stories.append(
            Story(
                id=sid,
                title=group.title,
                ac_refs=[],
                points=1 if not group.human_gate else 3,
                as_a="time de engenharia",
                i_want=group.title,
                so_that=group.reason or "manter o sistema seguro e em dia",
            )
        )
        requirements.append(Requirement(id=f"RF-{i}", title=group.title, statement=group.reason or group.title))
        for j, f in enumerate(group.findings, start=1):
            command = bump_command(f.manager, f.package, f.target)
            tasks.append(
                BacklogTask(
                    id=f"T-{n}-{i}-{j}" if len(group.findings) > 1 else f"T-{n}-{i}",
                    story=sid,
                    kind="dependency",
                    title=f"{f.package} {f.current or '?'} → {f.target}",
                    description=(
                        f"Atualização aplicada pelo Nexo: `{' '.join(command)}`. Se os testes quebrarem, o ciclo de"
                        " correção pede o ajuste mínimo no código."
                    ),
                    acceptance=[f"{f.package} na versão {f.target} ou superior", "pipeline de qualidade verde"],
                    files_hint=[],
                    size="S",
                    human_gate=group.human_gate,
                    script={"manager": f.manager, "package": f.package, "version": f.target},
                )
            )
    spec = Spec(
        id=fid,
        title=title,
        objective="Corrigir vulnerabilidades e manter as dependências em dia sem trabalho manual",
        problem="Dependências vulneráveis ou desatualizadas encontradas pelas ferramentas da stack",
        scope_in=[g.title for g in groups],
        scope_out=["mudanças de funcionalidade"],
        requirements=requirements,
        data_class=str(config.resolved(ctx).values.get("data_class") or "internal"),
        dependencies_none=True,
        risks_reviewed=True,
    )
    specs.save_spec(folder, spec)
    specs.save_questions(folder, [])
    planner.store.upsert_item(fid, "feature", title, planner.project, "local", planner.workflow.initial)
    planner._transition(fid, "plan")
    who = identity.current(ctx)
    ctx.audit(
        "gate.decision",
        {
            "gate": "deps",
            "feature": fid,
            "decision": "approved",
            "approver": who.id,
            "approver_verified": who.verified,
            "stories": len(stories),
        },
        ref=fid,
    )
    report = planner.materialize(folder, spec, Backlog(feature=fid, stories=stories, tasks=tasks))
    return {
        "feature": fid,
        "folder": str(folder),
        "stories": [{"id": s.id, "title": s.title} for s in stories],
        "human_gates": [s.id for s, g in zip(stories, groups, strict=True) if g.human_gate],
        "plan": report.__dict__,
    }


def apply_script(worktree: Path, script: dict[str, str]) -> tuple[bool, str]:
    """Aplica a atualização no worktree da tarefa com o gerenciador da stack (chamado pela execução da tarefa)."""
    import subprocess

    command = bump_command(script["manager"], script["package"], script["version"])
    if command[0] == "nexo:pip-bump":  # Pip não edita o manifesto sozinho; o Nexo edita e instala
        from nexo.modules.deps.adapters.scan import pip_bump

        return pip_bump(worktree, command[1], command[2])
    try:
        proc = subprocess.run(
            command, cwd=worktree, capture_output=True, text=True, encoding="utf-8", check=False, timeout=900
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{' '.join(command)}: {exc}"
    tail = (proc.stdout + proc.stderr)[-800:]
    return proc.returncode == 0, f"{' '.join(command)} → saída {proc.returncode}\n{tail}"
