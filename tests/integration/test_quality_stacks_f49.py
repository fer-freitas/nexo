"""executor de receitas para Java, Angular e Flutter: relatórios por curinga, argumentos opcionais, wrapper do
projeto, cobertura LCOV/JaCoCo relativa ao projeto e o CI gerado. Ferramentas simuladas com o próprio Python."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from nexo.modules.quality.adapters.runner import java_tools, load_recipe, run_stage

PY = sys.executable
JUNIT = '<testsuite name="{0}"><testcase classname="{0}" name="ok"/>{1}</testsuite>'
LCOV = "SF:lib/a.dart\nDA:1,1\nDA:2,0\nLF:2\nLH:1\nend_of_record\n"


def stage(**kw):  # type: ignore[no-untyped-def]
    return {"id": "unit", "kind": "unit", "parser": "exitcode", "required": True, **kw}


def _write(code: str) -> list[str]:
    return [PY, "-c", code]


def test_junit_report_glob_merges_one_file_per_class(tmp_path: Path) -> None:
    code = (
        "import pathlib; d = pathlib.Path('target/surefire-reports'); d.mkdir(parents=True, exist_ok=True)\n"
        f"(d / 'TEST-a.A.xml').write_text({JUNIT.format('a.A', '')!r})\n"
        f"(d / 'TEST-b.B.xml').write_text({JUNIT.format('b.B', '<testcase classname="b.B" name="f"><failure message="x"/></testcase>')!r})\n"
    )
    r = run_stage(
        stage(parser="junit", report="target/surefire-reports/TEST-*.xml", run=_write(code)),
        tmp_path,
        tmp_path / "o",
        {},
    )
    assert r.status == "failed" and len(r.tests) == 3 and [t.classname for t in r.failed_tests] == ["b.B"]
    empty = tmp_path / "vazio"
    empty.mkdir()
    missing = run_stage(stage(parser="junit", report="target/x/TEST-*.xml", run=_write("1")), empty, tmp_path / "o", {})
    assert missing.status == "failed" and "não gerado" in missing.summary


def test_flutter_machine_stage_reads_stdout_and_lcov(tmp_path: Path) -> None:
    events = [
        '{"suite":{"id":0,"path":"test/a_test.dart"},"type":"suite"}',
        '{"test":{"id":1,"name":"soma","suiteID":0,"url":"file:///x"},"type":"testStart"}',
        '{"testID":1,"result":"success","skipped":false,"hidden":false,"type":"testDone"}',
    ]
    code = (
        "import pathlib; pathlib.Path('cov').mkdir(exist_ok=True)\n"
        f"pathlib.Path('cov/lcov.info').write_text({LCOV!r})\n"
        f"print({chr(10).join(events)!r})\n"
    )
    st = stage(parser="flutter_machine", coverage="cov/lcov.info", coverage_format="lcov", run=_write(code))
    r = run_stage(st, tmp_path, tmp_path / "o", {})
    assert r.status == "passed" and [t.name for t in r.tests] == ["soma"] and r.coverage == 50.0
    crash = run_stage(
        stage(parser="flutter_machine", run=_write("import sys; sys.exit(1)")), tmp_path, tmp_path / "o", {}
    )
    assert crash.status == "error"


def test_stale_coverage_is_not_reported_as_this_run(tmp_path: Path) -> None:
    (tmp_path / "cov").mkdir()
    (tmp_path / "cov" / "lcov.info").write_text(LCOV, encoding="utf-8")
    st = stage(coverage="cov/lcov.info", coverage_format="lcov", run=_write("import sys; sys.exit(1)"))
    r = run_stage(st, tmp_path, tmp_path / "o", {})
    assert r.status == "failed" and r.coverage is None and not (tmp_path / "cov" / "lcov.info").exists()


def test_optional_args_and_glob_contains(tmp_path: Path) -> None:
    (tmp_path / "build.gradle.kts").write_text('plugins { id("jacoco") }\n', encoding="utf-8")
    code = "import sys, pathlib; pathlib.Path('args.txt').write_text(' '.join(sys.argv[1:]))"
    st = stage(
        when_contains={"file": "build.gradle*", "text": "jacoco"},
        run=[PY, "-c", code, "test"],
        optional_args=[
            {"when_contains": {"file": "build.gradle*", "text": "jacoco"}, "args": ["jacocoTestReport"]},
            {"when_exists": "nao-existe", "args": ["--nunca"]},
        ],
    )
    assert run_stage(st, tmp_path, tmp_path / "o", {}).status == "passed"
    assert (tmp_path / "args.txt").read_text(encoding="utf-8") == "test jacocoTestReport"
    (tmp_path / "build.gradle.kts").write_text("plugins {}\n", encoding="utf-8")
    assert run_stage(st, tmp_path, tmp_path / "o", {}).status == "skipped"


def test_java_wrapper_preferred_and_checked(tmp_path: Path) -> None:
    assert java_tools(tmp_path) == {"mvn": "mvn", "gradle": "gradle"}
    wrapper = tmp_path / ("mvnw.cmd" if os.name == "nt" else "mvnw")
    wrapper.write_text("", encoding="utf-8")
    assert java_tools(tmp_path)["mvn"] == str(wrapper.resolve())
    assert java_tools(tmp_path, ci=True)["mvn"] in ("./mvnw", "mvn")  # o CI roda no Linux
    subs = {"mvn": str(tmp_path / "sem-wrapper")}
    r = run_stage(stage(tool="{mvn}", run=["{mvn}", "test"]), tmp_path, tmp_path / "o", subs)
    assert r.status == "error" and "ausente" in r.summary


def test_recipes_load_for_new_stacks() -> None:
    for name in ("java", "angular", "flutter"):
        recipe = load_recipe(name)
        ids = [s["id"] for s in recipe["stages"]]
        assert "unit" in ids and "standards" in ids and "secrets" in ids, name


def test_new_tests_skip_type_analyzer_but_keep_format(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test-first (achado no e2e Flutter): o teste de aceite cita código que ainda não existe."""
    from nexo.modules.quality.adapters import runner

    calls: list[str] = []
    recipe = {
        "stages": [
            {"id": "format", "kind": "lint", "changed_only": [".dart"], "run": [*_write("1"), "."]},
            {"id": "lint", "kind": "lint", "changed_only": [".dart"], "skip_on_new_tests": True, "run": ["x", "."]},
        ]
    }
    monkeypatch.setattr(runner, "load_recipe", lambda stack, brain=None: recipe)
    real = runner.run_stage
    monkeypatch.setattr(runner, "run_stage", lambda st, *a, **k: calls.append(st["id"]) or real(st, *a, **k))
    assert runner.lint_files("flutter", tmp_path, tmp_path / "o", ["test/a_test.dart"]) == []
    assert calls == ["format"]


