"""Perguntas sobre o código e o cérebro: `nexo ask "<pergunta>"` responde com as fontes.

1. Busca determinística: trechos de código (caminho, símbolos e ocorrências dos termos) e decisões (ADRs do
   repositório e das features, especificações e conteúdo do cérebro), dentro de um orçamento de caracteres.
2. Um agente só de leitura responde em formato estruturado, citando as fontes.
3. O Nexo confere as fontes: caminho que não existe no repositório nem no cérebro é descartado; sem fonte
   válida, a resposta sai com confiança baixa. Nada é escrito no repositório.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from nexo.kernel import services
from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.platform import config, flags
from nexo.kernel.security.redaction import redact
from nexo.kernel.workspace import codemap
from nexo.modules.knowledge.app import brain, content

llm = services.load("execution.llm")

STOP = frozenset(
    {
        *("como", "onde", "quando", "qual", "quais", "quem", "porque", "para", "pelo", "pela", "pelos", "pelas"),
        *("uma", "umas", "uns", "com", "sem", "que", "isso", "esse", "essa", "este", "esta", "aqui", "funciona"),
        *("fazer", "feito", "the", "and", "for", "with", "what", "where", "how", "does", "from", "into"),
    }
)
DOC_GLOBS = ("spec/adr/*.md", "docs/adr/*.md", ".nexo/specs/*/adr/*.md", ".nexo/specs/*/spec.md", "docs/*.md")

ASK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "lines": {"type": "string"}, "why": {"type": "string"}},
                "required": ["path"],
            },
        },
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "unknown": {"type": "boolean"},
    },
    "required": ["answer", "sources", "confidence"],
}


def terms(question: str) -> list[str]:
    words = [w for w in re.findall(r"[\wÀ-ú]{3,}", question.lower()) if w not in STOP]
    return list(dict.fromkeys(words))[:12]


def _excerpt(text: str, words: list[str], radius: int = 3, limit: int = 3) -> tuple[int, str]:
    lines = text.splitlines()
    hits = [i for i, ln in enumerate(lines) if any(w in ln.lower() for w in words)]
    if not hits:
        return 0, ""
    blocks: list[str] = []
    used: set[int] = set()
    for i in hits[:limit]:
        lo, hi = max(0, i - radius), min(len(lines), i + radius + 1)
        if any(j in used for j in range(lo, hi)):
            continue
        used.update(range(lo, hi))
        blocks.append(f"[linhas {lo + 1}-{hi}]\n" + "\n".join(lines[lo:hi]))
    return len(hits), "\n".join(blocks)


def retrieve(root: Path, question: str, budget: int = 9000) -> dict[str, Any]:
    """Código e decisões mais ligados à pergunta, dentro do orçamento (sem IA)."""
    words = terms(question)
    if not words:
        raise NexoError("pergunta sem termos para buscar")
    scored: list[tuple[int, str, str]] = []
    for rel in codemap.working_files(root):
        if rel.startswith(".nexo/") or Path(rel).suffix not in codemap.LANGS:
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hits, excerpt = _excerpt(text, words)
        path_score = sum(3 for w in words if w in rel.lower())
        if hits or path_score:
            scored.append((hits + path_score, rel, excerpt))
    scored.sort(key=lambda x: (-x[0], x[1]))
    code_parts, used = [], 0
    for _, rel, excerpt in scored:
        block = f"### {rel}\n{excerpt}\n"
        if used + len(block) > budget * 2 // 3:
            break
        code_parts.append(block)
        used += len(block)
    docs: list[tuple[int, str, str]] = []
    for pattern in DOC_GLOBS:
        for path in root.glob(pattern):
            text = path.read_text(encoding="utf-8", errors="replace")
            hits, excerpt = _excerpt(text, words, radius=2, limit=2)
            if hits:
                docs.append((hits, path.relative_to(root).as_posix(), excerpt))
    docs.sort(key=lambda x: (-x[0], x[1]))
    doc_parts, used = [], 0
    for _, rel, excerpt in docs:
        block = f"### {rel}\n{excerpt}\n"
        if used + len(block) > budget // 3:
            break
        doc_parts.append(block)
        used += len(block)
    return {
        "terms": words,
        "code": [rel for _, rel, _ in scored[: len(code_parts)]],
        "docs": [rel for _, rel, _ in docs[: len(doc_parts)]],
        "code_text": "\n".join(code_parts),
        "doc_text": "\n".join(doc_parts),
    }


def ask(ctx: AppContext, question: str, budget: int = 9000) -> dict[str, Any]:
    flags.require(ctx, "ask")
    root = ctx.project_root
    if root is None:
        raise NexoError("rode dentro de um repositório com o Nexo (nexo init)")
    q = redact(question.strip())
    if len(q) < 5:
        raise NexoError("pergunta muito curta")
    found = retrieve(root, q, budget)
    lessons = brain.search(ctx, q, limit=4)
    brain_text = "\n".join(f"- cérebro:{r['path']}: {r['excerpt']}" for r in lessons) or "- nenhuma"
    prompt = content.prompt(ctx, "explainer").format(
        question=q,
        code=found["code_text"] or "- nenhum trecho encontrado",
        decisions=(found["doc_text"] or "- nenhuma") + "\n\n## Cérebro\n" + brain_text,
    )
    result = llm.call_structured(
        ctx,
        "explainer",
        "ask",
        prompt,
        ASK_SCHEMA,
        root,
        facts={"kind": "planning"},
        data_class=str(config.resolved(ctx).values.get("data_class") or "confidential"),
        read_files=True,
    )
    brain_paths = {f"cérebro:{r['path']}" for r in lessons}
    valid: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for src in result.data.get("sources") or []:
        path = str(src.get("path") or "").strip()
        rel = path.removeprefix("./")
        ok = path in brain_paths or (rel and not rel.startswith("/") and ".." not in rel and (root / rel).is_file())
        (valid if ok else dropped).append({**src, "path": rel if not path.startswith("cérebro:") else path})
    confidence = str(result.data.get("confidence") or "low")
    if not valid:
        confidence = "low"
    ctx.audit(
        "ask.answered",
        {
            "terms": found["terms"],
            "sources": len(valid),
            "dropped": len(dropped),
            "confidence": confidence,
            "run": result.run_id or "",
        },
    )
    return {
        "question": q,
        "answer": str(result.data.get("answer") or ""),
        "sources": valid,
        "dropped_sources": [d["path"] for d in dropped],
        "confidence": confidence,
        "unknown": bool(result.data.get("unknown")),
        "searched": {"code": found["code"], "docs": found["docs"], "brain": [r["path"] for r in lessons]},
    }
