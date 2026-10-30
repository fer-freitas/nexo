"""Código genérico: a identidade da empresa fica só na marca e na documentação, nunca no código nem nas skills."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# termos de um cliente específico; a versão dele vive na branch e na tag próprias
FORBIDDEN = re.compile(r"(?i)\bita[uú]\b|\bpipflow\b|iu[ -]?click|\bgmud\b|selo low")
SCANNED = ("src", "scripts")
BRAND = "brand.yaml"


def test_no_company_specific_terms() -> None:
    hits = []
    for base in SCANNED:
        path = ROOT / base
        files = [path] if path.is_file() else [p for p in path.rglob("*") if p.is_file()]
        for f in files:
            if f.name == BRAND or f.suffix not in {".py", ".md", ".yaml", ".yml", ".txt", ".json"}:
                continue
            for i, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if FORBIDDEN.search(line) and "test_generic_product" not in f.name:
                    hits.append(f"{f.relative_to(ROOT)}:{i}: {line.strip()[:80]}")
    assert hits == []
