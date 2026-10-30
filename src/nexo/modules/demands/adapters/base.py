"""Porta de tracker."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from nexo.kernel.tracker.mapping import Mapping, ProcessInfo


@dataclass
class TrackerItem:
    local_id: str
    type: str  # epic | feature | story | task | bug
    title: str
    state: str  # estado Nexo
    description: str = ""
    parent_ref: str | None = None  # referência externa do pai
    fields: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ExternalRef:
    ref: str  # id na ferramenta (ex.: "124", "PROJ-7", "#12")
    url: str


@dataclass(frozen=True)
class ExternalState:
    ref: str
    state: str  # estado externo
    tags: tuple[str, ...]
    changed_at: str


@dataclass(frozen=True)
class HistoricItem:
    """Item fechado no período, para a linha de base antes do Nexo."""

    ref: str
    kind: str  # story | bug | other
    created_at: str
    closed_at: str
    activated_at: str | None = None
    points: float | None = None
    nexo: bool = False  # passou pelo Nexo (tag/label `nexo`)


class Tracker(Protocol):
    name: str
    mapping: Mapping

    def create(self, item: TrackerItem) -> ExternalRef: ...

    def transition(self, ref: str, nexo_state: str, reason: str = "") -> None: ...

    def comment(self, ref: str, text: str) -> None: ...

    def fetch(self, refs: list[str]) -> list[ExternalState]: ...

    def describe_process(self) -> ProcessInfo | None: ...

    def history(self, since: str, until: str) -> list[HistoricItem]: ...
