"""grafo do código e do cérebro: resolução de imports, ligações e impacto."""

from __future__ import annotations

from nexo.modules.knowledge.domain import graph

CODE = {
    "src/loja/pagamento.py": {"imports": ["loja.moeda"], "is_test": False},
    "src/loja/moeda.py": {"imports": ["decimal"], "is_test": False},
    "src/loja/checkout.py": {"imports": ["loja.pagamento"], "is_test": False},
    "tests/test_pagamento.py": {"imports": ["loja.pagamento"], "is_test": True},
    "web/src/App.tsx": {"imports": ["./api/cliente", "react"], "is_test": False},
    "web/src/api/cliente.ts": {"imports": [], "is_test": False},
    "Loja/Pagamentos/Estorno.cs": {"imports": ["Loja.Moedas"], "is_test": False},
    "Loja/Moedas/Real.cs": {"imports": [], "is_test": False},
}
BACKLOG = {
    "feature": "F-1",
    "stories": [{"id": "US-1-1", "title": "Estorno", "ac_refs": ["AC-1", "AC-2"]}],
    "tasks": [{"id": "T-1-1", "story": "US-1-1", "title": "Regra", "files_hint": ["src/loja/pagamento.py"]}],
}


def sample() -> graph.Graph:
    return graph.build(
        CODE,
        [BACKLOG],
        [{"id": "INC-7", "service": "loja", "files": ["src/loja/moeda.py"]}],
        [
            ("adr", "spec/adr/0003-estorno.md", "O estorno fica em src/loja/pagamento.py e vale 90 dias."),
            ("lesson", "lessons/moeda.md", "Arredondar sempre em moeda.py? não: use Decimal em checkout.py"),
        ],
        {"tests/test_pagamento.py": "# US-1-1 AC-2\ndef test_estorno(): ..."},
    )


def test_imports_resolved_across_stacks() -> None:
    g = sample()
    assert g.out("file:src/loja/pagamento.py", "imports") == {"file:src/loja/moeda.py"}
    assert g.out("file:web/src/App.tsx", "imports") == {"file:web/src/api/cliente.ts"}
    assert g.out("file:Loja/Pagamentos/Estorno.cs", "imports") == {"file:Loja/Moedas/Real.cs"}
    assert g.out("file:tests/test_pagamento.py", "tests") == {"file:src/loja/pagamento.py"}
    assert g.out("file:tests/test_pagamento.py", "verifies") == {"story:US-1-1", "ac:F-1/AC-2"}
    assert g.stats()["file"] == 7 and g.stats()["test"] == 1
    again = graph.Graph.from_json(g.to_json())
    assert again.edges == g.edges and again.nodes == g.nodes


def test_impact_of_a_low_level_change() -> None:
    out = graph.impact(sample(), ["src/loja/moeda.py"])
    assert out["dependents"] == ["src/loja/checkout.py", "src/loja/pagamento.py"]
    assert out["tests"] == ["tests/test_pagamento.py"]
    assert out["stories"] == ["US-1-1 Estorno"] and out["criteria"] == ["F-1 AC-1", "F-1 AC-2"]
    assert out["incidents"] == ["INC-7 loja"] and "spec/adr/0003-estorno.md" in out["decisions"]
    assert graph.impact(sample(), ["src/loja/moeda.py"], depth=1)["dependents"] == ["src/loja/pagamento.py"]


def test_related_is_the_task_context() -> None:
    rel = graph.related(sample(), ["src/loja/pagamento.py"])
    assert rel == {
        "neighbors": ["src/loja/checkout.py", "src/loja/moeda.py"],
        "tests": ["tests/test_pagamento.py"],
        "decisions": ["spec/adr/0003-estorno.md"],
        "lessons": [],
        "docs": [],
        "incidents": [],
    }
    assert graph.related(sample(), ["src/loja/checkout.py"])["lessons"] == ["lessons/moeda.md"]
    assert graph.related(sample(), ["nao/existe.py"]) == {k: [] for k in rel}
