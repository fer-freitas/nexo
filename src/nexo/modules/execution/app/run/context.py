"""Contexto entregue ao agente.

política, skills do time, prompt, tokens, mapa do código, dúvidas e pacote de handoff.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from nexo import __version__
from nexo.kernel.finops import tokens
from nexo.kernel.models.work import Task
from nexo.kernel.runtime.resources import embedded_dir
from nexo.kernel.workflow.handoff import HandoffPacket
from nexo.kernel.workspace import git
from nexo.modules.knowledge.app import content

if TYPE_CHECKING:
    from nexo.modules.execution.app.run.runner import Orchestrator


def write_policy(o: Orchestrator, run_id: str) -> None:
    """Perfil e arquivos travados lidos pelo hook nativo da execução."""
    folder = o.root / ".nexo" / "runs" / run_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "policy.json").write_text(
        json.dumps({"profile": o.policy_profile, "locked": sorted(o.opts.locked)}), encoding="utf-8"
    )


def team_skills(o: Orchestrator, tool: str, task: Task) -> str:
    """Ferramentas sem skills nativas recebem a lista de skills do time (conteúdo sob demanda via search_brain).

    só as skills da stack da tarefa (ou sem stack) entram na lista."""
    from nexo.modules.packs.app.extensions import effective

    stacks = [task.stack] if task.stack else [str(x) for x in o.cfg.get("stacks") or []]
    items = [
        e
        for e in effective(o.ctx)
        if e.meta.type == "skill"
        and tool in e.meta.targets
        and tokens.skill_fits(f"{e.meta.name} {e.meta.description}", stacks)
    ]
    if not items:
        return ""
    lines = "\n".join(f"- {e.meta.name}: {e.meta.description}" for e in items)
    return f"\n\n## Skills do time (consulte com search_brain)\n{lines}\n"


def prompt(o: Orchestrator, task: Task, packet: HandoffPacket | None) -> str:
    stacks = o.cfg.get("stacks") or []
    handoff = packet.render(content.prompt(o.ctx, "handoff", task.id)) if packet else ""
    role = o.role if o.role in ("qa-author", "characterization", "writer") else "developer"
    text = content.prompt(o.ctx, role, task.id).format(
        task_id=task.id,
        title=task.title,
        kind=task.kind or "—",
        stack=task.stack or ", ".join(stacks) or "—",
        description=task.description or "(sem descrição)",
        acceptance="\n".join(f"- {a}" for a in task.acceptance) or "- (sem critérios explícitos)",
        files_hint=("\n".join(f"- {f}" for f in task.files_hint) or "- (não informados)") + map_context(o, task),
        handoff=handoff,
        clarifications=clarifications(o, task),
    )
    from nexo.modules.planning.app import product_context

    return text + product_context.section(o.ctx, role)  # resumo do produto e do repositório


def run_tokens(o: Orchestrator, run_id: str) -> float:
    """tokens da execução até aqui (entrada nova, escrita de cache e saída)."""
    units = {"tokens_in": 0.0, "tokens_out": 0.0, "cache_write": 0.0}
    for r in o.store.usage_for_run(run_id):
        if r["unit"] in units:
            units[r["unit"]] += float(r["quantity"] or 0)
    return tokens.total_tokens(units)


def map_context(o: Orchestrator, task: Task) -> str:
    """Código existente relevante (mapa do `nexo discover`), dentro do orçamento de contexto."""
    from nexo.modules.planning.app import discover

    budget = int((o.cfg.get("brownfield") or {}).get("context_chars", 3000))
    text = discover.context_for(o.root, task.files_hint, budget) if task.files_hint else ""
    out = f"\n\nCódigo existente relacionado (mapa do repositório):\n{text}" if text else ""
    from nexo.modules.knowledge.app import graph

    linked = graph.context_for(o.ctx, task.files_hint)  # só caminhos; o agente lê se precisar
    return out + (f"\n\nLigado aos arquivos da tarefa (grafo):\n{linked}" if linked else "")


def clarifications(o: Orchestrator, task: Task) -> str:
    from nexo.kernel.workspace import specs
    from nexo.modules.planning.app import clarify

    folder = clarify.feature_for_story(o.root, task.story)
    if folder is None:
        return "- (nenhum)"
    answered = [
        x
        for x in specs.load_questions(folder)
        if x.status == "answered" and (x.source == "planner" or x.text.startswith(f"[{task.id}]"))
    ]
    return "\n".join(f"- {x.text}\n  Resposta: {x.answer}" for x in answered) or "- (nenhum)"


def skills_plugin(o: Orchestrator) -> str:
    """Plugin efêmero com as skills do Nexo para o Claude Code (carregamento sob demanda)."""
    target = o.ctx.home / "plugins" / "nexo-skills"
    skills = target / "skills"
    if skills.exists():
        shutil.rmtree(skills)
    (target / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (target / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "nexo-skills", "version": __version__, "description": "Padrões da empresa (Nexo)"}),
        encoding="utf-8",
    )
    shutil.copytree(embedded_dir("skills"), skills)
    from nexo.modules.packs.app.extensions import effective

    for ext in effective(o.ctx):  # skills do time (org/squad/projeto/pessoal) sobrepõem as embutidas
        if ext.meta.type == "skill" and "claude" in ext.meta.targets:
            dest = skills / ext.meta.name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(ext.path, dest)
    return str(target)


def packet(
    o: Orchestrator, task: Task, run_id: str, worktree: Path, base: str, reason: str, from_route: str
) -> HandoffPacket:
    return HandoffPacket.build(
        task.id,
        reason,
        from_route,
        git.head(worktree) or "",
        o.store.progress(run_id),
        git.changed_files(worktree, base),
    )


def write_packet(o: Orchestrator, run_id: str, seq: int, packet: HandoffPacket) -> Path:
    folder = o.root / ".nexo" / "runs" / run_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"handoff-{seq}.md"
    body = f"# Handoff {seq} — tarefa {packet.task_id}\n\nDe: {packet.from_route}\n"
    path.write_text(body + packet.render(content.prompt(o.ctx, "handoff", run_id)), encoding="utf-8")
    return path
