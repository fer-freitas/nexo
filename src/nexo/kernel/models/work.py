"""Itens de trabalho executáveis (tarefas)."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

TASK_KINDS = ("backend", "frontend", "test", "migration", "docs", "infra", "contract", "integration", "dependency")
SIZES = ("S", "M", "L")
ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


class Task(BaseModel):
    id: str
    title: str
    kind: str | None = None
    stack: str | None = None
    size: str | None = None
    description: str = ""
    acceptance: list[str] = Field(default_factory=list)
    files_hint: list[str] = Field(default_factory=list)
    section_refs: list[str] = Field(default_factory=list)  # trechos da seção técnica de que a tarefa depende
    depends_on: list[str] = Field(default_factory=list)
    data_class: str | None = None
    story: str | None = None
    human_gate: bool = False
    # atualização aplicada pelo Nexo, sem agente (manager, package, version)
    script: dict[str, str] | None = None

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        if not ID_PATTERN.match(value):
            raise ValueError("id deve começar com letra e conter apenas letras, dígitos, '-' ou '_'")
        return value

    @field_validator("kind")
    @classmethod
    def _valid_kind(cls, value: str | None) -> str | None:
        if value is not None and value not in TASK_KINDS:
            raise ValueError(f"kind inválido: {value} (permitidos: {', '.join(TASK_KINDS)})")
        return value

    @field_validator("size")
    @classmethod
    def _valid_size(cls, value: str | None) -> str | None:
        if value is not None and value not in SIZES:
            raise ValueError(f"size inválido: {value}")
        return value

    @staticmethod
    def from_mapping(data: dict[str, Any]) -> Task:
        return Task.model_validate(data)
