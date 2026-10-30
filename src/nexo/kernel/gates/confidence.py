"""Portões duros + nota de confiança composta."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any

from nexo.kernel.quality.model import QualityReport
from nexo.kernel.quality.traceability import AcTrace

DEFAULT_WEIGHTS = {"review": 0.30, "quality": 0.20, "history": 0.20, "spec": 0.15, "risk": 0.15}
BLOCKING_SEVERITIES = ("high", "critical")


@dataclass(frozen=True)
class ReviewVerdict:
    reviewer: str  # reviewer | security-reviewer
    verdict: str  # approve | changes | reject
    findings: tuple[dict[str, Any], ...] = ()

    @property
    def blocking(self) -> list[dict[str, Any]]:
        return [f for f in self.findings if str(f.get("severity", "")).lower() in BLOCKING_SEVERITIES]


@dataclass
class GateInput:
    report: QualityReport
    traceability: list[AcTrace]
    reviews: list[ReviewVerdict]
    locked_intact: bool
    changed_files: list[str]
    diff_lines: int
    new_dependencies: list[str] = field(default_factory=list)
    history_success: float | None = None  # taxa de sucesso histórica da rota × tipo × stack
    spec_stable: bool = True
    min_coverage: float = 0.0
    min_diff_coverage: float = 0.0  # cobertura mínima das linhas alteradas


@dataclass
class GateResult:
    hard_failures: list[str]
    score: float
    breakdown: dict[str, float]
    human_areas: list[str]
    decision: str  # auto | human | fail
    threshold: float

    @property
    def passed_hard(self) -> bool:
        return not self.hard_failures


def hard_gates(g: GateInput) -> list[str]:
    fails = [f"etapa obrigatória falhou: {s.id} ({s.summary})" for s in g.report.failed_required]
    workflows = [f for f in g.changed_files if f.replace("\\", "/").startswith(".github/workflows/")]
    if workflows:
        fails.append(f"alteração em workflow predefinido: {', '.join(workflows)}")
    for s in g.report.stages:
        if s.kind == "security" and s.worst_severity() in BLOCKING_SEVERITIES:
            fails.append(f"{s.id}: achado {s.worst_severity()}")
    missing = [t.ac for t in g.traceability if t.status == "missing"]
    failing = [t.ac for t in g.traceability if t.status == "failed"]
    if missing:
        fails.append(f"critérios sem teste: {', '.join(missing)}")
    if failing:
        fails.append(f"critérios com teste falhando: {', '.join(failing)}")
    if not g.locked_intact:
        fails.append("testes bloqueados foram alterados")
    for r in g.reviews:
        if r.verdict == "reject":
            fails.append(f"{r.reviewer} reprovou")
        elif r.blocking:
            fails.append(f"{r.reviewer}: {len(r.blocking)} achado(s) alto/crítico")
    cov = g.report.coverage
    if g.min_coverage and cov is not None and cov < g.min_coverage:
        fails.append(f"cobertura {cov:.0f}% abaixo de {g.min_coverage:.0f}%")
    diff_cov = g.report.diff_coverage
    if g.min_diff_coverage and diff_cov is not None and diff_cov < g.min_diff_coverage:
        fails.append(f"cobertura das linhas alteradas {diff_cov:.0f}% abaixo de {g.min_diff_coverage:.0f}%")
    return fails


def risk_areas(changed_files: list[str], new_deps: list[str], risk_map: dict[str, list[str]]) -> list[str]:
    areas = []
    for area, patterns in risk_map.items():
        if any(fnmatch.fnmatch(f, p) or fnmatch.fnmatch(f.lower(), p.lower()) for f in changed_files for p in patterns):
            areas.append(area)
    if new_deps and "dependency" not in areas:
        areas.append("dependency")
    return areas


def score(g: GateInput, areas: list[str], weights: dict[str, float] | None = None) -> tuple[float, dict[str, float]]:
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    verdicts = [r.verdict for r in g.reviews]
    review = 1.0 if verdicts and all(v == "approve" for v in verdicts) else (0.5 if "reject" not in verdicts else 0.0)
    review -= 0.1 * sum(len(r.findings) for r in g.reviews if r.verdict == "approve")
    warnings = sum(len(s.findings) for s in g.report.stages if s.kind != "security")
    coverage = g.report.coverage
    quality = min(1.0, (coverage / 100 if coverage is not None else 0.8)) - 0.05 * min(warnings, 6)
    history = g.history_success if g.history_success is not None else 0.7
    spec = 1.0 if g.spec_stable else 0.5
    size_risk = min(1.0, g.diff_lines / 800)
    risk = min(1.0, 0.25 * len(areas) + 0.5 * size_risk)
    parts = {
        "review": max(0.0, review),
        "quality": max(0.0, quality),
        "history": history,
        "spec": spec,
        "risk": 1.0 - risk,
    }
    total = sum(w[k] * v for k, v in parts.items())
    return round(total, 4), {k: round(v, 3) for k, v in parts.items()}


def decide(
    g: GateInput,
    risk_map: dict[str, list[str]],
    always_human: list[str],
    threshold: float,
    weights: dict[str, float] | None = None,
) -> GateResult:
    fails = hard_gates(g)
    areas = risk_areas(g.changed_files, g.new_dependencies, risk_map)
    total, parts = score(g, areas, weights)
    human = [a for a in areas if a in always_human]
    if fails:
        decision = "fail"
    elif human or total < threshold:
        decision = "human"
    else:
        decision = "auto"
    return GateResult(fails, total, parts, human, decision, threshold)
