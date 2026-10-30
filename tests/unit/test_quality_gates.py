from __future__ import annotations

import json

from nexo.kernel.gates.confidence import GateInput, ReviewVerdict, decide, hard_gates, risk_areas, score
from nexo.kernel.quality.model import Finding, QualityReport, StageResult, TestCase
from nexo.kernel.quality.traceability import ac_ids_in, trace
from nexo.modules.quality.adapters import parsers

RISK = {"payments": ["**/*refund*"], "dependency": ["**/package.json"], "infra": [".github/workflows/**"]}


def report(**stage_kw) -> QualityReport:  # type: ignore[no-untyped-def]
    unit = StageResult(
        "unit", "unit", "passed", tests=[TestCase("test_ac_1_x", "tests.test_v", status="passed")], coverage=90.0
    )
    return QualityReport("python", [unit, StageResult("sast", "security", "passed", **stage_kw)])


def gi(**kw) -> GateInput:  # type: ignore[no-untyped-def]
    base = {
        "report": report(),
        "traceability": trace(["AC-1"], report().tests, {}),
        "reviews": [ReviewVerdict("reviewer", "approve"), ReviewVerdict("security-reviewer", "approve")],
        "locked_intact": True,
        "changed_files": ["src/svc/version.py"],
        "diff_lines": 20,
    }
    return GateInput(**{**base, **kw})


def test_auto_when_everything_green() -> None:
    r = decide(gi(history_success=1.0), RISK, ["payments"], 0.9)
    assert r.decision == "auto" and r.passed_hard and r.score >= 0.9


def test_prior_history_keeps_below_095() -> None:
    assert decide(gi(), RISK, [], 0.95).decision == "human"


def test_hard_failures() -> None:
    bad = QualityReport(
        "python",
        [
            StageResult("unit", "unit", "failed", summary="1 falha"),
            StageResult("sast", "security", "passed", findings=[Finding("r", "m", "critical")]),
        ],
    )
    tr = trace(["AC-1", "AC-2"], [TestCase("test_ac_1", status="failed")], {})
    fails = hard_gates(
        gi(
            report=bad,
            traceability=tr,
            locked_intact=False,
            reviews=[
                ReviewVerdict("reviewer", "reject"),
                ReviewVerdict("security-reviewer", "approve", ({"severity": "high", "message": "x"},)),
            ],
            min_coverage=80,
        )
    )
    joined = " | ".join(fails)
    for frag in:
        assert frag in joined, frag
    low_cov = QualityReport("python", [StageResult("unit", "unit", "passed", coverage=50.0)])
    assert any("cobertura" in f for f in hard_gates(gi(report=low_cov, traceability=[], min_coverage=80)))


def test_risk_areas_force_human() -> None:
    r = decide(gi(changed_files=["src/payments/refund.py"], history_success=1.0), RISK, ["payments"], 0.5)
    assert r.decision == "human" and r.human_areas == ["payments"]
    assert risk_areas(["web/package.json"], [], RISK) == ["dependency"]
    assert risk_areas([], ["left-pad"], RISK) == ["dependency"]


def test_score_breakdown_and_penalties() -> None:
    total, parts = score(gi(spec_stable=False, diff_lines=2000), ["payments", "infra"])
    assert parts["spec"] == 0.5 and parts["risk"] < 0.5 and 0 < total < 1
    t2, p2 = score(gi(reviews=[ReviewVerdict("reviewer", "changes")]), [])
    assert p2["review"] == 0.5
    t3, p3 = score(gi(reviews=[]), [], {"review": 0.0})
    assert p3["review"] == 0.5


