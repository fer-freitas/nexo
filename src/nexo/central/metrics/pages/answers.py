"""Perguntas respondidas no topo e "Precisa de atenção" das páginas Custos, Squads e Qualidade.

Cada função recebe os números que a página já calculou e devolve `(perguntas, atenção)`; nada de consulta nova.
Estados: good (no trilho), warn (olhar), bad (agir), neutral (sem base para julgar).
"""

from __future__ import annotations

from typing import Any

from nexo.central.metrics.widgets import attention, headline
from nexo.kernel.finops.money import brl

FPY_TARGET = 85.0  # mesma meta da visão geral
IDLE_PCT = 30.0  # uso abaixo disto da assinatura = licença ociosa
CACHE_GOOD = 50.0
INVOICE_TOLERANCE = 5.0  # % de diferença entre fatura e medido
WAIT_WARN, WAIT_BAD = 24.0, 72.0  # horas esperando decisão humana
ESTIMATE_GOOD, ESTIMATE_WARN = 20.0, 50.0  # erro médio da estimativa por feature (%)
COVERAGE_GOOD, COVERAGE_WARN = 80.0, 60.0
STANDARDS_GOOD, STANDARDS_WARN = 90.0, 75.0

Answers = tuple[list[dict[str, Any]], list[dict[str, str]]]


def _ratio(num: float, den: float) -> float | None:
    return round(100 * num / den, 1) if den else None


def finops(
    usage: float,
    projection: float | None,
    budget: float,
    allocated: float,
    outside: float,
    cache_hit: float | None,
    acu_excess: float,
    invoices: list[dict[str, Any]],
    month: str,
) -> Answers:
    if not budget:
        spend = ("warn", "sem orçamento definido")
    else:
        ratio = 100 * (projection or 0) / budget
        spend = (
            ("good", f"dentro do orçamento ({ratio:.0f}%)")
            if ratio <= 90
            else ("warn", f"perto do teto ({ratio:.0f}%)")
            if ratio <= 100
            else ("bad", f"projeção acima do orçamento ({ratio:.0f}%)")
        )
    used = _ratio(usage, allocated)
    if used is None:
        idle = ("neutral", "nenhuma assinatura cadastrada")
    elif used < IDLE_PCT:
        idle = ("warn", f"menos de {IDLE_PCT:.0f}% do valor das assinaturas")
    else:
        idle = ("good", "assinaturas em uso")
    share = _ratio(usage, usage + outside)
    cache = (
        ("neutral", "sem dados de cache no período")
        if cache_hit is None
        else ("good", "a parte fixa dos prompts está sendo reaproveitada")
        if cache_hit >= CACHE_GOOD
        else ("warn", "pouco reaproveitamento: tokens pagos de novo")
    )
    questions = [
        headline(
            "spend",
            "Quanto estamos gastando?",
            usage,
            "brl",
            f"{month} · projeção de {brl(projection)}" + (f" para um teto de {brl(budget)}" if budget else ""),
            *spend,
        ),
        headline(
            "licenses",
            "As assinaturas estão sendo usadas?",
            used,
            "pct",
            f"{brl(usage)} de uso para {brl(allocated)} em assinaturas" if allocated else "cadastre as licenças",
            *idle,
            bar=used,
        ),
        headline(
            "through_nexo",
            "Quanto da IA passa pelo Nexo?",
            share,
            "pct",
            f"{brl(outside)} usados direto nas ferramentas" if outside else "nenhum uso fora do Nexo registrado",
            bar=share,
        ),
        headline(
            "cache", "O cache dos modelos está ajudando?", cache_hit, "pct", "da entrada servida pelo cache", *cache
        ),
    ]
    todo = []
    if not budget:
        todo.append(
            attention(
                f"Sem orçamento para {month}",
                f"A projeção é de {brl(projection)}; com um teto, o Nexo avisa antes de estourar.",
                "nexo cost budget set",
                "warn",
            )
        )
    elif projection and projection > budget:
        todo.append(
            attention(
                "Projeção acima do orçamento",
                f"{brl(projection)} previstos para um teto de {brl(budget)}. Veja o uso por squad abaixo.",
                level="bad",
            )
        )
    if used is not None and used < IDLE_PCT:
        todo.append(
            attention(
                "Assinaturas pouco usadas",
                f"{brl(usage)} de uso para {brl(allocated)} pagos. Veja a licença por pessoa ou realoque.",
                level="warn",
            )
        )
    if acu_excess > 0:
        todo.append(
            attention(
                "ACU do Devin acima do incluso",
                f"{acu_excess:.1f} ACU excedentes neste mês são cobrados à parte.",
                level="warn",
            )
        )
    for inv in invoices:
        diff = inv.get("diff_pct")
        if diff is not None and abs(float(diff)) > INVOICE_TOLERANCE:
            todo.append(
                attention(
                    f"Fatura de {inv.get('tool')} diverge do medido",
                    f"Diferença de {float(diff):.1f}% entre a fatura e o que o Nexo mediu.",
                    "nexo cost reconcile",
                    "warn",
                )
            )
    return questions, todo


