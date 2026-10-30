"""Impacto de uma mudança na spec: quais tarefas e testes refazer. Sem IA; na dúvida, para tudo.

Uma tarefa é afetada quando cita o critério ou o trecho que mudou, ou quando os arquivos dela se sobrepõem aos
arquivos ligados à mudança. Tarefa sem nenhuma referência não dá para situar: nesse caso `uncertain` fica verdadeiro
e a história inteira pausa. Pausar demais só atrasa; continuar por engano gera retrabalho sem ninguém perceber.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath


@dataclass(frozen=True)
class TaskRef:
    id: str
    ac_refs: tuple[str, ...] = ()
    section_refs: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    done: bool = False


@dataclass
class Impact:
    affected: list[str] = field(default_factory=list)
    unaffected: list[str] = field(default_factory=list)
    done_kept: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    uncertain: bool = False
    reasons: list[str] = field(default_factory=list)


def _overlap(a: str, b: str) -> bool:
    x, y = PurePosixPath(a.replace("\\", "/")), PurePosixPath(b.replace("\\", "/"))
    return x == y or x in y.parents or y in x.parents


def compute(
    tasks: list[TaskRef],
    changed_acs: list[str],
    changed_refs: list[str],
    changed_files: list[str],
    ac_tests: dict[str, list[str]] | None = None,
) -> Impact:
    out = Impact()
    if not changed_acs and not changed_refs and not changed_files:
        out.uncertain = True
        out.reasons.append("a mudança não aponta critério, trecho nem arquivo")
    for t in tasks:
        known = bool(t.ac_refs or t.section_refs or t.files)
        hit = (
            set(t.ac_refs) & set(changed_acs)
            or set(t.section_refs) & set(changed_refs)
            or any(_overlap(a, b) for a in t.files for b in changed_files)
        )
        if not known:
            out.uncertain = True
            out.reasons.append(f"{t.id} não declara critérios, trechos nem arquivos")
        if hit:
            out.affected.append(t.id)
        elif t.done:
            out.done_kept.append(t.id)
        else:
            out.unaffected.append(t.id)
    for ac in changed_acs:
        out.tests.extend(t for t in (ac_tests or {}).get(ac, []) if t not in out.tests)
    if out.uncertain:
        out.affected = [t.id for t in tasks if not t.done or t.id in out.affected]
        out.unaffected = []
    return out
