"""Geração determinística de Gherkin (pt) a partir dos critérios estruturados."""

from __future__ import annotations

import re

from nexo.kernel.spec.model import Scenario


def _steps(keyword: str, items: list[str]) -> list[str]:
    return [f"    {keyword if i == 0 else 'E'} {text.strip()}" for i, text in enumerate(items)]


def render_feature(story_id: str, title: str, scenarios: list[Scenario]) -> str:
    lines = ["# language: pt", f"@{story_id}", f"Funcionalidade: {title}", ""]
    for sc in scenarios:
        lines.append(f"  @{sc.id}")
        lines.append(f"  Cenário: {sc.title}")
        lines += _steps("Dado", sc.given)
        lines += _steps("Quando", sc.when)
        lines += _steps("Então", sc.then)
        lines.append("")
    return "\n".join(lines)


def lint_feature(text: str) -> list[str]:
    """Checagem estrutural: todo cenário tem tag @AC, Quando e Então."""
    problems: list[str] = []
    blocks = re.split(r"\n\s*(?=@AC-\d+\n\s*Cenário:)", text)
    if "Funcionalidade:" not in text:
        problems.append("sem 'Funcionalidade:'")
    for block in blocks[1:]:
        tag = block.strip().splitlines()[0]
        if not re.search(r"^\s*Quando ", block, re.M):
            problems.append(f"{tag}: sem passo 'Quando'")
        if not re.search(r"^\s*Então ", block, re.M):
            problems.append(f"{tag}: sem passo 'Então'")
    return problems
