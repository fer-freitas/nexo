"""regras determinísticas de economia de tokens."""

from __future__ import annotations

from importlib import resources

from nexo.kernel.finops import tokens

PYTEST_FAILURE = "\n".join(
    [
        "def test_total():",
        "    carrinho = Carrinho()",
        *[f"    linha {i} do corpo do teste" for i in range(40)],
        ">       assert carrinho.total() == 10",
        "E       assert 9 == 10",
        "E        +  where 9 = <bound method Carrinho.total>()",
        '  File "/home/dev/.venv/lib/python3.12/site-packages/_pytest/python.py", line 159, in pytest_pyfunc_call',
        '  File "src/loja/carrinho.py", line 42, in total',
        '  File "tests/test_carrinho.py", line 12, in test_total',
    ]
)


def test_condense_keeps_error_and_project_frames() -> None:
    out = tokens.condense(PYTEST_FAILURE, max_chars=400)
    assert "E       assert 9 == 10".strip() in out
    assert "src/loja/carrinho.py" in out and "tests/test_carrinho.py" in out
    assert "site-packages" not in out and "linha 20 do corpo" not in out
    assert "omitida" in out and len(out) < len(PYTEST_FAILURE) // 3
    assert tokens.condense("falhou: esperado 3") == "falhou: esperado 3"  # curto fica como está


def test_condense_dotnet_and_js() -> None:
    dotnet = "\n".join(
        [
            "Assert.Equal() Failure: Values differ",
            "Expected: 10",
            "Actual:   9",
            *[f"   at System.Runtime.Something{i}() in \\Microsoft.NET\\x.cs:line {i}" for i in range(30)],
            "   at Loja.Tests.CarrinhoTests.Total() in /src/Loja.Tests/CarrinhoTests.cs:12",
        ]
    )
    out = tokens.condense(dotnet, max_chars=300)
    assert "Expected: 10" in out and "CarrinhoTests.cs:12" in out and "Something5" not in out
    js = "\n".join(
        ["● carrinho › soma os itens", "expect(received).toBe(expected)", "Expected: 10", "Received: 9"]
        + [f"    at Object.<anonymous> (node_modules/jest/x{i}.js:{i}:1)" for i in range(30)]
        + ["    at Object.<anonymous> (src/carrinho.test.ts:8:20)"]
    )
    out = tokens.condense(js, max_chars=300)
    assert "Received: 9" in out and "src/carrinho.test.ts:8" in out and "node_modules" not in out


def test_cache_hit_total_and_cap() -> None:
    assert tokens.cache_hit({"tokens_in": 200, "cache_read": 700, "cache_write": 100}) == 0.7
    assert tokens.cache_hit({}) is None
    assert tokens.total_tokens({"tokens_in": 200, "cache_read": 700, "cache_write": 100, "tokens_out": 50}) == 350
    assert tokens.over_cap(351, 350) and not tokens.over_cap(350, 350) and not tokens.over_cap(10**9, None)


def test_embedded_prompts_put_the_fixed_part_first() -> None:
    """Modelos de prompt de agente: a parte fixa vem antes da variável (o cache do modelo reaproveita o começo)."""
    base = resources.files("nexo.resources").joinpath("prompts")
    fragments = {"handoff.md"}  # pedaço inserido no fim de outro prompt
    low = {
        e.name: tokens.static_prefix(e.read_text(encoding="utf-8"))
        for e in base.iterdir()
        if e.name.endswith(".md") and e.name not in fragments
    }
    assert low and {k: v for k, v in low.items() if v < 0.6} == {}
    assert tokens.static_prefix("Regras fixas.\n{task}") > 0.5 and tokens.static_prefix("{task} regras") == 0.0


def test_skill_fits_stack() -> None:
    assert tokens.skill_fits("revisao-de-pr: checklist de revisão", ["python"])
    assert tokens.skill_fits("python-standards: padrões Python e pytest", ["python"])
    assert not tokens.skill_fits("dotnet-api: controllers ASP.NET e xUnit", ["python"])
    assert tokens.skill_fits("dotnet-api: controllers ASP.NET", [])  # stack desconhecida: não corta
