"""Java, Angular e Flutter: leitores de resultado (saídas reais das ferramentas) e padrão de código."""

from __future__ import annotations

from pathlib import Path

import pytest

from nexo.kernel.quality.standards import check
from nexo.modules.quality.adapters import parsers
from nexo.modules.quality.adapters.standards import load_rules

# `flutter test --machine` do Flutter 3.47 (app do `flutter create` com a asserção quebrada), caminhos encurtados
FLUTTER_FAIL = r"""{"protocolVersion":"0.1.1","runnerVersion":null,"pid":1,"type":"start","time":0}
{"suite":{"id":0,"platform":"vm","path":"/app/test/widget_test.dart"},"type":"suite","time":0}
{"test":{"id":1,"name":"loading /app/test/widget_test.dart","suiteID":0,"groupIDs":[],"metadata":{"skip":false,"skipReason":null},"line":null,"column":null,"url":null},"type":"testStart","time":0}
[{"event":"test.startedProcess","params":{"vmServiceUri":"http://127.0.0.1:1/x/"}}]
{"testID":1,"result":"success","skipped":false,"hidden":true,"type":"testDone","time":1904}
{"test":{"id":3,"name":"Counter increments smoke test","suiteID":0,"groupIDs":[2],"metadata":{"skip":false,"skipReason":null},"line":174,"column":5,"url":"package:flutter_test/src/widget_tester.dart","root_line":14,"root_column":3,"root_url":"file:///app/test/widget_test.dart"},"type":"testStart","time":1905}
{"testID":3,"messageType":"print","message":"══╡ EXCEPTION CAUGHT BY FLUTTER TEST FRAMEWORK ╞══\nExpected: exactly one matching candidate\n  Actual: _TextWidgetFinder:<Found 0 widgets with text \"99\": []>","type":"print","time":2489}
{"testID":3,"error":"Test failed. See exception logs above.","stackTrace":"","isFailure":false,"type":"error","time":2490}
{"testID":3,"result":"error","skipped":false,"hidden":false,"type":"testDone","time":2491}
{"test":{"id":4,"name":"soma","suiteID":0,"groupIDs":[2],"metadata":{"skip":false,"skipReason":null},"line":5,"column":3,"url":"file:///app/test/widget_test.dart"},"type":"testStart","time":2500}
{"testID":4,"result":"success","skipped":false,"hidden":false,"type":"testDone","time":2501}
{"test":{"id":5,"name":"pendente","suiteID":0,"groupIDs":[2],"metadata":{"skip":true,"skipReason":"x"},"line":9,"column":3,"url":"file:///app/test/widget_test.dart"},"type":"testStart","time":2502}
{"testID":5,"result":"success","skipped":true,"hidden":false,"type":"testDone","time":2503}
{"success":false,"type":"done","time":2600}
"""

LCOV = """SF:lib/main.dart
DA:3,0
DA:4,0
DA:8,1
LF:3
LH:1
end_of_record
SF:lib/domain/pedido.dart
DA:1,2
LF:1
LH:1
end_of_record
"""

