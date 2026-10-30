"""Página de qualidade: falhas, cobertura, escapes e retrabalho."""

from __future__ import annotations

from typing import Any

from nexo.central.common import names
from nexo.central.db import CollectorDB
from nexo.central.metrics.scope import Scope
from nexo.central.metrics.widgets import _drill, _pct, _r, kpi, page, table


def _bucket(score: float | None) -> str:
    if score is None:
        return "sem nota"
    value = float(score) / 100 if float(score) > 1 else float(score)
    for limit, label in ((0.5, "< 0,50"), (0.7, "0,50–0,69"), (0.8, "0,70–0,79"), (0.9, "0,80–0,89")):
        if value < limit:
            return label
    return "≥ 0,90"


def quality(d: CollectorDB, scope: Scope, actor: str) -> dict[str, Any]:
    where, params = scope.where(months=scope.period)
    gates = d.query(
        f"SELECT gate, decision, score, threshold, project, story, at FROM fact_gate WHERE {where}"
        " ORDER BY project, story, at",
        tuple(params),
    )
    code = [g for g in gates if g["gate"] == "code"]
    auto = sum(1 for g in code if g["decision"] == "auto")
    human = sum(1 for g in code if g["decision"] == "human")
    failed = sum(1 for g in code if g["decision"] == "fail")
    buckets: dict[str, int] = {}
    for g in code:
        if g["decision"] in ("auto", "human", "fail"):
            buckets[_bucket(g["score"])] = buckets.get(_bucket(g["score"]), 0) + 1
    calibration: dict[str, list[int]] = {}
    pending: dict[tuple[str, str], str] = {}
    for g in code:
        key = (str(g["project"]), str(g["story"]))
        if g["decision"] == "human":
            pending[key] = _bucket(g["score"])
        elif g["decision"] in ("approved", "rejected") and key in pending:
            slot = calibration.setdefault(pending.pop(key), [0, 0])
            slot[0 if g["decision"] == "approved" else 1] += 1
    qa = (
        d.one(
            "SELECT COUNT(*) AS runs, SUM(CASE WHEN failed > 0 THEN 1 ELSE 0 END) AS failed_runs,"
            " COALESCE(SUM(findings_critical), 0) AS c, COALESCE(SUM(findings_high), 0) AS h,"
            " COALESCE(SUM(findings_medium), 0) AS m, COALESCE(SUM(findings_low), 0) AS l, AVG(coverage) AS cov"
            f" FROM fact_quality WHERE {where}",
            tuple(params),
        )
        or {}
    )
    flaky = d.one(f"SELECT COUNT(*) AS n FROM fact_event WHERE type = 'quality.flaky' AND {where}", tuple(params))
    violations = d.query(
        f"SELECT COALESCE(rule, '-') AS rule, COUNT(*) AS n FROM fact_event WHERE type = 'policy.violation'"
        f" AND {where} GROUP BY COALESCE(rule, '-') ORDER BY 1",
        tuple(params),
    )
    cost_gates = [g for g in gates if g["gate"] == "cost"]
    std = _standards(d, where, params)
    tables = [
        table(
            "scores",
            "Distribuição das notas do portão",
            ["faixa", "decisões"],
            sorted([list(x) for x in buckets.items()]),
        ),
        table(
            "calibration",
            "Calibração: decisão humana por faixa de nota",
            ["faixa", "aprovadas", "rejeitadas", "% aprovadas"],
            [[k, v[0], v[1], _pct(v[0], v[0] + v[1])] for k, v in sorted(calibration.items())],
        ),
        table(
            "findings",
            "Achados por severidade",
            ["severidade", "achados"],
            [
                ["crítica", int(qa.get("c") or 0)],
                ["alta", int(qa.get("h") or 0)],
                ["média", int(qa.get("m") or 0)],
                ["baixa", int(qa.get("l") or 0)],
            ],
        ),
        table(
            "violations",
            "Violações de política por regra",
            ["regra", "ocorrências"],
            [[r["rule"], int(r["n"])] for r in violations],
        ),
        table(
            "standards_rules",
            "Padrão de código: regras mais violadas (F48)",
            ["regra", "violações", "projetos"],
            [[r["rule"], int(r["n"]), int(r["projects"])] for r in std["rules"]],
        ),
        table(
            "standards_projects",
            "Padrão de código: aderência por projeto",
            ["projeto", "arquivos verificados", "sem violação", "aderência %", "erros"],
            [
                [r["project"], int(r["files"]), int(r["ok"]), _pct(float(r["ok"]), float(r["files"])), int(r["errors"])]
                for r in std["projects"]
            ],
        ),
        table(
            "cost_gates",
            "Portão de custo",
            ["decisão", "ocorrências"],
            sorted(
                [
                    [k, sum(1 for g in cost_gates if g["decision"] == k)]
                    for k in {str(g["decision"]) for g in cost_gates}
                ]
            ),
        ),
    ]
    if scope.principal.individual_squads("violations") is None:
        rows = d.query(
            f"SELECT dev_pid, COUNT(*) AS n FROM fact_event WHERE type = 'policy.violation' AND {where}"
            " GROUP BY dev_pid",
            tuple(params),
        )
        who = names(actor, "violations", [str(r["dev_pid"]) for r in rows])
        tables.append(
            table(
                "violations_people",
                "Violações por pessoa (Segurança, auditado, ordem alfabética)",
                ["pessoa", "violações"],
                sorted([[who.get(str(r["dev_pid"]), "?"), int(r["n"])] for r in rows]),
                individual="violations",
            )
        )
    from nexo.central.metrics.pages import answers

    questions, todo = answers.quality(
        auto_pct=_pct(auto, auto + human),
        critical=int(qa.get("c") or 0),
        high=int(qa.get("h") or 0),
        adherence=std["adherence"],
        coverage=_r(qa.get("cov"), 1),
        flaky=int(flaky["n"] if flaky else 0),
        violations=sum(int(r["n"]) for r in violations),
        gate_fail=failed,
    )
    return page(
        "quality",
        "Qualidade e segurança",
        scope,
        headline=questions,
        attention=todo,
        kpis=[
            kpi("auto", "Portões automáticos", auto, "int", _drill("gate", gate="code", decision="auto")),
            kpi("human", "Portões humanos", human, "int", _drill("gate", gate="code", decision="human")),
            kpi("gate_fail", "Reprovados pelo portão", failed, "int", _drill("gate", gate="code", decision="fail")),
            kpi("auto_pct", "% automáticos", _pct(auto, auto + human), "pct"),
            kpi("runs", "Execuções do pipeline", int(qa.get("runs") or 0), "int", _drill("quality")),
            kpi("coverage", "Cobertura média %", _r(qa.get("cov"), 1), "num"),
            kpi("high", "Achados altos/críticos", int(qa.get("h") or 0) + int(qa.get("c") or 0), "int"),
            kpi(
                "flaky",
                "Testes instáveis",
                int(flaky["n"] if flaky else 0),
                "int",
                _drill("event", type="quality.flaky"),
            ),
            kpi("standards", "Aderência ao padrão de código %", std["adherence"], "pct"),
            kpi("standards_errors", "Erros de padrão de código", std["errors"], "int"),
            kpi(
                "violations",
                "Violações de política",
                sum(int(r["n"]) for r in violations),
                "int",
                _drill("event", type="policy.violation"),
            ),
        ],
        tables=tables,
    )


