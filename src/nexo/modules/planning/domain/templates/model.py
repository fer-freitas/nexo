"""Templates de artefatos: cabeçalho YAML + corpo Mustache; seções identificadas pelo título.

A seção `## 4. Critérios de aceite` tem id `criterios-de-aceite` (sem numeração, sem acento, minúsculas).
O cabeçalho declara `required_sections` (não podem ficar vazias no documento renderizado),
`locked_sections` (a org trava: squad e projeto não removem), DoR/DoD em texto e o mapeamento para o tracker.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from nexo.modules.planning.domain.templates import mustache

TYPES = ("epic", "feature", "sdd", "functional-requirements", "technical-design", "story", "task", "bug", "adr")
HEADING = re.compile(r"(?m)^(#{2,3})\s+(.+?)\s*$")


def slug(title: str) -> str:
    text = re.sub(r"^\d+(\.\d+)*\.?\s*", "", title.strip())
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def sections(markdown: str) -> dict[str, str]:
    """id da seção → corpo (sem comentários HTML), para títulos `##` e `###` fora de blocos de código."""
    clean = re.sub(r"```.*?```", lambda m: "\n" * m.group(0).count("\n"), markdown, flags=re.S)
    heads = list(HEADING.finditer(clean))
    out: dict[str, str] = {}
    for i, h in enumerate(heads):
        if h.group(1) != "##":
            continue
        end = next((x.start() for x in heads[i + 1 :] if x.group(1) == "##"), len(markdown))
        body = markdown[h.end() : end]
        out[slug(h.group(2))] = re.sub(r"<!--.*?-->", "", body, flags=re.S).strip()
    return out


@dataclass
class Template:
    type: str
    header: dict[str, Any]
    body: str
    origin: str = "embutido"
    problems: list[str] = field(default_factory=list)

    @property
    def required(self) -> list[str]:
        return [str(s) for s in self.header.get("required_sections") or []]

    @property
    def locked(self) -> list[str]:
        return [str(s) for s in self.header.get("locked_sections") or []]

    @property
    def dor(self) -> list[str]:
        return [str(s) for s in self.header.get("dor") or []]

    @property
    def dod(self) -> list[str]:
        return [str(s) for s in self.header.get("dod") or []]

    def section_ids(self) -> list[str]:
        return list(sections(self.body))

    def render(self, data: dict[str, Any]) -> str:
        text = mustache.render(self.body, data)
        return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"

    def missing(self, rendered: str) -> list[str]:
        found = sections(rendered)
        return [s for s in self.required if not found.get(s)]


def parse(text: str, origin: str = "embutido", expected_type: str | None = None) -> Template:
    from ruamel.yaml import YAML

    problems: list[str] = []
    header: dict[str, Any] = {}
    body = text
    m = re.match(r"(?s)^---\n(.*?)\n---\n(.*)$", text)
    if m:
        try:
            header = dict(YAML(typ="safe").load(m.group(1)) or {})
        except Exception as exc:
            problems.append(f"cabeçalho YAML inválido: {exc}")
        body = m.group(2)
    else:
        problems.append("cabeçalho `---` ausente")
    kind = str(header.get("nexo_template") or header.get("nexo_template") or expected_type or "")
    if kind not in TYPES:
        problems.append(f"nexo_template desconhecido: {kind or '—'}")
    elif expected_type and kind != expected_type:
        problems.append(f"nexo_template {kind} ≠ {expected_type}")
    problems += mustache.check(body)
    tpl = Template(kind, header, body, origin, problems)
    present = set(tpl.section_ids())
    for sid in [*tpl.required, *tpl.locked]:
        if sid not in present:
            problems.append(f"seção declarada e ausente no corpo: {sid}")
    return tpl


def override_problems(base: Template, override: Template) -> list[str]:
    """Um template de nível acima (squad/projeto) não remove seção travada nem obrigatória travada."""
    present = set(override.section_ids())
    missing = [s for s in base.locked if s not in present]
    problems = [f"seção travada removida: {s}" for s in missing]
    problems += [
        f"seção travada deixou de ser obrigatória: {s}"
        for s in base.locked
        if s in base.required and s not in override.required
    ]
    return problems
