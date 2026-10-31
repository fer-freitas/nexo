from __future__ import annotations

from nexo.kernel.runtime.resources import read_text
from nexo.modules.planning.domain.templates import mustache
from nexo.modules.planning.domain.templates.model import override_problems, parse, sections, slug


def test_mustache_variables_sections_and_standalone_lines() -> None:
    tpl = "# {{title}}\n{{#items}}\n- {{name}} ({{meta.size}})\n{{/items}}\n{{^items}}\nNada.\n{{/items}}\n{{! comentário }}\n"
    out = mustache.render(tpl, {"title": "T", "items": [{"name": "a", "meta": {"size": 1}}, {"name": "b"}]})
    assert out == "# T\n- a (1)\n- b ()\n"
    assert mustache.render(tpl, {"title": "T", "items": []}) == "# T\nNada.\n"
    assert mustache.render("{{#xs}}{{.}};{{/xs}}", {"xs": ["x", "y"]}) == "x;y;"
    assert mustache.render("{{#ok}}sim{{/ok}}{{^ok}}não{{/ok}}", {"ok": True}) == "sim"
    assert mustache.render("{{lista}}", {"lista": ["a", "b"]}) == "a, b"
    assert mustache.check("{{#a}}sem fim") and mustache.check("{{/b}}")


def test_sections_are_identified_by_heading_slug() -> None:
    md = "# T\n\n## 4. Critérios de aceite\n- AC-1\n\n## Riscos\n<!-- orientação -->\n\n```\n## não é seção\n```\n"
    assert slug("5. Classificação de dados") == "classificacao-de-dados"
    found = sections(md)
    assert found["criterios-de-aceite"] == "- AC-1" and found["riscos"].startswith("```")
    assert "nao-e-secao" not in found


def test_baseline_templates_are_valid_and_override_keeps_locked_sections() -> None:
    for name in ("epic", "feature", "sdd", "requisitos", "design-tecnico", "story", "task", "bug", "adr"):
        assert parse(read_text(f"templates/{name}.md")).problems == [], name
    base = parse(read_text("templates/story.md"))
    bad = parse("---\nnexo_template: story\nrequired_sections: [narrativa]\n---\n# {{id}}\n\n## Narrativa\n{{as_a}}\n")
    assert override_problems(base, bad) == [
        "seção travada removida: criterios-de-aceite",
        "seção travada deixou de ser obrigatória: criterios-de-aceite",
    ]
    broken = parse("---\nnexo_template: story\nrequired_sections: [inexistente]\n---\n## Narrativa\n")
    assert "seção declarada e ausente no corpo: inexistente" in broken.problems


def test_requirement_with_many_criteria_renders_each_line_once() -> None:
    from nexo.kernel.spec.model import Requirement, Scenario, Spec
    from nexo.modules.planning.app.templates import requirements_context

    acs = [Scenario(id=f"AC-{i}", title="a", when=["x"], then=["y"]) for i in (1, 2)]
    rf = Requirement(
        id="RF-1", title="R", statement="O sistema deve", acceptance=["AC-1", "AC-2"], rules=["RN-1", "RN-2"]
    )
    spec = Spec(id="F-3", title="T", acceptance=acs, requirements=[rf])
    text = parse(read_text("templates/requisitos.md")).render(requirements_context(spec, None))
    assert text.count("**Critérios de aceite**: AC-1, AC-2") == 1 and text.count("**Regras**: RN-1, RN-2") == 1
    assert "- RF-1 → critérios AC-1, AC-2" in text
