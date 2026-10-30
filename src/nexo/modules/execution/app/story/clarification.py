"""Pergunta do agente durante a execução da história: responder e retomar dali (achado na gravação real).

O agente pode parar e perguntar (ex.: a assinatura de uma função que a spec não define). A história vai para
Refinamento até a resposta. Se a pergunta foi respondida e os critérios de aceite da história continuam na spec, a
retomada volta à execução sem reaprovar o plano; se o refinamento mudou os critérios, aí sim o plano é revisto.
"""

from __future__ import annotations

from pathlib import Path

from nexo.kernel.spec import questions as q
from nexo.kernel.workspace import specs


def _story_tags(folder: Path, story_id: str) -> tuple[str, ...]:
    backlog = specs.load_backlog(folder)
    tasks = [t.id for t in backlog.tasks if t.story == story_id] if backlog else []
    return tuple(f"[{x}" for x in (story_id, *tasks))  # "[US-1-1-QA]", "[T-1-2]", "[US-1-1]"


def agent_questions(folder: Path, story_id: str) -> list[q.Question]:
    tags = _story_tags(folder, story_id)
    return [x for x in specs.load_questions(folder) if x.source == "agent" and x.text.startswith(tags)]


def open_agent_questions(folder: Path, story_id: str) -> list[q.Question]:
    return [x for x in agent_questions(folder, story_id) if x.status == "open"]


def missing_criteria(folder: Path, story_id: str) -> list[str]:
    """Critérios da história que sumiram da spec (o refinamento mudou o requisito)."""
    backlog = specs.load_backlog(folder)
    story = next((s for s in backlog.stories if s.id == story_id), None) if backlog else None
    known = {a.id for a in specs.load_spec(folder).acceptance}
    return [ac for ac in (story.ac_refs if story else []) if ac not in known]


def resumable(folder: Path | None, story_id: str) -> tuple[bool, str]:
    """(pode retomar, o que falta). Só vale para história parada por pergunta do agente."""
    if folder is None or not agent_questions(folder, story_id):
        return False, "responda as dúvidas (plan answer) e aprove o plano de novo"
    pending = open_agent_questions(folder, story_id)
    if pending:
        ids = ", ".join(x.id for x in pending)
        fid = "-".join(folder.name.split("-")[:2])
        return False, f"responda a pergunta do agente ({ids}) com `plan answer {fid}`"
    gone = missing_criteria(folder, story_id)
    if gone:
        return False, f"o refinamento mudou os critérios ({', '.join(gone)}): aprove o plano de novo"
    return True, ""
