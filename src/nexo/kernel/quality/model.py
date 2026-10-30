"""Resultados normalizados do pipeline de qualidade (JUnit/SARIF/exit code → modelo único)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

PASSED, FAILED, SKIPPED, ERROR = "passed", "failed", "skipped", "error"
SEVERITY_ORDER = ("note", "low", "medium", "high", "critical")


@dataclass
class Finding:
    rule: str
    message: str
    severity: str = "medium"  # note | low | medium | high | critical
    file: str = ""
    line: int = 0


@dataclass
class TestCase:
    __test__ = False  # não é uma classe de teste do pytest
    name: str
    classname: str = ""
    file: str = ""
    status: str = PASSED  # passed | failed | error | skipped
    message: str = ""


@dataclass
class StageResult:
    id: str
    kind: str
    status: str
    required: bool = True
    summary: str = ""
    findings: list[Finding] = field(default_factory=list)
    tests: list[TestCase] = field(default_factory=list)
    coverage: float | None = None
    seconds: float = 0.0
    log: str = ""
    preexisting: int = 0  # achados/testes da baseline ignorados no portão
    diff_coverage: float | None = None  # cobertura das linhas alteradas
    mutation_score: float | None = None  # Mortos / avaliados
    checked: int | None = None  # arquivos verificados pela etapa de padrão de código

    @property
    def failed_tests(self) -> list[TestCase]:
        return [t for t in self.tests if t.status in (FAILED, ERROR)]

    def worst_severity(self) -> str | None:
        if not self.findings:
            return None
        return max(
            (f.severity for f in self.findings), key=lambda s: SEVERITY_ORDER.index(s) if s in SEVERITY_ORDER else 2
        )


@dataclass
class QualityReport:
    stack: str
    stages: list[StageResult] = field(default_factory=list)
    commit: str = ""

    def stage(self, stage_id: str) -> StageResult | None:
        return next((s for s in self.stages if s.id == stage_id), None)

    @property
    def tests(self) -> list[TestCase]:
        return [t for s in self.stages for t in s.tests]

    @property
    def coverage(self) -> float | None:
        values = [s.coverage for s in self.stages if s.coverage is not None]
        return max(values) if values else None

    @property
    def diff_coverage(self) -> float | None:
        values = [s.diff_coverage for s in self.stages if s.diff_coverage is not None]
        return min(values) if values else None

    @property
    def failed_required(self) -> list[StageResult]:
        return [s for s in self.stages if s.required and s.status in (FAILED, ERROR)]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> QualityReport:
        stages = []
        for s in data.get("stages", []):
            stages.append(
                StageResult(
                    **{k: v for k, v in s.items() if k not in ("findings", "tests")},
                    findings=[Finding(**f) for f in s.get("findings", [])],
                    tests=[TestCase(**t) for t in s.get("tests", [])],
                )
            )
        return QualityReport(data.get("stack", ""), stages, data.get("commit", ""))


def telemetry_summary(report: QualityReport) -> dict[str, object]:
    """Só números para o central: situação das etapas, cobertura e achados por severidade."""
    findings: dict[str, int] = {}
    for stage in report.stages:
        for f in stage.findings:
            findings[f.severity] = findings.get(f.severity, 0) + 1
    coverages = [s.coverage for s in report.stages if s.coverage is not None]
    return {
        "stack": report.stack,
        "stages": [[s.id, s.status] for s in report.stages],
        "failed": sum(1 for s in report.stages if s.required and s.status == FAILED),
        "coverage": round(max(coverages), 2) if coverages else None,
        "findings": findings,
        "mutation": next((round(s.mutation_score, 3) for s in report.stages if s.mutation_score is not None), None),
        "standards": _standards(report),
    }


def _standards(report: QualityReport) -> dict[str, object] | None:
    """aderência ao padrão de código (arquivos, violações e contagem por regra; ids, nunca texto)."""
    stage = next((s for s in report.stages if s.kind == "standards" and s.checked is not None), None)
    if stage is None:
        return None
    from nexo.kernel.quality.standards import Violation, summary

    severity = {"high": "error", "critical": "error"}
    violations = [Violation(f.rule, f.file, f.line, "", severity.get(f.severity, "warning")) for f in stage.findings]
    return summary(violations, int(stage.checked or 0), stage.preexisting)
