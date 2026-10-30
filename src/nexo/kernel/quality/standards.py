"""Padrão de código garantido: regras determinísticas por stack, verificação dos arquivos e medição.

As regras vêm das skills (DDD tático, SOLID e código limpo, padrões de projeto) e viram verificações sem IA:
dependência proibida entre camadas, construção proibida, tamanho de função, número de parâmetros, tamanho de
arquivo e teste obrigatório. Regra `error` reprova a história no portão; `warning` aparece no relatório e no painel.
"""

from __future__ import annotations

import ast
import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

KINDS = (
    "forbidden_import",
    "forbidden_pattern",
    "max_function_lines",
    "max_params",
    "max_file_lines",
    "required_test",
)
SEVERITY = {"error": "high", "warning": "medium"}  # severidade do achado no pipeline (alta bloqueia)
RULE_ID = re.compile(r"^[A-Z][A-Z0-9]*(-[A-Z0-9]+){1,4}$")  # vai para a telemetria: identificador, nunca texto
EXTENSIONS = {
    "python": (".py",),
    "dotnet": (".cs",),
    "react": (".ts", ".tsx", ".js", ".jsx"),
    "java": (".java", ".kt"),
    "angular": (".ts",),
    "flutter": (".dart",),
}
IMPORTS = {
    "python": re.compile(r"^\s*(?:from\s+([\w.]+)\s+import\b|import\s+([\w.]+))"),
    "dotnet": re.compile(r"^\s*(?:global\s+)?using\s+(?:static\s+)?(?:\w+\s*=\s*)?([\w.]+)\s*;"),
    "react": re.compile(r"""(?:\bfrom\s*|\bimport\s*\(?\s*|\brequire\(\s*)['"]([^'"]+)['"]"""),
    "angular": re.compile(r"""(?:\bfrom\s*|\bimport\s*\(?\s*)['"]([^'"]+)['"]"""),
    "java": re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)"),
    "flutter": re.compile(r"""^\s*(?:import|export)\s+['"]([^'"]+)['"]"""),
}
# Início de função nas linguagens com chaves (heurística: assinatura seguida de `{`)
BRACE_FUNCTIONS = {
    "dotnet": re.compile(
        r"^\s*(?:(?:public|private|protected|internal|static|async|override|virtual|sealed|abstract|partial)\s+)+"
        r"[\w<>\[\],.?\s]+?\s+(\w+)\s*(?:<[^>]*>)?\s*\(([^)]*)\)\s*(?:where\s[^{]*)?\{?\s*$"
    ),
    "react": re.compile(
        r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function\s+(\w+)\s*\(([^)]*)\)"
        r"|(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?\(([^)]*)\)\s*(?::[^=]+)?=>)\s*[^;]*\{?\s*$"
    ),
    "java": re.compile(
        r"^\s*(?:(?:public|private|protected|static|final|abstract|synchronized|default)\s+)+"
        r"(?:<[^>]*>\s*)?[\w<>\[\],.?\s]+?\s+(\w+)\s*\(([^)]*)\)\s*(?:throws\s[^{]*)?\{?\s*$"
    ),
    # métodos de classe (componentes, serviços) e funções soltas do TypeScript
    "angular": re.compile(
        r"^\s*(?:(?:public|private|protected|static|async|override|readonly)\s+)*(?:export\s+)?(?:function\s+)?"
        r"(\w+)\s*(?:<[^>]*>)?\s*\(([^)]*)\)\s*(?::\s*[^={]+)?\{\s*$"
    ),
    "flutter": re.compile(
        r"^\s*(?:(?:static|final|external|@override)\s+)*(?:[\w<>?,\[\]]+\s+)?(\w+)\s*\(([^)]*)\)\s*"
        r"(?:async\*?|sync\*)?\s*\{\s*$"
    ),
}


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    kind: str
    severity: str = "warning"
    stacks: tuple[str, ...] = ()  # vazio = todas
    paths: tuple[str, ...] = ("**",)
    exclude: tuple[str, ...] = ()
    params: dict[str, Any] = field(default_factory=dict)
    skill: str = ""
    message: str = ""

    @staticmethod
    def from_mapping(data: dict[str, Any]) -> Rule:
        rid = str(data.get("id", ""))
        if not RULE_ID.match(rid):
            raise ValueError(f"id de regra inválido: {rid!r} (ex.: DDD-DOMAIN-INFRA)")
        kind = str(data.get("kind", ""))
        if kind not in KINDS:
            raise ValueError(f"{rid}: tipo desconhecido {kind!r} (use {', '.join(KINDS)})")
        severity = str(data.get("severity", "warning"))
        if severity not in SEVERITY:
            raise ValueError(f"{rid}: severidade {severity!r} (use error ou warning)")
        params = dict(data.get("params") or {})
        for key in ("pattern", "imports"):
            raw = params.get(key)
            for value in raw if isinstance(raw, list) else [raw]:
                if value:
                    re.compile(str(value))  # regra com regex inválida falha ao carregar, não ao verificar
        return Rule(
            rid,
            str(data.get("title") or rid),
            kind,
            severity,
            tuple(str(s) for s in data.get("stacks") or ()),
            tuple(str(p) for p in data.get("paths") or ("**",)),
            tuple(str(p) for p in data.get("exclude") or ()),
            params,
            str(data.get("skill") or ""),
            str(data.get("message") or data.get("title") or rid),
        )


