"""Motor de máquina de status declarativa."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nexo.kernel.errors import NexoError


class InvalidTransition(NexoError):
    pass


@dataclass(frozen=True)
class Transition:
    event: str
    sources: frozenset[str]
    target: str


@dataclass(frozen=True)
class Workflow:
    name: str
    initial: str
    states: frozenset[str]
    terminal: frozenset[str]
    transitions: tuple[Transition, ...]

    @staticmethod
    def from_mapping(data: dict[str, Any]) -> Workflow:
        states = frozenset(str(s) for s in data["states"])
        transitions: list[Transition] = []
        for raw in data["transitions"]:
            sources = frozenset(str(s) for s in raw["from"])
            target = str(raw["to"])
            unknown = (sources | {target}) - states
            if unknown:
                raise ValueError(f"workflow {data['name']}: estados desconhecidos {sorted(unknown)}")
            transitions.append(Transition(str(raw["event"]), sources, target))
        initial = str(data["initial"])
        terminal = frozenset(str(s) for s in data.get("terminal", []))
        if initial not in states or not terminal <= states:
            raise ValueError(f"workflow {data['name']}: estado inicial/terminal inválido")
        return Workflow(str(data["name"]), initial, states, terminal, tuple(transitions))

    def next_state(self, current: str, event: str) -> str:
        for tr in self.transitions:
            if tr.event == event and current in tr.sources:
                return tr.target
        raise InvalidTransition(f"transição inválida no workflow '{self.name}': {current} --{event}-->")

    def can(self, current: str, event: str) -> bool:
        return any(tr.event == event and current in tr.sources for tr in self.transitions)

    def events_from(self, current: str) -> list[str]:
        return sorted({tr.event for tr in self.transitions if current in tr.sources})
