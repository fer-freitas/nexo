"""produto: visões e componentes compartilhados, impacto cruzado e resumo para o prompt."""

from __future__ import annotations

import pytest

from nexo.kernel.product import Part, Product, describe, impact, mentioned


def custodia() -> Product:
    return Product(
        id="custodia",
        name="Custódia",
        description="posições e movimentações de ativos",
        parts=[
            Part(id="motor-posicao", kind="componente", name="Motor de posição", squads=["sq-core"], aliases=["mp"]),
            Part(id="ui-kit", kind="componente", name="UI Kit", squads=["sq-core"]),
            Part(id="carteira-passiva", kind="visao", name="Carteira passiva", squads=["sq-a"], uses=["motor-posicao"]),
            Part(
                id="custodia-banco",
                kind="visao",
                name="Custódia banco",
                squads=["sq-b"],
                uses=["motor-posicao", "ui-kit"],
            ),
            Part(id="custodia-cliente", kind="visao", name="Custódia cliente", squads=["sq-b"], uses=["ui-kit"]),
        ],
    )


def test_component_change_affects_every_consumer() -> None:
    found = impact(custodia(), "motor-posicao", "qualquer mudança")
    assert found.components == ["Motor de posição"]
    assert sorted(found.affected) == ["Carteira passiva", "Custódia banco"] and found.squads == ["sq-a", "sq-b"]


def test_vision_feature_citing_a_component_warns_the_other_consumers() -> None:
    found = impact(custodia(), "custodia-cliente", "Novo filtro no UI Kit para a tela de extrato")
    assert found.components == ["UI Kit"] and found.affected == ["Custódia banco"]
    assert not impact(custodia(), "custodia-cliente", "Nova tela de extrato").any  # só a própria visão
    assert [p.id for p in mentioned(custodia(), "ajuste no MP")] == ["motor-posicao"]  # apelido


def test_invalid_product_is_explained() -> None:
    with pytest.raises(ValueError, match="usa motor, que não é componente"):
        Product(id="x1", parts=[Part(id="tela", kind="visao", uses=["motor"])])
    with pytest.raises(ValueError, match="parte repetida"):
        Product(id="x1", parts=[Part(id="a1", kind="visao"), Part(id="a1", kind="componente")])
    with pytest.raises(ValueError, match="id inválido"):
        Part(id="Com Espaço", kind="visao")


def test_summary_tells_the_ai_where_the_repository_sits() -> None:
    text = describe(custodia(), "motor-posicao")
    assert "Produto: Custódia" in text and "Visões: Carteira passiva, Custódia banco, Custódia cliente" in text
    assert "Este repositório é componente compartilhado Motor de posição" in text
    assert "mudanças aqui afetam Carteira passiva, Custódia banco" in text
