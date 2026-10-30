"""Formato canônico de extensões: skill, hook, tool (MCP)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, field_validator

TYPES = ("skill", "hook", "tool")
SCOPES = ("personal", "project", "squad", "org")
TARGETS = ("claude", "copilot", "devin")
HOOK_EVENTS = ("session_start", "pre_tool", "post_edit", "post_step", "pre_commit", "task_done")
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
DATA_CLASSES = ("public", "internal", "confidential", "restricted")


class HookSpec(BaseModel):
    event: str
    matcher: str = "*"
    runtime: str = "python"
    timeout_ms: int = 5000
    on_error: str = "deny"

    @field_validator("event")
    @classmethod
    def _event(cls, v: str) -> str:
        if v not in HOOK_EVENTS:
            raise ValueError(f"evento de hook inválido: {v} (use {', '.join(HOOK_EVENTS)})")
        return v

    @field_validator("on_error")
    @classmethod
    def _on_error(cls, v: str) -> str:
        if v not in ("deny", "allow", "pause"):
            raise ValueError("on_error deve ser deny, allow ou pause")
        return v


class ExtensionMeta(BaseModel):
    name: str
    type: str
    version: str = "0.1.0"
    scope: str = "project"
    owner: str = ""
    description: str = ""
    data_class: str = "internal"
    targets: list[str] = Field(default_factory=lambda: list(TARGETS))
    hook: HookSpec | None = None
    locked: bool = False
    signature: str = ""
    rollout: Any = "all"  # "all" ou {"squads": [...]} — canário por squad

    def rolled_out_to(self, squad: str | None) -> bool:
        if self.rollout in (None, "all"):
            return True
        squads = self.rollout.get("squads") if isinstance(self.rollout, dict) else None
        return isinstance(squads, list) and squad in squads

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not NAME.match(v):
            raise ValueError("nome deve ser kebab-case (a-z, 0-9, -), 2 a 64 caracteres")
        return v

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        if v not in TYPES:
            raise ValueError(f"tipo inválido: {v}")
        return v

    @field_validator("scope")
    @classmethod
    def _scope(cls, v: str) -> str:
        if v not in SCOPES:
            raise ValueError(f"escopo inválido: {v}")
        return v

    @field_validator("targets")
    @classmethod
    def _targets(cls, v: list[str]) -> list[str]:
        bad = [t for t in v if t not in TARGETS]
        if bad:
            raise ValueError(f"alvos inválidos: {bad}")
        return v

    @field_validator("data_class")
    @classmethod
    def _dc(cls, v: str) -> str:
        if v not in DATA_CLASSES:
            raise ValueError(f"classe de dados inválida: {v}")
        return v


@dataclass(frozen=True)
class Extension:
    meta: ExtensionMeta
    path: str  # pasta da extensão
    content: str  # texto principal (SKILL.md, hook.py ou mcp.json) usado em varredura e similaridade

    @property
    def key(self) -> str:
        return f"{self.meta.type}:{self.meta.name}"


def main_file(ext_type: str) -> str:
    return {"skill": "SKILL.md", "hook": "hook.py", "tool": "mcp.json"}[ext_type]


def frontmatter_description(skill_md: str) -> str:
    match = re.search(r"^---\s*\n(.*?)\n---", skill_md, re.S)
    if not match:
        return ""
    desc = re.search(r"^description:\s*(.+)$", match.group(1), re.M)
    return desc.group(1).strip() if desc else ""


# ---------------- similaridade


def shingles(text: str, k: int = 5) -> set[str]:
    words = re.findall(r"\w+", text.lower())
    if len(words) < k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + k]) for i in range(len(words) - k + 1)}


def similarity(a: str, b: str) -> float:
    sa, sb = shingles(a), shingles(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def most_similar(candidate: str, others: list[Extension], threshold: float = 0.5) -> list[tuple[Extension, float]]:
    scored = [(o, similarity(candidate, o.content)) for o in others]
    return sorted([s for s in scored if s[1] >= threshold], key=lambda s: -s[1])
