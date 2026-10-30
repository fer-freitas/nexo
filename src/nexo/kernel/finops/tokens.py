"""Economia de tokens: regras determinísticas, sem IA.

- Cache dos modelos: o provedor reaproveita o começo do prompt que se repete entre chamadas. Por isso a parte fixa
  (papel, regras, formato) vem antes da parte variável (tarefa, código, falhas); `static_prefix` mede isso no modelo
  do prompt e `cache_hit` mede o resultado no consumo real.
- Saída de ferramenta resumida: só a linha do erro e os primeiros quadros do código do projeto chegam ao agente.
- Teto por tarefa: total de tokens da execução comparado ao limite configurado.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

PLACEHOLDER = re.compile(r"\{[a-z_]{3,}\}")  # campos do modelo; `{n}` de exemplo não conta
ERROR_LINE = re.compile(
    r"^\s*(E\s+|[\w.]*(Error|Exception|Failure)\b|AssertionError|assert\s|Expected|Received|expected|error\s|"
    r"FAILED|●|\s*at\s.+:\d+)",
)
FRAME = re.compile(r'(File "([^"]+)", line (\d+)|\bat .+?\(?([\w./\\-]+):(\d+)(?::\d+)?\)?$|([\w./\\-]+\.\w+):(\d+))')
NOISE = ("site-packages", "node_modules", "/usr/lib", "\\Microsoft.NET\\", "<frozen", "_pytest", "pluggy")


STACK_WORDS: dict[str, tuple[str, ...]] = {
    "python": ("python", "pytest", "django", "fastapi", "flask"),
    "dotnet": ("dotnet", ".net", "c#", "csharp", "asp.net", "xunit", "nuget"),
    "react": ("react", "typescript", "javascript", "node", "jest", "npm", "next.js"),
    # só termos próprios da stack ("bloc", "provider", "widget" e "signal" aparecem em textos gerais)
    "java": ("java", "spring", "maven", "gradle", "junit", "kotlin", "jpa", "hibernate"),
    "angular": ("angular", "rxjs", "ngrx", "karma", "jasmine"),
    "flutter": ("flutter", "dart", "riverpod", "pubspec"),
    "go": ("golang",),
}


def _cites(text: str, word: str) -> bool:
    """Palavra inteira para termos alfabéticos ("java" não casa com "javascript"); `.net`, `c#` por trecho."""
    if word.isalpha():
        return re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", text) is not None
    return word in text


def skill_fits(text: str, stacks: list[str]) -> bool:
    """Skill sem stack citada vale para todas; skill de outra stack fica fora do prompt."""
    low = f" {text.lower()} "
    cited = {k for k, words in STACK_WORDS.items() if any(_cites(low, w) for w in words)}
    return not cited or not stacks or bool(cited & {x.lower() for x in stacks})


def static_prefix(template: str) -> float:
    """Fração do modelo antes do primeiro campo variável: quanto maior, mais o cache do modelo aproveita."""
    text = template.strip()
    if not text:
        return 0.0
    m = PLACEHOLDER.search(text)
    return 1.0 if m is None else round(m.start() / len(text), 3)


def cache_hit(usage: Mapping[str, Any]) -> float | None:
    """Parte da entrada servida pelo cache: lido do cache / (entrada nova + lido + escrito). None sem dado."""
    read = float(usage.get("cache_read") or 0)
    total = float(usage.get("tokens_in") or 0) + read + float(usage.get("cache_write") or 0)
    return round(read / total, 3) if total > 0 else None


def total_tokens(usage: Mapping[str, Any]) -> float:
    """Tokens que contam para o teto: entrada nova, escrita de cache e saída (leitura de cache é barata e fica fora)."""
    return (
        float(usage.get("tokens_in") or 0) + float(usage.get("cache_write") or 0) + float(usage.get("tokens_out") or 0)
    )


def over_cap(used: float, cap: float | None) -> bool:
    return bool(cap) and used > float(cap or 0)


def condense(output: str, max_chars: int = 600, frames: int = 3) -> str:
    """Resume a saída de um teste ou ferramenta: linhas de erro e quadros do projeto, sem ruído de bibliotecas."""
    text = (output or "").strip()
    if len(text) <= max_chars:
        return text
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    first = lines[0] if lines else ""
    errors = [ln.strip() for ln in lines if ERROR_LINE.match(ln) and not FRAME.search(ln)]
    own = [ln.strip() for ln in lines if FRAME.search(ln) and not any(n in ln for n in NOISE)]
    kept: list[str] = []
    for ln in [first, *errors[:4], *own[-frames:]]:
        if ln and ln not in kept:
            kept.append(ln)
    out = "\n".join(kept)
    if len(out) > max_chars:
        out = out[: max_chars - 1] + "…"
    omitted = len(lines) - len(kept)
    return out + (f"\n[… {omitted} linha(s) omitida(s)]" if omitted > 0 else "")
