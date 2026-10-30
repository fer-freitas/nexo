"""De-para entre estados/tipos do Nexo e da ferramenta de gestão."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

NX_STATES = (
    "Ideia",
    "Refinamento",
    "Pronto",
    "EmDev",
    "EmQA",
    "EmReview",
    "AguardandoHumano",
    "Concluido",
    "Bloqueado",
    "Escalado",
    "Cancelado",
)
NX_TYPES = ("epic", "feature", "story", "task", "bug")
TAG_PREFIX = "nexo-"


@dataclass(frozen=True)
class StateTarget:
    state: str | None  # None = manter o estado externo atual
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProcessInfo:
    name: str
    types: dict[str, list[str]]  # tipo externo → estados
    categories: dict[str, str] = field(default_factory=dict)  # estado externo → categoria (Proposed/InProgress/...)


@dataclass(frozen=True)
class Mapping:
    tracker: str
    types: dict[str, str]
    states: dict[str, StateTarget]
    reverse: dict[str, str]
    fields: dict[str, str] = field(default_factory=dict)

    @staticmethod
    def from_mapping(data: dict[str, Any]) -> Mapping:
        states: dict[str, StateTarget] = {}
        for nexo_state, raw in (data.get("states") or {}).items():
            raw = raw or {}
            states[str(nexo_state)] = StateTarget(
                None if raw.get("keep_state") else str(raw["state"]) if raw.get("state") else None,
                tuple(str(t) for t in raw.get("tags") or ()),
            )
        return Mapping(
            tracker=str(data.get("tracker", "")),
            types={str(k): str(v) for k, v in (data.get("types") or {}).items()},
            states=states,
            reverse={str(k): str(v) for k, v in (data.get("reverse") or {}).items()},
            fields={str(k): str(v) for k, v in (data.get("fields") or {}).items()},
        )

    def target(self, nexo_state: str) -> StateTarget:
        if nexo_state not in self.states:
            raise KeyError(f"estado {nexo_state} sem de-para para {self.tracker}")
        return self.states[nexo_state]

    def external_type(self, nexo_type: str) -> str:
        if nexo_type not in self.types:
            raise KeyError(f"tipo {nexo_type} sem de-para para {self.tracker}")
        return self.types[nexo_type]

    def merge_tags(self, current: list[str], nexo_state: str) -> list[str]:
        """Remove as tags que o de-para usa para estados (e as `nexo-*` antigas), preserva as do time e adiciona
        as do novo estado. Ex.: no GitLab, `workflow::ready` sai quando a história passa a `workflow::in review`."""
        managed = {t for target in self.states.values() for t in target.tags}
        keep = [t for t in current if not t.startswith(TAG_PREFIX) and t not in managed]
        return [*keep, *[t for t in self.target(nexo_state).tags if t not in keep]]

    def nexo_state(self, external_state: str, tags: list[str]) -> str | None:
        """Estado Nexo a partir do externo: tags do Nexo têm prioridade (mais específicas)."""
        tagged = [
            s
            for s, t in self.states.items()
            if t.tags and set(t.tags) <= set(tags) and (t.state is None or t.state == external_state)
        ]
        if tagged:
            return max(tagged, key=lambda s: len(self.states[s].tags))
        plain = [s for s, t in self.states.items() if not t.tags and t.state == external_state]
        if plain:
            return plain[0]
        return self.reverse.get(external_state)


def check(mapping: Mapping, process: ProcessInfo | None, required_states: tuple[str, ...] = NX_STATES) -> list[str]:
    problems = [f"estado Nexo sem de-para: {s}" for s in required_states if s not in mapping.states]
    problems += [f"tipo Nexo sem de-para: {t}" for t in NX_TYPES if t not in mapping.types]
    if process is not None:
        for nexo_type, ext_type in mapping.types.items():
            if ext_type not in process.types:
                problems.append(f"tipo '{ext_type}' ({nexo_type}) não existe no processo {process.name}")
                continue
            valid = set(process.types[ext_type])
            for nexo_state, target in mapping.states.items():
                if target.state and target.state not in valid:
                    problems.append(f"estado '{target.state}' ({nexo_state}) não existe em '{ext_type}'")
    return problems


CATEGORY_FOR = {
    "Ideia": "Proposed",
    "Refinamento": "Proposed",
    "Pronto": "Proposed",
    "EmDev": "InProgress",
    "EmQA": "InProgress",
    "EmReview": "Resolved",
    "AguardandoHumano": "Resolved",
    "Concluido": "Completed",
    "Cancelado": "Removed",
}


def suggest(process: ProcessInfo, tracker: str, type_names: dict[str, str]) -> dict[str, Any]:
    """Sugere um de-para a partir das categorias de estado do processo (modo automático, 4.2)."""
    story_type = type_names.get("story", next(iter(process.types), ""))
    states = process.types.get(story_type, [])
    by_cat: dict[str, str] = {}
    for s in states:
        by_cat.setdefault(process.categories.get(s, ""), s)
    first = states[0] if states else "New"
    out: dict[str, Any] = {}
    for nexo_state in NX_STATES:
        cat = CATEGORY_FOR.get(nexo_state)
        ext = by_cat.get(cat or "") or (by_cat.get("InProgress") if cat == "Resolved" else None) or first
        tags = (
            [f"{TAG_PREFIX}{nexo_state.lower()}"]
            if nexo_state in ("Refinamento", "Pronto", "EmQA", "AguardandoHumano")
            else []
        )
        if nexo_state in ("Bloqueado", "Escalado"):
            out[nexo_state] = {"keep_state": True, "tags": [f"{TAG_PREFIX}{nexo_state.lower()}"]}
        else:
            out[nexo_state] = {"state": ext, **({"tags": tags} if tags else {})}
    reverse = {
        s: n
        for n, cat in CATEGORY_FOR.items()
        for s in states
        if process.categories.get(s) == cat and n in ("Pronto", "EmDev", "EmReview", "Concluido", "Cancelado")
    }
    return {"tracker": tracker, "types": type_names, "states": out, "reverse": reverse}
