"""Resumo de pedidos (usa o cálculo de fatura)."""

from billing.invoice import calcTotal, formatMoney


def gerarResumo(pedidos):
    linhas = []
    for pedido in pedidos:
        linhas.append(pedido["cliente"] + ": " + formatMoney(calcTotal(pedido["itens"])))
    return "\n".join(linhas)