def test_missing_report_says_why(tmp_path: Path) -> None:
    code = "import sys; print('\\x1b[31m✘ [ERROR] TS2307: Cannot find module node:fs\\x1b[0m'); sys.exit(1)"
    r = run_stage(
        stage(parser="junit", report="{out}/j.xml", run=_write(code)),
        tmp_path,
        tmp_path / "o",
        {"out": str(tmp_path / "o")},
    )
    assert r.status == "error" and "TS2307: Cannot find module node:fs" in r.summary


def test_build_failure_points_to_file_and_is_not_environment(tmp_path: Path) -> None:
    from nexo.kernel.quality.model import QualityReport
    from nexo.modules.execution.app.story.quality import environment_errors

    out = (
        "[ERROR] COMPILATION ERROR : \\n[ERROR] /w/src/test/java/PedidoTest.java:[106,9] cannot find symbol\\n"
        "[ERROR] -> [Help 1]"
    )
    code = f"import sys; print({out!r}.replace('\\\\n', chr(10))); sys.exit(1)"
    build = run_stage({**stage(run=_write(code)), "id": "build", "kind": "build"}, tmp_path, tmp_path / "o", {})
    assert build.status == "failed" and "PedidoTest.java:[106,9] cannot find symbol" in build.summary
    unit = run_stage(
        stage(parser="junit", report="{out}/j.xml", run=_write(code)),
        tmp_path,
        tmp_path / "o",
        {"out": str(tmp_path / "o")},
    )
    assert unit.status == "error" and "PedidoTest.java:[106,9]" in unit.summary
    assert environment_errors(QualityReport("java", [build, unit])) == []  # vai para correção do dev
    assert environment_errors(QualityReport("java", [unit])) == [unit]  # sem build reprovado: ambiente