@dataclass(frozen=True)
class Violation:
    rule: str
    file: str
    line: int
    message: str
    severity: str  # error | warning


def merge_layers(*layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fábrica → cérebro da org → projeto: mesma `id` sobrescreve campo a campo; `disabled: true` remove."""
    merged: dict[str, dict[str, Any]] = {}
    for layer in layers:
        for item in layer:
            rid = str(item.get("id", ""))
            merged[rid] = {**merged.get(rid, {}), **item}
    return [v for v in merged.values() if not v.get("disabled")]


def parse_rules(items: list[dict[str, Any]], disabled: tuple[str, ...] = ()) -> list[Rule]:
    return [Rule.from_mapping(i) for i in items if str(i.get("id")) not in disabled]


def glob_match(path: str, pattern: str) -> bool:
    """`**/domain/**` também casa com `domain/x.py` (sem pasta antes)."""
    if fnmatch.fnmatch(path, pattern):
        return True
    return pattern.startswith("**/") and fnmatch.fnmatch(path, pattern[3:])


def applies(rule: Rule, path: str, stack: str) -> bool:
    if rule.stacks and stack not in rule.stacks:
        return False
    if not path.endswith(EXTENSIONS.get(stack, ())) and rule.kind != "max_file_lines":
        return False
    return any(glob_match(path, p) for p in rule.paths) and not any(glob_match(path, p) for p in rule.exclude)


def _patterns(rule: Rule, key: str) -> list[re.Pattern[str]]:
    value = rule.params.get(key)
    values = value if isinstance(value, list) else [value] if value else []
    return [re.compile(str(v)) for v in values]


def _functions(text: str, stack: str) -> list[tuple[str, int, int, int]]:
    """(nome, linha inicial, linhas, parâmetros) de cada função ou método."""
    if stack == "python":
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return []
        out = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = [a.arg for a in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]]
                params = len([a for a in args if a not in ("self", "cls")])
                params += int(node.args.vararg is not None) + int(node.args.kwarg is not None)
                body_start = node.body[0].lineno if node.body else node.lineno
                out.append((node.name, node.lineno, (node.end_lineno or node.lineno) - body_start + 1, params))
        return out
    regex = BRACE_FUNCTIONS.get(stack)
    if regex is None:
        return []
    lines = text.splitlines()
    out = []
    for i, line in enumerate(lines):
        m = regex.match(line)
        if not m:
            continue
        groups = [g for g in m.groups() if g is not None]
        name, raw = (groups[0], groups[1]) if len(groups) >= 2 else (groups[0], "")
        if name in (
            "if",
            "for",
            "while",
            "switch",
            "catch",
            "using",
            "return",
            "foreach",
            "lock",
            "constructor",
        ):
            continue
        params = len([p for p in raw.split(",") if p.strip()])
        depth, first_open, end = 0, -1, i
        for j in range(i, min(len(lines), i + 2000)):
            depth += lines[j].count("{") - lines[j].count("}")
            if first_open < 0 and "{" in lines[j]:
                first_open = j
            if first_open >= 0 and depth <= 0:
                end = j
                break
        if first_open >= 0:  # corpo: entre a linha que abre e a que fecha a função
            out.append((name, i + 1, max(0, end - first_open - 1), params))
    return out


def check_file(path: str, text: str, rules: list[Rule], stack: str, existing: set[str]) -> list[Violation]:
    out: list[Violation] = []
    lines = text.splitlines()
    for rule in rules:
        if not applies(rule, path, stack):
            continue
        if rule.kind == "forbidden_import":
            regex, forbidden = IMPORTS.get(stack), _patterns(rule, "imports")
            for n, line in enumerate(lines, 1):
                m = regex.match(line) if regex is not None and stack not in ("react", "angular") else None
                targets = [g for g in m.groups() if g] if m else []
                if stack in ("react", "angular") and regex is not None:
                    targets = regex.findall(line)
                if any(f.search(t) for t in targets for f in forbidden):
                    out.append(Violation(rule.id, path, n, rule.message, rule.severity))
        elif rule.kind == "forbidden_pattern":
            for pattern in _patterns(rule, "pattern"):
                for m in pattern.finditer(text):
                    out.append(
                        Violation(rule.id, path, text.count("\n", 0, m.start()) + 1, rule.message, rule.severity)
                    )
        elif rule.kind == "max_file_lines":
            limit = int(rule.params.get("limit", 400))
            if len(lines) > limit:
                out.append(Violation(rule.id, path, 1, f"{rule.message} ({len(lines)} > {limit})", rule.severity))
        elif rule.kind in ("max_function_lines", "max_params"):
            limit = int(rule.params.get("limit", 30 if rule.kind == "max_function_lines" else 3))
            for name, start, size, params in _functions(text, stack):
                value = size if rule.kind == "max_function_lines" else params
                if value > limit:
                    out.append(
                        Violation(rule.id, path, start, f"{rule.message}: {name} ({value} > {limit})", rule.severity)
                    )
        elif rule.kind == "required_test":
            stem = PurePosixPath(path).stem
            test_glob = str(rule.params.get("test", "")).replace("{stem}", stem)
            if test_glob and not any(glob_match(f, test_glob) for f in existing):
                out.append(Violation(rule.id, path, 1, f"{rule.message} ({test_glob})", rule.severity))
    return out


def check(files: dict[str, str], rules: list[Rule], stack: str, existing: set[str] | None = None) -> list[Violation]:
    known = existing if existing is not None else set(files)
    out: list[Violation] = []
    for path in sorted(files):
        out += check_file(path, files[path], rules, stack, known)
    return out


def summary(violations: list[Violation], checked: int, preexisting: int = 0) -> dict[str, Any]:
    """Só números e ids de regra (vai para o central): arquivos, aderência e violações por regra."""
    rules: dict[str, int] = {}
    for v in violations:
        rules[v.rule] = rules.get(v.rule, 0) + 1
    with_violation = len({v.file for v in violations})
    return {
        "files": checked,
        "files_ok": max(0, checked - with_violation),
        "violations": len(violations),
        "errors": sum(1 for v in violations if v.severity == "error"),
        "preexisting": preexisting,
        "rules": rules,
    }


def to_sarif(violations: list[Violation], rules: list[Rule]) -> dict[str, Any]:
    known = {r.id: r for r in rules}
    return {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "nexo-standards",
                        "rules": [
                            {"id": r.id, "shortDescription": {"text": r.title}, "helpUri": f"skill:{r.skill}"}
                            for r in rules
                        ],
                    }
                },
                "results": [
                    {
                        "ruleId": v.rule,
                        "level": "error" if v.severity == "error" else "warning",
                        "message": {
                            "text": v.message + (f" (skill {known[v.rule].skill})" if known[v.rule].skill else "")
                        },
                        "properties": {"security-severity": "7.0" if v.severity == "error" else "4.0"},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": v.file},
                                    "region": {"startLine": max(1, v.line)},
                                }
                            }
                        ],
                    }
                    for v in violations
                ],
            }
        ],
    }
