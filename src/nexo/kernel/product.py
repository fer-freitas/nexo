"""Contexto do projeto: repositórios interligados que formam um projeto e os componentes que eles dividem.

Um projeto tem **visões** (sistemas que o usuário vê, ex.: carteira passiva, custódia banco, custódia cliente) e
**componentes** compartilhados (bibliotecas, serviços) que as visões consomem. Cada parte vive num repositório
próprio ou numa pasta de um monorepo; squads diferentes podem cuidar de partes diferentes.

O projeto fica no cérebro da org (`projetos/<id>/projeto.yaml`, `contexto.md`, `glossario.md`): vale para todos os
squads. Cada repositório diz a que projeto e a que parte pertence (`context.project`/`context.part` na configuração) e
guarda o que é só dele em `.nexo/contexto.md`.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
KIND_LABEL = {"visao": "visão", "componente": "componente compartilhado", "externo": "sistema externo"}
KINDS = ("visao", "componente", "externo")


class Part(BaseModel):
    """Uma parte do projeto: visão, componente (num repositório ou numa pasta de monorepo) ou sistema externo
    (fora do projeto, sem repositório: entra no desenho da solução)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["visao", "componente", "externo"]
    name: str = ""
    description: str = ""
    repo: str = ""  # ex.: org/custodia-cliente (vazio: mesmo repositório do projeto, ver `path`)
    path: str = ""  # monorepo: pasta da parte dentro do repositório
    squads: list[str] = Field(default_factory=list)
    uses: list[str] = Field(default_factory=list)  # componentes e sistemas externos que esta parte consome
    aliases: list[str] = Field(default_factory=list)  # outros nomes pelos quais o time cita a parte

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not ID.match(value):
            raise ValueError(f"id inválido: {value!r} (minúsculas, números e hífen)")
        return value

    @property
    def label(self) -> str:
        return self.name or self.id


class Product(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str = ""
    description: str = ""
    squads: list[str] = Field(default_factory=list)
    parts: list[Part] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not ID.match(value):
            raise ValueError(f"id inválido: {value!r} (minúsculas, números e hífen)")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Product:
        ids = [p.id for p in self.parts]
        repeated = sorted({i for i in ids if ids.count(i) > 1})
        if repeated:
            raise ValueError(f"parte repetida: {', '.join(repeated)}")
        usable = {p.id for p in self.parts if p.kind != "visao"}
        for part in self.parts:
            unknown = [u for u in part.uses if u not in usable]
            if unknown:
                raise ValueError(f"{part.id} usa {', '.join(unknown)}, que não é componente nem sistema externo")
            if part.id in part.uses:
                raise ValueError(f"{part.id} não pode usar a si mesmo")
        return self

    @property
    def label(self) -> str:
        return self.name or self.id

    def part(self, part_id: str) -> Part | None:
        return next((p for p in self.parts if p.id == part_id), None)

    def consumers(self, component_id: str) -> list[Part]:
        return [p for p in self.parts if component_id in p.uses]

    def squads_of(self, parts: list[Part]) -> list[str]:
        return sorted({s for p in parts for s in p.squads})


class Impact(BaseModel):
    """Quem mais é afetado por uma mudança: componentes tocados, partes que os consomem e os squads delas."""

    components: list[str] = Field(default_factory=list)
    affected: list[str] = Field(default_factory=list)
    squads: list[str] = Field(default_factory=list)

    @property
    def any(self) -> bool:
        return bool(self.affected)


def mentioned(product: Product, text: str) -> list[Part]:
    """Componentes citados no texto (id, nome ou apelido como palavra inteira, sem diferenciar maiúsculas)."""
    lowered = text.lower()
    found = []
    for part in product.parts:
        if part.kind != "componente":
            continue
        names = {part.id, part.name, *part.aliases} - {""}
        if any(re.search(rf"(?<![\w-]){re.escape(n.lower())}(?![\w-])", lowered) for n in names):
            found.append(part)
    return found


def impact(product: Product, here: str, text: str) -> Impact:
    """Impacto cruzado de uma feature feita na parte `here`.

    - Num componente: toda mudança pode afetar quem o consome.
    - Numa visão: os componentes citados na feature (que ela usa) afetam as outras partes que também os usam.
    """
    part = product.part(here)
    if part is None:
        return Impact()
    touched = [part] if part.kind == "componente" else mentioned(product, text)
    affected: dict[str, Part] = {}
    for component in touched:
        for consumer in product.consumers(component.id):
            if consumer.id != here:
                affected[consumer.id] = consumer
    parts = list(affected.values())
    return Impact(
        components=[c.label for c in touched],
        affected=[p.label for p in parts],
        squads=product.squads_of(parts),
    )


def describe(product: Product, here: str) -> str:
    """Resumo curto da posição do repositório no projeto (vai para o prompt e para o `nexo context`)."""
    part = product.part(here)
    lines = [f"Projeto: {product.label}" + (f" — {product.description}" if product.description else "")]
    visions = [p.label for p in product.parts if p.kind == "visao"]
    components = [p for p in product.parts if p.kind == "componente"]
    external = [p.label for p in product.parts if p.kind == "externo"]
    if visions:
        lines.append(f"Visões: {', '.join(visions)}")
    if external:
        lines.append(f"Sistemas externos: {', '.join(external)}")
    for c in components:
        users = ", ".join(u.label for u in product.consumers(c.id)) or "nenhuma parte ainda"
        lines.append(f"Componente {c.label}: {c.description or 'sem descrição'} (usado por {users})")
    if part is not None:
        what = f": {part.description}" if part.description else ""
        lines.append(f"Este repositório é {KIND_LABEL[part.kind]} {part.label}{what}")
        if part.uses:
            names = ", ".join((product.part(u) or Part(id=u, kind="componente")).label for u in part.uses)
            lines.append(f"Usa os componentes: {names}")
        if part.kind == "componente":
            users = ", ".join(u.label for u in product.consumers(part.id)) or "ninguém ainda"
            lines.append(f"Atenção: mudanças aqui afetam {users}; preserve a compatibilidade do que é público.")
    return "\n".join(lines)
