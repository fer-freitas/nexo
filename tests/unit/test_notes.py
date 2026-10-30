"""Cérebro: frontmatter, links e isolamento são conferidos sem IA."""

from __future__ import annotations

from datetime import date

from nexo.modules.knowledge.domain import notes as n

NOTE = """---
id: lesson-sqs
type: lesson
title: Idempotência
scope: {scope}
status: active
data_class: internal
verified_at: 2026-10-30
links:
  - {{rel: derived_from, to: decision-fila}}
---

Veja [[decision-fila]].
"""
DECISION = "---\nid: decision-fila\ntype: decision\ntitle: Fila\nscope: global\n---\n\nTexto.\n"


def note(path: str, scope: str = "global") -> n.Note:
    return n.parse(path, NOTE.format(scope=scope))


def errors(notes: list[n.Note]) -> list[str]:
    return [p["message"] for p in n.lint(notes, date(2026, 11, 1)) if p["level"] == "error"]


def test_valid_note_has_no_problem() -> None:
    assert errors([note("lessons/lesson-sqs.md"), n.parse("decisions/decision-fila.md", DECISION)]) == []


def test_broken_link_and_missing_id_are_errors() -> None:
    assert any("link quebrado" in e for e in errors([note("lessons/lesson-sqs.md")]))
    assert errors([n.parse("lessons/x.md", "sem frontmatter")]) == ["sem frontmatter"]


def test_id_must_match_file_name_and_scope_must_match_folder() -> None:
    other = n.parse("decisions/decision-fila.md", DECISION)
    assert any("difere do nome" in e for e in errors([note("lessons/outro-nome.md"), other]))
    assert any("não bate com a pasta" in e for e in errors([note("projects/loja/lessons/lesson-sqs.md"), other]))
    assert errors([note("projects/loja/lessons/lesson-sqs.md", "project:loja"), other]) == []


def test_unknown_relation_is_refused() -> None:
    bad = NOTE.format(scope="global").replace("derived_from", "gosta_de")
    other = n.parse("decisions/decision-fila.md", DECISION)
    assert any("relação desconhecida" in e for e in errors([n.parse("lessons/lesson-sqs.md", bad), other]))


def test_case_only_names_collide_for_windows() -> None:
    a = n.parse("lessons/Abc.md", "---\nid: abc\ntype: lesson\nscope: global\n---\n")
    b = n.parse("decisions/abc.md", "---\nid: abc2\ntype: decision\nscope: global\n---\n")
    assert any("maiúscula" in e for e in errors([a, b]))


def test_skill_keeps_the_standard_format() -> None:
    ok = n.parse("extensions/skills/x/SKILL.md", "---\nname: x\ndescription: d\n---\ncorpo")
    bad = n.parse("extensions/skills/y/SKILL.md", "---\nname: z\ndescription: d\n---\ncorpo")
    assert errors([ok]) == [] and any("difere da pasta" in e for e in errors([bad]))


def test_index_is_deterministic_and_ignores_order() -> None:
    a, b = note("lessons/lesson-sqs.md"), n.parse("decisions/decision-fila.md", DECISION)
    assert n.nodes_and_edges([a, b]) == n.nodes_and_edges([b, a])
    _nodes, edges = n.nodes_and_edges([a, b])
    assert {"from": "lesson-sqs", "rel": "derived_from", "to": "decision-fila"} in edges
    assert {"from": "lesson-sqs", "rel": "links_to", "to": "decision-fila"} in edges


def test_project_isolation() -> None:
    assert n.visible_to("lessons/a.md", "loja") and n.visible_to("projects/loja/a.md", "loja")
    assert not n.visible_to("projects/outro/a.md", "loja")
    assert not n.visible_to("projects\\outro\\a.md", "loja")


def test_expired_verification_is_a_warning() -> None:
    a = n.parse("decisions/decision-fila.md", DECISION.replace("scope: global", "scope: global\nverified_at: 2020-01-01"))
    assert [p["level"] for p in n.lint([a], date(2026, 11, 1))] == ["warning"]


def test_migration_gives_identity() -> None:
    new = n.add_frontmatter("lessons/Minha Lição.md", "# Título\n\nTexto", date(2026, 11, 1))
    parsed = n.parse("lessons/minha-lição.md", new)
    assert parsed.meta["type"] == "lesson" and parsed.meta["title"] == "Título" and parsed.meta["scope"] == "global"
    assert n.add_frontmatter("x/SKILL.md", "corpo") == "corpo"
