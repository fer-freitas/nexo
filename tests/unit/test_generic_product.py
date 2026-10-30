"""produto genérico: nada específico de uma empresa no código, nas skills e na documentação."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# termos de um cliente específico; a versão dele vive na branch e na tag próprias
FORBIDDEN = re.compile(r"(?i)\bita[uú]\b|\bpipflow\b|iu[ -]?click|\bgmud\b|selo low")
SCANNED = ("src", "docs", "spec", "README.md", "CHANGELOG.md", "scripts")


def test_no_company_specific_terms() -> None:
    hits = []
    for base in SCANNED:
        path = ROOT / base
        files = [path] if path.is_file() else [p for p in path.rglob("*") if p.is_file()]
        for f in files:
            if "apresentacao" in f.parts or f.suffix not in {".py", ".md", ".yaml", ".yml", ".txt", ".json"}:
                continue
            for i, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if FORBIDDEN.search(line) and "test_generic_product" not in f.name:
                    hits.append(f"{f.relative_to(ROOT)}:{i}: {line.strip()[:80]}")
    assert hits == []
