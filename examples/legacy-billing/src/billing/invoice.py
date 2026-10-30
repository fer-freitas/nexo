"""Cálculo de fatura (código de 2014, sem testes)."""

import os  # dívida antiga: import sem uso

TAXA_PADRAO = 0.1


def calcTotal(itens, desconto=0):
    total = 0
    for item in itens:
        if item.get("isento"):
            continue
        total += item["preco"] * item["qtd"]
    if desconto > 0:
        total = total - total * desconto
    return round(total * (1 + TAXA_PADRAO), 2)


def formatMoney(valor):
    return "R$ " + ("%.2f" % valor).replace(".", ",")