# relatório do JaCoCo 0.8.13 (Spring Initializr), resumido
JACOCO = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<report name="api"><sessioninfo id="x" start="1" dump="2"/>
<package name="com/exemplo/api"><class name="com/exemplo/api/ApiApplication" sourcefilename="ApiApplication.java">
<counter type="LINE" missed="2" covered="1"/></class>
<sourcefile name="ApiApplication.java"><line nr="7" mi="0" ci="3" mb="0" cb="0"/><line nr="10" mi="4" ci="0" mb="0" cb="0"/>
<line nr="11" mi="1" ci="0" mb="0" cb="0"/><counter type="LINE" missed="2" covered="1"/></sourcefile>
<counter type="LINE" missed="2" covered="1"/></package>
<counter type="INSTRUCTION" missed="5" covered="3"/><counter type="LINE" missed="2" covered="1"/></report>
"""


def test_flutter_machine_reads_results_and_failure_detail() -> None:
    cases = parsers.flutter_machine(FLUTTER_FAIL)
    assert [(c.name, c.status) for c in cases] == [
        ("Counter increments smoke test", "error"),
        ("soma", "passed"),
        ("pendente", "skipped"),
    ]  # a carga do arquivo (oculta) não vira teste
    assert cases[0].file == "/app/test/widget_test.dart"
    assert "Found 0 widgets" in cases[0].message and "See exception logs" in cases[0].message
    assert parsers.flutter_machine("saída sem json\n") == []


def test_lcov_totals_and_lines() -> None:
    assert parsers.lcov(LCOV) == 50.0
    assert parsers.lcov_lines(LCOV)["lib/main.dart"] == {3: 0, 4: 0, 8: 1}
    assert parsers.lcov("") is None


def test_jacoco_uses_report_line_counter_and_source_paths() -> None:
    assert parsers.jacoco(JACOCO) == 33.3
    assert parsers.jacoco_lines(JACOCO) == {"com/exemplo/api/ApiApplication.java": {7: 1, 10: 0, 11: 0}}
    assert parsers.jacoco("<report/>") is None


def _factory(stack: str, tmp_path: Path) -> list:  # type: ignore[type-arg]
    return load_rules(stack, tmp_path)


def test_java_factory_rules(tmp_path: Path) -> None:
    rules = _factory("java", tmp_path)
    src = (
        "package com.loja.domain;\n"
        "import java.util.List;\n"
        "import org.springframework.stereotype.Service;\n"
        "public class Pedido {\n"
        "  @Autowired private Repo repo;\n"
        "  public void fechar(int a, int b, int c, int d, int e) {\n"
        "    try { x(); } catch (Exception ex) { }\n"
        "    System.out.println(a);\n"
        "  }\n"
        "}\n"
    )
    found = {(v.rule, v.line) for v in check({"src/main/java/com/loja/domain/Pedido.java": src}, rules, "java")}
    assert {
        ("DDD-DOMAIN-INFRA", 3),
        ("JAVA-AUTOWIRED-CAMPO", 5),
        ("CLEAN-MUITOS-PARAMETROS", 6),
        ("CLEAN-CATCH-VAZIO", 7),
        ("CLEAN-SYSTEM-OUT", 8),
        ("DDD-TESTE-DO-DOMINIO", 1),
    } <= found
    files = {
        "src/main/java/com/loja/domain/Pedido.java": "package com.loja.domain;\npublic class Pedido {}\n",
        "src/test/java/com/loja/domain/PedidoTest.java": "class PedidoTest {}\n",
    }
    assert check(files, rules, "java") == []


def test_angular_factory_rules(tmp_path: Path) -> None:
    rules = _factory("angular", tmp_path)
    domain = "import { Injectable } from '@angular/core';\nexport function total(x: any) { return x; }\n"
    comp = (
        "import { HttpClient } from '@angular/common/http';\n"
        "export class PedidoComponent {\n"
        "  constructor(private http: HttpClient) {}\n"
        "  salvar() {\n    console.log('x');\n  }\n}\n"
    )
    found = {
        (v.rule, v.file, v.line)
        for v in check({"src/app/domain/total.ts": domain, "src/app/pedido.component.ts": comp}, rules, "angular")
    }
    assert ("DDD-DOMAIN-INFRA", "src/app/domain/total.ts", 1) in found
    assert ("CLEAN-ANY", "src/app/domain/total.ts", 2) in found
    assert ("ANGULAR-HTTP-NO-COMPONENTE", "src/app/pedido.component.ts", 1) in found
    assert ("CLEAN-CONSOLE-LOG", "src/app/pedido.component.ts", 5) in found


def test_flutter_factory_rules(tmp_path: Path) -> None:
    rules = _factory("flutter", tmp_path)
    domain = "import 'package:flutter/material.dart';\nimport '../data/api.dart';\nclass Pedido {}\n"
    page = (
        "class _S extends State<P> {\n"
        "  Future<void> carregar() async {\n"
        "    final x = await api.buscar();\n"
        "    setState(() => itens = x);\n"
        "    print(x);\n"
        "    try { y(); } catch (e) { }\n"
        "  }\n"
        "}\n"
    )
    found = {
        (v.rule, v.file, v.line)
        for v in check({"lib/domain/pedido.dart": domain, "lib/ui/pedidos_page.dart": page}, rules, "flutter")
    }
    assert {
        ("DDD-DOMAIN-INFRA", "lib/domain/pedido.dart", 1),
        ("DDD-DOMAIN-INFRA", "lib/domain/pedido.dart", 2),
        ("DDD-TESTE-DO-DOMINIO", "lib/domain/pedido.dart", 1),
        ("FLUTTER-SETSTATE-ASSINCRONO", "lib/ui/pedidos_page.dart", 3),
        ("FLUTTER-PRINT", "lib/ui/pedidos_page.dart", 5),
        ("CLEAN-CATCH-VAZIO", "lib/ui/pedidos_page.dart", 6),
    } <= found
    safe = page.replace("    setState", "    if (!mounted) return;\n    setState")
    rules_ok = {v.rule for v in check({"lib/ui/pedidos_page.dart": safe}, rules, "flutter")}
    assert "FLUTTER-SETSTATE-ASSINCRONO" not in rules_ok
    # código gerado (build_runner) fica fora do limite de tamanho de arquivo
    big = "\n".join(f"// {i}" for i in range(500))
    assert not check({"lib/x.g.dart": big}, rules, "flutter")


def test_flutter_long_build_counts(tmp_path: Path) -> None:
    rules = [r for r in _factory("flutter", tmp_path) if r.id == "CLEAN-FUNCAO-LONGA"]
    body = "\n".join("      Text('x')," for _ in range(70))
    src = (
        f"class A extends StatelessWidget {{\n  @override\n  Widget build(BuildContext context) {{\n{body}\n  }}\n}}\n"
    )
    assert [v.message.split(": ")[1].split(" ")[0] for v in check({"lib/a.dart": src}, rules, "flutter")] == ["build"]


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "deps"


@pytest.mark.parametrize(
    ("command", "allowed"),
    [
        ("./mvnw -B test", True),
        ("./gradlew test", True),
        ("npx ng test --watch=false", True),
        ("flutter test test/domain/pedido_test.dart", True),
        ("dart run build_runner build --delete-conflicting-outputs", True),
        ("dart run tool/calcula.dart", True),
        ("cat > /tmp/p.dart <<'E'\nvoid main(){}\nE", False),
        ('python3 -c "print(1)"', False),
        ("flutter pub add dio", False),
        ("dart pub add http", False),
        ("npx ng add @angular/material", False),
        ("ng update @angular/core", False),
        ("./mvnw deploy", False),
        ("./mvnw versions:set -DnewVersion=2", False),
        ("gradle publish", False),
    ],
)
def test_policy_for_new_stacks(command: str, allowed: bool) -> None:
    from nexo.kernel.runtime.resources import load_yaml
    from nexo.kernel.security.policy import Action, Policy, evaluate

    policy = Policy.from_mapping(load_yaml("policy.yaml"))
    assert evaluate(Action("shell", "Bash", command=command), "/w", policy).allow is allowed


def test_added_dependencies_only_inside_manifests() -> None:
    from nexo.kernel.learning.model import added_dependencies

    diff = (
        "+++ b/pubspec.yaml\n"
        "+  dio: ^5.4.0\n"
        "+  sdk: ^3.5.0\n"
        "+++ b/docker-compose.yaml\n"
        "+  version: 3\n"
        "+  redis: 7\n"
        "+++ b/pom.xml\n"
        "+      <artifactId>guava</artifactId>\n"
        "+++ b/build.gradle.kts\n"
        '+    implementation("com.squareup.okhttp3:okhttp:4.12.0")\n'
    )
    assert added_dependencies(diff) == {("pub", "dio"), ("maven", "guava"), ("maven", "com.squareup.okhttp3:okhttp")}


def test_registries_pub_and_maven(monkeypatch: pytest.MonkeyPatch) -> None:
    from nexo.kernel.dependencies import registries

    pages = {
        "https://pub.dev/api/packages/dio": {
            "latest": {"version": "5.11.1"},
            "versions": [{"version": "1.0.0", "published": "2018-04-21T17:17:44.390253Z"}],
        },
        "https://pub.dev/api/packages/dio/score": {"tags": ["license:mit", "license:osi-approved"]},
    }
    monkeypatch.setattr(registries, "_get", lambda url: pages.get(url))
    info = registries.package_info("pub", "dio")
    assert info["exists"] and info["latest"] == "5.11.1" and info["license"] == "MIT" and info["age_days"] > 365
    assert registries.package_info("pub", "nao-existe") == {"exists": False}
    xml = "<metadata><versioning><latest>33.7.2-jre</latest><release>33.7.2-jre</release></versioning></metadata>"
    monkeypatch.setattr(registries, "_get_text", lambda url: xml if "com/google/guava/guava/" in url else None)
    assert registries.package_info("maven", "com.google.guava:guava")["latest"] == "33.7.2-jre"
    assert registries.package_info("maven", "x.y:z") == {"exists": False}
    with pytest.raises(ValueError):
        registries.package_info("maven", "guava")
    assert registries.OSV_ECOSYSTEM["pub"] == "Pub" and registries.OSV_ECOSYSTEM["maven"] == "Maven"


def test_traceability_by_test_file_path() -> None:
    from nexo.kernel.quality.model import TestCase
    from nexo.kernel.quality.traceability import trace

    vitest = TestCase("Pedido > recusa vazio", "src/app/domain/pedido.spec.ts", "", "passed")
    flutter = TestCase("recusa vazio", "/w/test/pedido_test.dart", "/w/test/pedido_test.dart", "failed")
    tags = {"src/app/domain/pedido.spec.ts": {"AC-1"}, "test/pedido_test.dart": {"AC-2"}}
    result = {t.ac: t.status for t in trace(["AC-1", "AC-2"], [vitest, flutter], tags)}
    assert result == {"AC-1": "passed", "AC-2": "failed"}


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("src/test/java/com/loja/web/SimulacaoControllerTest.java", True),  # achado no e2e Java
        ("modulo/src/test/java/com/loja/PedidoIT.java", True),
        ("test/domain/parcelas_test.dart", True),
        ("packages/core/test/helpers.dart", True),
        ("integration_test/app_test.dart", True),
        ("src/main/java/com/loja/Pedido.java", False),
        ("lib/domain/parcelas.dart", False),
        (".nexo/tmp/calc.dart", True),  # rascunho dos agentes (achado no e2e Flutter)
        ("tool_calc.dart", False),
    ],
)
def test_test_writer_paths_for_new_stacks(path: str, allowed: bool) -> None:
    from nexo.kernel.runtime.resources import load_yaml
    from nexo.kernel.security.policy import Action, Policy, evaluate

    policy = Policy.from_mapping(load_yaml("policy.yaml"))
    decision = evaluate(Action("write", "Write", path=f"/w/{path}"), "/w", policy, profile="test-writer")
    assert decision.allow is allowed


def test_scratch_area_for_agents() -> None:
    from nexo.kernel.runtime.resources import load_yaml
    from nexo.kernel.security.policy import Action, Policy, evaluate
    from nexo.modules.setup.app.project import GITIGNORE_LINES

    assert "tmp/" in GITIGNORE_LINES  # .nexo/tmp fica fora do git
    policy = Policy.from_mapping(load_yaml("policy.yaml"))
    for command in (
        "mkdir -p .nexo/tmp && cat > .nexo/tmp/calc.dart <<'E'\nvoid main() => print(1);\nE",
        "dart run .nexo/tmp/calc.dart",
        "rm .nexo/tmp/calc.dart",
    ):
        assert evaluate(Action("shell", "Bash", command=command), "/w", policy, profile="test-writer").allow, command
    for name in ("qa-author", "developer", "characterization"):
        assert ".nexo/tmp/" in load_yaml_text(name)


def load_yaml_text(prompt: str) -> str:
    from nexo.kernel.runtime.resources import read_text

    return read_text(f"prompts/{prompt}.md")


def test_fix_task_kind_is_from_code_tasks() -> None:
    from types import SimpleNamespace

    from nexo.modules.execution.app.story.bugs import fix_kind

    tasks = [SimpleNamespace(kind="contract"), SimpleNamespace(kind="test"), SimpleNamespace(kind="frontend")]
    assert fix_kind(tasks) == "frontend"
    assert fix_kind([SimpleNamespace(kind="contract")]) == "backend"