def test_traceability() -> None:
    tests = [
        TestCase("test_ac_1_ok", "tests.test_a", status="passed"),
        TestCase("test_other", "tests.test_b", status="passed"),
        TestCase("test_generic", "tests.test_c", file="tests/test_c.py", status="failed"),
    ]
    result = trace(["AC-1", "AC-2", "AC-3"], tests, {"tests/test_b.py": {"AC-2"}, "tests/test_c.py": {"AC-3"}})
    assert [(t.ac, t.status) for t in result] == [("AC-1", "passed"), ("AC-2", "passed"), ("AC-3", "failed")]
    assert ac_ids_in("# @AC-1 e AC_2 e ac 3, AC-10") == {"AC-1", "AC-2", "AC-3", "AC-10"}


JUNIT = """<testsuites><testsuite name="s">
<testcase classname="tests.test_v" name="test_ac_1" file="tests/test_v.py"/>
<testcase classname="tests.test_v" name="test_fail"><failure message="boom">trace</failure></testcase>
<testcase classname="tests.test_v" name="test_err"><error message="err"/></testcase>
<testcase classname="tests.test_v" name="test_skip"><skipped/></testcase>
</testsuite></testsuites>"""

TRX = """<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010">
<TestDefinitions><UnitTest id="u1"><TestMethod className="Api.Tests.VersionTests" name="AC1_Retorna"/></UnitTest>
<UnitTest id="u2"><TestMethod className="Api.Tests.VersionTests" name="Falha"/></UnitTest></TestDefinitions>
<Results><UnitTestResult testId="u1" testName="AC1_Retorna" outcome="Passed"/>
<UnitTestResult testId="u2" testName="Falha" outcome="Failed"><Output><ErrorInfo><Message>esperado 1</Message></ErrorInfo></Output></UnitTestResult>
</Results></TestRun>"""

SARIF = json.dumps(
    {
        "runs": [
            {
                "tool": {
                    "driver": {
                        "rules": [
                            {"id": "sql", "properties": {"security-severity": "9.1"}},
                            {"id": "weak", "defaultConfiguration": {"level": "note"}},
                        ]
                    }
                },
                "results": [
                    {
                        "ruleId": "sql",
                        "message": {"text": "SQL injection"},
                        "locations": [
                            {"physicalLocation": {"artifactLocation": {"uri": "src/a.py"}, "region": {"startLine": 7}}}
                        ],
                    },
                    {"ruleId": "weak", "message": {"text": "fraco"}},
                    {"ruleId": "x", "level": "warning", "message": {"text": "aviso"}},
                ],
            }
        ]
    }
)


def test_parsers() -> None:
    cases = parsers.junit(JUNIT)
    assert [c.status for c in cases] == ["passed", "failed", "error", "skipped"] and cases[1].message == "boom"
    trx = parsers.trx(TRX)
    assert [(c.name, c.classname, c.status) for c in trx] == [
        ("AC1_Retorna", "Api.Tests.VersionTests", "passed"),
        ("Falha", "Api.Tests.VersionTests", "failed"),
    ]
    assert trx[1].message == "esperado 1"
    findings = parsers.sarif(SARIF)
    assert [(f.rule, f.severity) for f in findings] == [("sql", "critical"), ("weak", "low"), ("x", "medium")]
    assert findings[0].file == "src/a.py" and findings[0].line == 7
    assert parsers.sarif(SARIF, "high")[1].severity == "high"
    assert parsers.sarif("") == []
    assert parsers.cobertura('<coverage line-rate="0.8765"/>') == 87.6
    assert parsers.cobertura("<coverage/>") is None
    out = "   > Newtonsoft.Json      12.0.1      12.0.1      High      https://github.com/advisories/x"
    assert parsers.dotnet_vulnerable(out)[0].severity == "high"


def test_report_roundtrip() -> None:
    r = report(findings=[Finding("r", "m", "low")])
    again = QualityReport.from_dict(r.as_dict())
    assert again.stage("sast").findings[0].rule == "r" and again.coverage == 90.0  # type: ignore[union-attr]
    assert again.stage("nope") is None and again.stage("sast").worst_severity() == "low"  # type: ignore[union-attr]
