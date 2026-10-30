"""Classificação de dados da feature: a IA sugere, uma pessoa decide.

A classe define o que pode ir para as ferramentas de IA (`data_policy`). Achado no uso real: o planejador marcou
uma feature de login como `restricted` sozinho; a política padrão não permite nenhuma ferramenta para essa classe e
o próprio planejamento travou. Agora:

- a IA só muda a classe quando isso não muda o que pode ir para a IA (mesmos fornecedores na `data_policy`); o resto
  vira dúvida da feature (bloqueante quando restringe), com o que cada classe implica;
- a resposta a essa dúvida (ou `nexo plan classify F-n <classe>`) aplica a classe e fica na auditoria.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.spec import questions as q
from nexo.kernel.spec.model import DATA_CLASSES, Spec
from nexo.kernel.workspace import specs

SECTION = "data_class"
ALIASES = {
    "public": "public",
    "publica": "public",
    "pública": "public",
    "internal": "internal",
    "interna": "internal",
    "confidential": "confidential",
    "confidencial": "confidential",
    "restricted": "restricted",
    "restrita": "restricted",
    "restrito": "restricted",
}


def rank(value: str) -> int:
    return DATA_CLASSES.index(value) if value in DATA_CLASSES else DATA_CLASSES.index("confidential")


def _allowed(ctx: AppContext, value: str) -> frozenset[str] | None:
    """Fornecedores permitidos para a classe (None = todos)."""
    from nexo.kernel.platform import config

    allowed = (config.resolved(ctx).values.get("data_policy") or {}).get(value)
    return None if allowed is None or "*" in allowed else frozenset(str(v) for v in allowed)


def _effects(ctx: AppContext, value: str) -> str:
    from nexo.kernel.platform import config

    allowed = (config.resolved(ctx).values.get("data_policy") or {}).get(value)
    if allowed is None or "*" in allowed:
        return "qualquer ferramenta de IA pode trabalhar nela"
    if not allowed:
        return "nenhuma ferramenta de IA pode trabalhar nela (tudo manual)"
    return f"só ferramentas destes fornecedores: {', '.join(allowed)}"


def hold(ctx: AppContext, before: Spec, after: Spec, qs: list[q.Question]) -> tuple[Spec, list[q.Question]]:
    """Segura a classe proposta pela IA e pergunta à pessoa, a não ser que seja a mesma do projeto."""
    from nexo.kernel.platform import config

    default = str(config.resolved(ctx).values.get("data_class") or "confidential")
    proposed, current = after.data_class, before.data_class
    if not proposed or proposed == current:
        return after, qs
    effective = current or default
    if _allowed(ctx, proposed) == _allowed(ctx, effective):
        return after, qs  # nada muda no que pode ir para a IA (ex.: internal e confidential na política padrão)
    kept = after.model_copy(update={"data_class": current})
    stricter = rank(proposed) > rank(effective)
    text = (
        f"Classificação de dados: a IA sugere '{proposed}' (hoje '{effective}'). A classe é do que vai para a IA "
        "ao construir (spec, código, exemplos), não dos dados que o sistema vai tratar em produção — esses viram "
        f"risco e requisito de privacidade. Com '{proposed}', {_effects(ctx, proposed)}; com '{effective}', "
        f"{_effects(ctx, effective)}. Qual vale para esta feature? (public, internal, confidential ou restricted)"
    )
    severity = "blocking" if stricter else "important"
    merged = q.merge_new(qs, [{"text": text, "severity": severity, "section": SECTION, "source": "planner"}])
    ctx.audit("spec.data_class_suggested", {"feature": after.id, "proposed": proposed, "kept": effective})
    return kept, merged


def parse(answer: str) -> str:
    for word in re.findall(r"[a-záéíóúâêôãõç]+", answer.lower()):
        if word in ALIASES:
            return ALIASES[word]
    return ""


def apply_answers(ctx: AppContext, folder: Path, answered: list[q.Question]) -> str:
    """Resposta à dúvida de classificação aplica a classe (decisão humana, auditada); resposta a dúvida da revisão
    da quebra vai para a descrição da tarefa."""
    from nexo.modules.planning.app import backlog_review

    backlog_review.apply_answers(ctx, folder, answered)
    for item in answered:
        if item.section == SECTION:
            value = parse(item.answer)
            if value:
                set_class(ctx, folder, value, f"resposta à dúvida {item.id}")
                return value
    return ""


def set_class(ctx: AppContext, folder: Path, value: str, reason: str) -> dict[str, Any]:
    if value not in DATA_CLASSES:
        raise NexoError(f"classe inválida: {value} (use {', '.join(DATA_CLASSES)})")
    spec = specs.load_spec(folder)
    old = spec.data_class
    specs.save_spec(folder, spec.model_copy(update={"data_class": value}))
    qs = [
        x.model_copy(update={"status": "answered", "answer": x.answer or value})
        if x.section == SECTION and x.status == "open"
        else x
        for x in specs.load_questions(folder)
    ]
    specs.save_questions(folder, qs)
    ctx.audit("spec.data_class", {"feature": spec.id, "from": old, "to": value, "reason": reason, "by": ctx.actor})
    return {"feature": spec.id, "from": old, "to": value, "effects": _effects(ctx, value)}


def classify_feature(ctx: AppContext, fid: str, value: str, reason: str) -> dict[str, Any]:
    """`nexo plan classify F-n <classe>`: decisão humana sobre a classe de dados da feature."""
    root = ctx.project_root
    if root is None:
        raise NexoError("nenhum projeto encontrado; rode `nexo init`")
    return set_class(ctx, specs.feature_dir(root, fid), value, reason or "decisão manual")
