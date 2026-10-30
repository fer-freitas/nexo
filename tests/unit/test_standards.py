"""motor do padrão de código: cada tipo de regra, camadas de configuração e medição."""

from __future__ import annotations

import re

import pytest

from nexo.kernel.quality.standards import Rule, check, glob_match, merge_layers, parse_rules, summary, to_sarif


def rule(**kw: object) -> Rule:
    return Rule.from_mapping({"id": "T-RULE", "title": "t", "message": "m", **kw})


def test_forbidden_import_by_stack() -> None:
    py = rule(kind="forbidden_import", severity="error", paths=["**/domain/**"], params={"imports": ["^requests"]})
    files = {
        "src/app/domain/estorno.py": "import json\nfrom requests import get\n",
        "src/app/infra/http.py": "import requests\n",
    }
    found = check(files, [py], "python")
    assert [(v.file, v.line, v.severity) for v in found] == [("src/app/domain/estorno.py", 2, "error")]
    cs = rule(
        kind="forbidden_import", paths=["**/*.Domain/**"], params={"imports": ["^Microsoft\\.EntityFrameworkCore"]}
    )
    src = "using System;\nusing Microsoft.EntityFrameworkCore;\nnamespace Loja.Domain;\n"
    assert [v.line for v in check({"src/Loja.Domain/Compra.cs": src}, [cs], "dotnet")] == [2]
    ts = rule(kind="forbidden_import", paths=["**/domain/**"], params={"imports": ["^axios"]})
    tsx = "import { x } from './x';\nimport axios from 'axios';\nconst y = require('axios');\n"
    assert [v.line for v in check({"src/domain/pedido.ts": tsx}, [ts], "react")] == [2, 3]


def test_forbidden_pattern_reports_each_line() -> None:
    r = rule(kind="forbidden_pattern", params={"pattern": "catch\\s*(\\([^)]*\\))?\\s*\\{\\s*\\}"})
    src = "class A {\n  void F() {\n    try { G(); } catch (Exception) { }\n    try { H(); } catch {}\n  }\n}\n"
    assert [v.line for v in check({"A.cs": src}, [r], "dotnet")] == [3, 4]


def test_function_size_and_params_python() -> None:
    lines = "\n".join(f"    x{i} = {i}" for i in range(35))
    src = f"def longa(a, b):\n{lines}\n\n\nclass S:\n    def m(self, a, b, c, d, e, f):\n        return a\n"
    size = rule(kind="max_function_lines", params={"limit": 30})
    params = rule(kind="max_params", params={"limit": 5})
    assert [v.message for v in check({"x.py": src}, [size], "python")] == ["m: longa (35 > 30)"]
    assert [v.message for v in check({"x.py": src}, [params], "python")] == ["m: m (6 > 5)"]  # self não conta


def test_function_size_brace_languages() -> None:
    body = "\n".join("        var x = 1;" for _ in range(32))
    cs = f"public class C\n{{\n    public void Grande(int a, int b)\n    {{\n{body}\n    }}\n}}\n"
    assert [
        v.message for v in check({"C.cs": cs}, [rule(kind="max_function_lines", params={"limit": 30})], "dotnet")
    ] == ["m: Grande (32 > 30)"]
    ts = "export function soma(a: number, b: number, c: number, d: number, e: number) {\n  return a;\n}\n"
    assert [v.line for v in check({"s.ts": ts}, [rule(kind="max_params", params={"limit": 4})], "react")] == [1]


def test_file_size_and_required_test() -> None:
    big = rule(kind="max_file_lines", params={"limit": 3})
    assert check({"a.py": "1\n2\n3\n4\n"}, [big], "python")[0].message == "m (4 > 3)"
    req = rule(kind="required_test", paths=["**/domain/**"], params={"test": "**/test_{stem}.py"})
    files = {"src/domain/estorno.py": "x = 1\n", "src/domain/compra.py": "y = 1\n"}
    existing = {*files, "tests/unit/test_estorno.py"}
    assert [v.file for v in check(files, [req], "python", existing)] == ["src/domain/compra.py"]


def test_layers_override_and_disable() -> None:
    factory = [{"id": "A-ONE", "title": "a", "kind": "max_params", "severity": "warning", "params": {"limit": 3}}]
    org = [{"id": "A-ONE", "severity": "error"}, {"id": "B-TWO", "title": "b", "kind": "max_file_lines"}]
    project = [{"id": "B-TWO", "disabled": True}]
    rules = parse_rules(merge_layers(factory, org, project))
    assert [(r.id, r.severity, r.params) for r in rules] == [("A-ONE", "error", {"limit": 3})]
    assert parse_rules(merge_layers(factory), disabled=("A-ONE",)) == []


@pytest.mark.parametrize(
    "data,message",
    [
        ({"id": "minúscula", "kind": "max_params"}, "id de regra inválido"),
        ({"id": "A-ONE", "kind": "llm"}, "tipo desconhecido"),
        ({"id": "A-ONE", "kind": "max_params", "severity": "fatal"}, "severidade"),
    ],
)
def test_invalid_rules_fail_on_load(data: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Rule.from_mapping(data)
    with pytest.raises(re.error):  # regex inválida também falha ao carregar
        Rule.from_mapping({"id": "A-ONE", "kind": "forbidden_pattern", "params": {"pattern": "("}})


def test_summary_and_sarif() -> None:
    r = rule(kind="forbidden_pattern", severity="error", params={"pattern": "print\\("}, skill="solid-e-clean-code")
    found = check({"a.py": "print(1)\nprint(2)\n", "b.py": "x = 1\n"}, [r], "python")
    assert summary(found, 2) == {
        "files": 2,
        "files_ok": 1,
        "violations": 2,
        "errors": 2,
        "preexisting": 0,
        "rules": {"T-RULE": 2},
    }
    sarif = to_sarif(found, [r])
    result = sarif["runs"][0]["results"][0]
    assert (
        result["ruleId"] == "T-RULE"
        and result["level"] == "error"
        and "solid-e-clean-code" in result["message"]["text"]
    )


def test_glob_matches_top_level_folder() -> None:
    assert glob_match("domain/x.py", "**/domain/**") and glob_match("src/a/domain/x.py", "**/domain/**")
    assert not glob_match("src/domainx/x.py", "**/domain/**")
