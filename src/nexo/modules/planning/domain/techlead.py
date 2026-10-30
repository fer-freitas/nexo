"""Trabalho do tech lead: analisar o requisito funcional e escrever o requisito técnico com premissas.

- **Análise**: o que falta ou está ambíguo no requisito, riscos técnicos, o que é afetado e as perguntas que voltam
  para o PM (viram dúvidas da feature, respondidas no fluxo normal).
- **Requisito técnico**: premissas (o que se assume como verdade, por quê e o que acontece se não for), decisões,
  design, requisitos não funcionais, riscos, dependências e estratégia de teste. O design vai para a spec (a mesma
  que o dev e os agentes usam) e um documento legível fica junto da feature.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from nexo.kernel.spec.model import Spec

SEVERITY_LABEL = {"blocking": "bloqueia", "important": "importante", "optional": "opcional"}
EFFORT_LABEL = {"P": "pequeno", "M": "médio", "G": "grande"}


class Gap(BaseModel):
    text: str
    severity: Literal["blocking", "important", "optional"] = "important"
    suggestion: str = ""  # lacuna técnica: decisão ou premissa recomendada


class TechRisk(BaseModel):
    title: str
    mitigation: str = ""


class Analysis(BaseModel):
    summary: str
    gaps: list[Gap] = Field(default_factory=list)  # técnicas: o tech lead decide
    functional_gaps: list[Gap] = Field(default_factory=list)  # escaparam do refinamento; o tech lead decide se devolve
    risks: list[TechRisk] = Field(default_factory=list)
    affected: list[str] = Field(default_factory=list)  # áreas, módulos, componentes do produto
    effort: Literal["P", "M", "G"] = "M"
    ready_for_tech: bool = False  # dá para escrever o requisito técnico agora?


class Premise(BaseModel):
    text: str
    why: str = ""
    if_false: str = ""  # o que muda se a premissa não se confirmar


def render_analysis(spec: Spec, a: Analysis) -> str:
    lines = [f"# Análise técnica — {spec.id} {spec.title}", "", a.summary, ""]
    verdict = "pronto para o requisito técnico" if a.ready_for_tech else "há lacuna funcional para decidir"
    lines += [f"**Esforço**: {EFFORT_LABEL[a.effort]} · **Situação**: {verdict}", ""]
    if a.gaps:
        lines += ["## Lacunas técnicas (decisão do tech lead)", ""]
        for g in a.gaps:
            lines.append(f"- ({SEVERITY_LABEL[g.severity]}) {g.text}")
            if g.suggestion:
                lines.append(f"  - Sugestão: {g.suggestion}")
        lines.append("")
    if a.functional_gaps:
        lines += ["## Lacunas funcionais que escaparam do refinamento", ""]
        lines += [f"- ({SEVERITY_LABEL[g.severity]}) {g.text}" for g in a.functional_gaps]
        lines += ["", "O tech lead decide: assumir como premissa no requisito técnico ou devolver ao PM.", ""]
    if a.risks:
        lines += ["## Riscos técnicos", ""]
        lines += [f"- {r.title}" + (f" — mitigação: {r.mitigation}" if r.mitigation else "") for r in a.risks] + [""]
    if a.affected:
        lines += ["## O que é afetado", ""] + [f"- {x}" for x in a.affected] + [""]
    return "\n".join(lines)
