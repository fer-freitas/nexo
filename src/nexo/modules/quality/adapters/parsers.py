"""Parsers de relatórios de ferramentas: JUnit, TRX (.NET), SARIF, Cobertura."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET  # relatórios gerados localmente pelas ferramentas da receita
from typing import Any

from nexo.kernel.quality.model import ERROR, FAILED, PASSED, SKIPPED, Finding, TestCase

SARIF_LEVEL = {"error": "high", "warning": "medium", "note": "low", "none": "note"}


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def junit(text: str) -> list[TestCase]:
    root = ET.fromstring(text)  # noqa: S314
    cases: list[TestCase] = []
    for el in root.iter():
        if _strip_ns(el.tag) != "testcase":
            continue
        status, message = PASSED, ""
        for child in el:
            tag = _strip_ns(child.tag)
            if tag in ("failure", "error"):
                status = FAILED if tag == "failure" else ERROR
                message = (child.get("message") or child.text or "").strip()[:500]
            elif tag == "skipped":
                status = SKIPPED
        cases.append(TestCase(el.get("name", ""), el.get("classname", ""), el.get("file", ""), status, message))
    return cases


def trx(text: str) -> list[TestCase]:
    root = ET.fromstring(text)  # noqa: S314
    definitions: dict[str, str] = {}
    for el in root.iter():
        if _strip_ns(el.tag) == "UnitTest":
            method = next((c for c in el.iter() if _strip_ns(c.tag) == "TestMethod"), None)
            if method is not None:
                definitions[el.get("id", "")] = method.get("className", "")
    cases = []
    for el in root.iter():
        if _strip_ns(el.tag) != "UnitTestResult":
            continue
        outcome = el.get("outcome", "")
        status = {"Passed": PASSED, "Failed": FAILED, "NotExecuted": SKIPPED}.get(outcome, ERROR)
        message = ""
        for c in el.iter():
            if _strip_ns(c.tag) == "Message" and c.text:
                message = c.text.strip()[:500]
        cases.append(TestCase(el.get("testName", ""), definitions.get(el.get("testId", ""), ""), "", status, message))
    return cases


def sarif(text: str, default_severity: str | None = None) -> list[Finding]:
    data = json.loads(text or "{}")
    findings: list[Finding] = []
    for run in data.get("runs", []):
        rules = {r.get("id"): r for r in (run.get("tool", {}).get("driver", {}).get("rules") or [])}
        for res in run.get("results", []):
            rule_id = str(res.get("ruleId", ""))
            level = res.get("level") or (rules.get(rule_id, {}).get("defaultConfiguration") or {}).get("level")
            sev = default_severity or SARIF_LEVEL.get(str(level or "warning"), "medium")
            props = rules.get(rule_id, {}).get("properties") or {}
            if isinstance(props.get("security-severity"), str):
                score = float(props["security-severity"])
                sev = "critical" if score >= 9 else "high" if score >= 7 else "medium" if score >= 4 else "low"
            loc = (res.get("locations") or [{}])[0].get("physicalLocation") or {}
            findings.append(
                Finding(
                    rule=rule_id,
                    message=str((res.get("message") or {}).get("text", ""))[:300],
                    severity=sev,
                    file=str((loc.get("artifactLocation") or {}).get("uri", "")),
                    line=int((loc.get("region") or {}).get("startLine", 0) or 0),
                )
            )
    return findings


def flutter_machine(text: str) -> list[TestCase]:
    """`flutter test --machine` (JSON por linha): `testStart` dá nome e arquivo; `testDone` dá o resultado."""
    started: dict[int, tuple[str, str]] = {}
    suites: dict[int, str] = {}
    cases: list[TestCase] = []
    errors: dict[int, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get("type")
        if kind == "suite":
            suite = event.get("suite") or {}
            suites[int(suite.get("id", -1))] = str(suite.get("path") or "")
        elif kind == "testStart":
            test = event.get("test") or {}
            if test.get("url") is None and str(test.get("name", "")).startswith("loading "):
                continue  # carga do arquivo, não é teste
            started[int(test.get("id", -1))] = (str(test.get("name", "")), suites.get(int(test.get("suiteID", -1)), ""))
        elif kind == "error":
            tid = int(event.get("testID", -1))
            errors[tid] = (errors.get(tid, "") + "\n" + str(event.get("error") or "")).strip()
        elif kind == "print" and "EXCEPTION CAUGHT" in str(event.get("message") or ""):
            # o `error` do flutter_test só diz "See exception logs above": o esperado/obtido vem no log do teste
            tid = int(event.get("testID", -1))
            errors[tid] = (str(event.get("message")) + "\n" + errors.get(tid, "")).strip()
        elif kind == "testDone" and int(event.get("testID", -1)) in started and not event.get("hidden"):
            tid = int(event["testID"])
            name, path = started[tid]
            result = str(event.get("result", "success"))
            status = (
                SKIPPED
                if event.get("skipped")
                else PASSED
                if result == "success"
                else FAILED
                if result == "failure"
                else ERROR
            )
            cases.append(TestCase(name, path, path, status, errors.get(tid, "")[:1500]))
    return cases


def lcov(text: str) -> float | None:
    """Percentual de linhas cobertas de um `lcov.info` (soma de LH/LF)."""
    hit = found = 0
    for line in text.splitlines():
        if line.startswith("LH:"):
            hit += int(line[3:] or 0)
        elif line.startswith("LF:"):
            found += int(line[3:] or 0)
    return round(100 * hit / found, 1) if found else None


def lcov_lines(text: str) -> dict[str, dict[int, int]]:
    """Cobertura linha a linha de um `lcov.info`: arquivo → {linha: execuções}."""
    out: dict[str, dict[int, int]] = {}
    current: dict[int, int] | None = None
    for line in text.splitlines():
        if line.startswith("SF:"):
            current = out.setdefault(line[3:].strip().replace("\\", "/"), {})
        elif line.startswith("DA:") and current is not None:
            number, hits = line[3:].split(",")[:2]
            current[int(number)] = int(hits)
    return out


def jacoco(text: str) -> float | None:
    """Percentual de linhas cobertas do relatório XML do JaCoCo (contador LINE do relatório inteiro)."""
    root = ET.fromstring(text)  # noqa: S314
    for counter in root.findall("counter"):
        if counter.get("type") == "LINE":
            covered, missed = int(counter.get("covered", 0)), int(counter.get("missed", 0))
            return round(100 * covered / (covered + missed), 1) if covered + missed else None
    return None


def jacoco_lines(text: str) -> dict[str, dict[int, int]]:
    """Cobertura linha a linha do JaCoCo: `pacote/Arquivo.java` → {linha: 1 coberta | 0 não}."""
    root = ET.fromstring(text)  # noqa: S314
    out: dict[str, dict[int, int]] = {}
    for package in root.iter("package"):
        for source in package.iter("sourcefile"):
            lines = out.setdefault(f"{package.get('name', '')}/{source.get('name', '')}", {})
            for line in source.iter("line"):
                lines[int(line.get("nr", 0))] = 1 if int(line.get("ci", 0)) > 0 else 0
    return out


def cobertura(text: str) -> float | None:
    root = ET.fromstring(text)  # noqa: S314
    rate = root.get("line-rate")
    return round(float(rate) * 100, 1) if rate is not None else None


def dotnet_vulnerable(output: str) -> list[Finding]:
    findings = []
    for match in re.finditer(r">\s+(\S+)\s+\S+\s+\S+\s+(Low|Moderate|High|Critical)\s+(\S+)", output):
        sev = {"Moderate": "medium"}.get(match.group(2), match.group(2).lower())
        findings.append(Finding("dotnet-vulnerable", f"{match.group(1)}: {match.group(3)}", sev))
    return findings


def mutation_score(data: dict[str, Any]) -> float | None:
    """Pontuação de mutação (mortos / avaliados). mutmut: `export-cicd-stats`; Stryker: `mutation-report.json`."""
    if "killed" in data and "survived" in data:
        killed = int(data.get("killed", 0)) + int(data.get("timeout", 0))
        evaluated = killed + int(data.get("survived", 0)) + int(data.get("suspicious", 0))
        return killed / evaluated if evaluated else None
    statuses = [str(m.get("status")) for f in dict(data.get("files") or {}).values() for m in f.get("mutants") or []]
    killed = sum(1 for st in statuses if st in ("Killed", "Timeout"))
    evaluated = sum(1 for st in statuses if st in ("Killed", "Timeout", "Survived", "NoCoverage"))
    return killed / evaluated if evaluated else None
