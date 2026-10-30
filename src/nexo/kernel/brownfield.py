"""Código existente: regras puras para baseline de achados, cobertura do diff, hotspots, convenções locais e
seleção de contexto a partir do mapa do repositório. Nenhum I/O aqui."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any


def fingerprint(stage: str, rule: str, file: str, message: str) -> str:
    """Identidade estável de um achado, sem número de linha (código em volta muda, o achado continua o mesmo)."""
    msg = re.sub(r"\d+", "#", message.strip().lower())
    return hashlib.sha256(f"{stage}|{rule}|{file.replace(chr(92), '/')}|{msg}".encode()).hexdigest()[:16]


def diff_coverage(changed: dict[str, set[int]], covered: dict[str, dict[int, int]]) -> tuple[float | None, int, int]:
    """Cobertura das linhas alteradas que são executáveis (presentes no relatório). None = nada mensurável."""
    total = hit = 0
    for path, lines in changed.items():
        report = next((v for k, v in covered.items() if path.endswith(k) or k.endswith(path)), None)
        if report is None:
            continue
        for n in lines:
            if n in report:
                total += 1
                hit += report[n] > 0
    return (round(100 * hit / total, 1) if total else None), hit, total


@dataclass
class FileInfo:
    path: str
    lang: str
    loc: int
    symbols: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    complexity: int = 0
    churn: int = 0
    owner: str = ""
    coverage: float | None = None
    is_test: bool = False

    @property
    def hotspot(self) -> int:
        return self.churn * max(1, self.complexity)


def hotspots(files: list[FileInfo], limit: int = 10) -> list[FileInfo]:
    candidates = [f for f in files if not f.is_test and f.churn and f.complexity]
    return sorted(candidates, key=lambda f: (-f.hotspot, f.path))[:limit]


def _case(name: str) -> str:
    if re.fullmatch(r"[a-z][a-z0-9]*(_[a-z0-9]+)+", name):
        return "snake_case"
    if re.fullmatch(r"[a-z][a-z0-9]*([A-Z][a-z0-9]*)+", name):
        return "camelCase"
    if re.fullmatch(r"([A-Z][a-z0-9]+){2,}", name):
        return "PascalCase"
    return ""


def conventions(files: list[FileInfo], configs: dict[str, bool], test_frameworks: list[str]) -> dict[str, Any]:
    """Convenções reais do repositório (o que o código faz, não o que a org recomenda)."""
    by_lang: dict[str, Counter[str]] = {}
    for f in files:
        if f.is_test:
            continue
        counter = by_lang.setdefault(f.lang, Counter())
        for s in f.symbols:
            name = s.split("(")[0].split(" ")[-1]
            style = _case(name)
            if style:
                counter[style] += 1
    naming = {lang: c.most_common(1)[0][0] for lang, c in by_lang.items() if c}
    tests = [f.path for f in files if f.is_test]
    test_dirs = Counter(p.split("/")[0] for p in tests if "/" in p)
    return {
        "naming": naming,
        "test_frameworks": test_frameworks,
        "test_dir": test_dirs.most_common(1)[0][0] if test_dirs else "",
        "tools": sorted(k for k, v in configs.items() if v),
        "src_layout": any(f.path.startswith("src/") for f in files),
    }


def related(files: list[FileInfo], targets: list[str]) -> list[FileInfo]:
    """Arquivos alvo + quem eles importam + quem importa eles (vizinhança de 1 nível)."""
    by_path = {f.path: f for f in files}
    stems = {t: t.rsplit("/", 1)[-1].rsplit(".", 1)[0] for t in targets}
    chosen: dict[str, FileInfo] = {t: by_path[t] for t in targets if t in by_path}
    for f in files:
        if f.path in chosen or f.is_test:
            continue
        if any(
            stem and any(stem == i.split(".")[-1] or i.endswith(f"/{stem}") for i in f.imports)
            for stem in stems.values()
        ):
            chosen[f.path] = f
    for t in list(chosen.values()):
        for imp in t.imports:
            last = imp.split(".")[-1].split("/")[-1]
            for f in files:
                if f.path not in chosen and f.path.rsplit("/", 1)[-1].rsplit(".", 1)[0] == last and not f.is_test:
                    chosen[f.path] = f
    return list(chosen.values())


def context_text(files: list[FileInfo], budget_chars: int = 3000) -> str:
    """Resumo do código relevante para o agente, dentro do orçamento de contexto."""
    parts: list[str] = []
    used = 0
    for f in files:
        cov = f" · cobertura {f.coverage:.0f}%" if f.coverage is not None else " · sem cobertura medida"
        head = f"- `{f.path}` ({f.lang}, {f.loc} linhas{cov})"  # sem nomes de autores: dado pessoal não vai ao LLM
        syms = "".join(f"\n  - {s}" for s in f.symbols[:12])
        block = head + syms
        if used + len(block) > budget_chars:
            parts.append(f"- … {len(files) - len(parts)} arquivo(s) omitido(s) pelo orçamento de contexto")
            break
        parts.append(block)
        used += len(block)
    return "\n".join(parts)
