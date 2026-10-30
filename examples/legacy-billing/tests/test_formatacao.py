from billing.invoice import formatMoney


def test_format_money_milhar():  # falha desde 2019: separador de milhar nunca foi implementado
    assert formatMoney(1234.5) == "R$ 1.234,50"
