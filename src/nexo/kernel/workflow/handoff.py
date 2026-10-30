"""Pacote de handoff: tudo que a próxima rota precisa, sem histórico bruto."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProgressEntry:
    done: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    percent: int | None = None


@dataclass
class HandoffPacket:
    task_id: str
    reason: str
    from_route: str
    checkpoint: str
    done: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)

    @staticmethod
    def build(
        task_id: str, reason: str, from_route: str, checkpoint: str, progress: list[ProgressEntry], changed: list[str]
    ) -> HandoffPacket:
        packet = HandoffPacket(task_id, reason, from_route, checkpoint, changed_files=sorted(set(changed)))
        for entry in progress:
            packet.done.extend(x for x in entry.done if x not in packet.done)
            packet.decisions.extend(x for x in entry.decisions if x not in packet.decisions)
            packet.blockers.extend(x for x in entry.blockers if x not in packet.blockers)
        if progress:
            packet.next_steps = list(progress[-1].next_steps)
        return packet

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    def render(self, template: str) -> str:
        def bullets(items: list[str]) -> str:
            return "\n".join(f"- {i}" for i in items) if items else "- (nada registrado)"

        return template.format(
            reason=self.reason,
            checkpoint=self.checkpoint[:12] or "—",
            done=bullets(self.done),
            decisions=bullets(self.decisions),
            next_steps=bullets(self.next_steps),
            blockers=bullets(self.blockers),
            changed_files=bullets(self.changed_files),
        )
