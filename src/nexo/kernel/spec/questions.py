"""Dúvidas do refinamento."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

SEVERITIES = ("blocking", "important", "optional")
MAX_PER_ROUND = 10


class Question(BaseModel):
    id: str
    text: str
    severity: str = "important"
    section: str = ""
    status: str = "open"  # open | answered | dismissed
    answer: str = ""
    source: str = "planner"  # planner | agent


def _norm(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def open_questions(qs: list[Question]) -> list[Question]:
    return [q for q in qs if q.status == "open"]


def blocking_open(qs: list[Question]) -> int:
    return sum(1 for q in qs if q.status == "open" and q.severity == "blocking")


def merge_new(existing: list[Question], proposed: list[dict[str, Any]]) -> list[Question]:
    """Acrescenta dúvidas novas sem repetir as já feitas e limita por rodada."""
    seen = {_norm(q.text) for q in existing}
    next_n = max([int(q.id.split("-")[1]) for q in existing if re.fullmatch(r"Q-\d+", q.id)] or [0]) + 1
    ranked = sorted(
        proposed,
        key=lambda p: SEVERITIES.index(p.get("severity", "important")) if p.get("severity") in SEVERITIES else 1,
    )
    added: list[Question] = []
    for p in ranked:
        text = str(p.get("text", "")).strip()
        if not text or _norm(text) in seen:
            continue
        severity = p.get("severity") if p.get("severity") in SEVERITIES else "important"
        added.append(
            Question(
                id=f"Q-{next_n}",
                text=text,
                severity=str(severity),
                section=str(p.get("section", "")),
                source=str(p.get("source", "planner")),
            )
        )
        seen.add(_norm(text))
        next_n += 1
        if len(added) >= MAX_PER_ROUND:
            break
    return [*existing, *added]


def answer(qs: list[Question], qid: str, text: str) -> list[Question]:
    found = False
    out = []
    for q in qs:
        if q.id == qid:
            found = True
            q = q.model_copy(update={"answer": text, "status": "answered"})
        out.append(q)
    if not found:
        raise ValueError(f"dúvida {qid} não encontrada")
    return out