def _standards(d: CollectorDB, where: str, params: list[Any]) -> dict[str, Any]:
    """aderência = arquivos sem violação ÷ arquivos verificados, nas execuções do pipeline do período."""
    total = (
        d.one(
            "SELECT COALESCE(SUM(standards_files), 0) AS files, COALESCE(SUM(standards_ok), 0) AS ok,"
            f" COALESCE(SUM(standards_errors), 0) AS errors FROM fact_quality WHERE {where}",
            tuple(params),
        )
        or {}
    )
    projects = d.query(
        "SELECT project, COALESCE(SUM(standards_files), 0) AS files, COALESCE(SUM(standards_ok), 0) AS ok,"
        f" COALESCE(SUM(standards_errors), 0) AS errors FROM fact_quality WHERE {where}"
        " AND standards_files IS NOT NULL GROUP BY project ORDER BY project",
        tuple(params),
    )
    rules = d.query(
        "SELECT rule, SUM(count) AS n, COUNT(DISTINCT project) AS projects FROM fact_standards_rule"
        f" WHERE {where} GROUP BY rule ORDER BY 2 DESC, 1 LIMIT 20",
        tuple(params),
    )
    files = float(total.get("files") or 0)
    return {
        "adherence": _pct(float(total.get("ok") or 0), files) if files else None,
        "errors": int(total.get("errors") or 0),
        "projects": projects,
        "rules": rules,
    }