def squad(
    stories: dict[str, Any],
    in_progress: int,
    estimates: list[tuple[float, float]],
    over_cap: list[str],
    near_cap: list[str],
    escalated: int,
    top_block: tuple[str, int] | None,
) -> Answers:
    done = int(stories.get("done") or 0)
    wait = stories.get("waiting_hours")
    waiting = (
        ("neutral", "nenhuma decisão humana no período")
        if wait is None
        else ("good", "decisões rápidas")
        if wait < WAIT_WARN
        else ("warn", "decisões demorando mais de um dia")
        if wait < WAIT_BAD
        else ("bad", "decisões paradas há dias")
    )
    fpy = stories.get("fpy")
    quality = (
        ("neutral", "nenhuma história entregue no período")
        if fpy is None
        else ("good", f"acima da meta de {FPY_TARGET:.0f}%")
        if fpy >= FPY_TARGET
        else ("bad" if fpy < FPY_TARGET - 15 else "warn", f"abaixo da meta de {FPY_TARGET:.0f}%")
    )
    errors = [abs(real - est) / est * 100 for est, real in estimates if est]
    error = round(sum(errors) / len(errors), 1) if errors else None
    estimate = (
        ("neutral", "nenhuma feature concluída com estimativa")
        if error is None
        else ("good", "estimativa confiável")
        if error <= ESTIMATE_GOOD
        else ("warn", "estimativa com folga grande")
        if error <= ESTIMATE_WARN
        else ("bad", "estimativa pouco confiável")
    )
    questions = [
        headline("done", "Quanto entregamos?", done, "int", f"histórias no período · {in_progress} em andamento"),
        headline("waiting", "Onde está travando?", wait, "num", "horas, em média, esperando decisão humana", *waiting),
        headline(
            "rework", "Quanto volta para retrabalho?", fpy, "pct", "das histórias sem bug depois de entregues", *quality
        ),
        headline(
            "estimate", "A estimativa bate com o real?", error, "pct", "erro médio do custo por feature", *estimate
        ),
    ]
    todo = []
    if over_cap:
        todo.append(
            attention(
                f"{len(over_cap)} história(s) acima do teto de custo",
                f"{', '.join(over_cap[:5])}: o portão de custo pede decisão.",
                "nexo inbox",
                "bad",
            )
        )
    if near_cap:
        todo.append(
            attention(
                f"{len(near_cap)} história(s) perto do teto",
                f"{', '.join(near_cap[:5])} já passaram de 80% do teto de custo.",
                level="warn",
            )
        )
    if escalated:
        todo.append(
            attention(
                f"{escalated} história(s) escalada(s)",
                "O agente desistiu e devolveu para uma pessoa; veja o motivo e retome.",
                "nexo inbox",
                "warn",
            )
        )
    if top_block:
        todo.append(
            attention(
                f"Bloqueio mais comum: {top_block[0]}",
                f"{top_block[1]} ocorrência(s) no período.",
            )
        )
    return questions, todo


def quality(
    auto_pct: float | None,
    critical: int,
    high: int,
    adherence: float | None,
    coverage: float | None,
    flaky: int,
    violations: int,
    gate_fail: int,
) -> Answers:
    risk = ("good", "nenhum achado alto ou crítico") if not (critical or high) else ("bad", "corrigir antes do release")
    standards = (
        ("neutral", "padrão de código ainda não verificado")
        if adherence is None
        else ("good", "código no padrão")
        if adherence >= STANDARDS_GOOD
        else ("warn", "parte do código fora do padrão")
        if adherence >= STANDARDS_WARN
        else ("bad", "muito código fora do padrão")
    )
    cover = (
        ("neutral", "sem cobertura medida")
        if coverage is None
        else ("good", f"acima de {COVERAGE_GOOD:.0f}%")
        if coverage >= COVERAGE_GOOD
        else ("warn", f"abaixo de {COVERAGE_GOOD:.0f}%")
        if coverage >= COVERAGE_WARN
        else ("bad", f"abaixo de {COVERAGE_WARN:.0f}%")
    )
    questions = [
        headline("risk", "Há risco aberto?", critical + high, "int", "achados altos e críticos no período", *risk),
        headline("standards", "O código segue o padrão?", adherence, "pct", "dos arquivos sem violação", *standards),
        headline("coverage", "Os testes cobrem o código?", coverage, "pct", "cobertura média do pipeline", *cover),
        headline(
            "auto",
            "Quanto o portão decide sozinho?",
            auto_pct,
            "pct",
            "das histórias sem aprovação humana",
            bar=auto_pct,
        ),
    ]
    todo = []
    if critical:
        todo.append(
            attention(f"{critical} achado(s) crítico(s)", "Veja os achados por severidade abaixo.", level="bad")
        )
    if flaky:
        todo.append(
            attention(
                f"{flaky} teste(s) instável(is)",
                "Teste que ora passa, ora falha esconde defeito e trava o portão.",
                "nexo quality run",
                "warn",
            )
        )
    if violations:
        todo.append(
            attention(
                f"{violations} violação(ões) de política",
                "Comandos ou acessos barrados pela política de segurança; veja as regras abaixo.",
                level="warn",
            )
        )
    if gate_fail:
        todo.append(attention(f"{gate_fail} reprovação(ões) pelo portão", "Viraram bug e voltaram para o agente."))
    return questions, todo
