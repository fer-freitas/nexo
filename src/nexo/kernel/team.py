"""Time do projeto: pessoas, papéis e modo (`team` ou `solo`), lidos de `team.yaml`.

O papel organiza os comandos e valida aprovações; não restringe leitura. Em `solo`, uma pessoa pode ter todos os
papéis e aprovar o que fez, com o registro na trilha. Os portões continuam iguais, e a IA nunca aprova.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nexo.kernel.runtime import yamlfile
from nexo.kernel.workspace.specs import project_settings

ROLE_ALIASES = {"tech_lead": "tl", "tl": "tl", "pm": "pm", "qa": "qa", "dev": "dev"}


@dataclass(frozen=True)
class Member:
    id: str
    roles: tuple[str, ...]


@dataclass
class Team:
    name: str = ""
    mode: str = "team"
    tl: str = ""
    members: list[Member] = field(default_factory=list)
    declared: bool = False

    def roles_of(self, who: str) -> list[str]:
        return [r for m in self.members if m.id == who for r in m.roles]

    def people(self, role: str) -> list[str]:
        return [m.id for m in self.members if role in m.roles]

    @property
    def solo(self) -> bool:
        return self.mode == "solo"


def parse(data: dict[str, Any]) -> Team:
    members = [
        Member(str(m["id"]), tuple(ROLE_ALIASES.get(str(r), str(r)) for r in m.get("roles") or []))
        for m in data.get("members") or []
    ]
    return Team(
        name=str(data.get("name", "")),
        mode=str(data.get("mode", "team")),
        tl=str(data.get("tl", "")),
        members=members,
        declared=bool(data),
    )


def team_file(project_root: Path | None) -> Path | None:
    if project_root is None:
        return None
    return project_settings(project_root).team_dir() / "team.yaml"


def load(project_root: Path | None) -> Team:
    path = team_file(project_root)
    return parse(yamlfile.load(path)) if path and path.is_file() else Team()
