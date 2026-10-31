"""Leitura determinística do repositório para o mapa de código: arquivos, símbolos, imports, complexidade,
histórico do git, donos, dependências, pontos de entrada, frameworks de teste, ferramentas e comandos do pipeline."""

from __future__ import annotations

import ast
import json
import re
import subprocess
import tomllib
from collections import Counter, defaultdict
from fnmatch import fnmatch
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from nexo.kernel.brownfield import FileInfo

LANGS = {
    ".py": "python",
    ".cs": "csharp",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".java": "java",
    ".kt": "kotlin",
    ".dart": "dart",
}
SKIP_DIRS = {
    ".git",
    ".nexo",
    "node_modules",
    "bin",
    "obj",
    ".venv",
    "venv",
    "dist",
    "build",
    "__pycache__",
    ".mypy_cache",
    "target",  # Maven
    ".gradle",
    ".dart_tool",
    ".angular",
}
BRANCH = re.compile(r"\b(if|elif|for|foreach|while|case|catch|except)\b|&&|\|\||\?\s")
ENTRY_NAMES = {
    "__main__.py",
    "main.py",
    "app.py",
    "manage.py",
    "wsgi.py",
    "Program.cs",
    "Startup.cs",
    "index.ts",
    "index.js",
    "main.ts",
    "main.js",
    "server.ts",
    "server.js",
    "Application.java",
    "main.dart",
}
TEST_PATTERNS = (
    "tests/*",
    "test/*",
    "*/tests/*",
    "*/test/*",
    "*test_*.py",
    "*_test.py",
    "*.test.*",
    "*.spec.*",
    "*Tests.cs",
    "*Tests/*",
    "*Test.java",
    "*Tests.java",
    "*IT.java",
    "*Test.kt",
    "*_test.dart",
    "integration_test/*",
)


def is_test(path: str) -> bool:
    return any(fnmatch(path, p) for p in TEST_PATTERNS)


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )
    return proc.stdout if proc.returncode == 0 else ""


def list_files(root: Path) -> list[str]:
    tracked = [ln for ln in _git(root, "ls-files").splitlines() if ln]
    if tracked:
        return [p for p in tracked if not set(Path(p).parts) & SKIP_DIRS]
    return sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and not set(p.relative_to(root).parts) & SKIP_DIRS
    )


def working_files(root: Path) -> list[str]:
    """Arquivos versionados e novos ainda não commitados (sem os ignorados pelo git)."""
    listed = _git(root, "ls-files", "--cached", "--others", "--exclude-standard").splitlines()
    found = sorted({ln for ln in listed if ln and not set(Path(ln).parts) & SKIP_DIRS})
    return found or list_files(root)


def parse(lang: str, text: str) -> tuple[list[str], list[str]]:
    """Símbolos e imports de um arquivo de código."""
    return _python(text) if lang == "python" else _regex(lang, text)


def _python(text: str) -> tuple[list[str], list[str]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [], []
    symbols, imports = [], []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(f"def {node.name}({', '.join(a.arg for a in node.args.args)})")
        elif isinstance(node, ast.ClassDef):
            symbols.append(f"class {node.name}")
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and not item.name.startswith("__"):
                    symbols.append(
                        f"def {node.name}.{item.name}({', '.join(a.arg for a in item.args.args if a.arg != 'self')})"
                    )
    for sub in ast.walk(tree):
        if isinstance(sub, ast.Import):
            imports += [a.name for a in sub.names]
        elif isinstance(sub, ast.ImportFrom) and sub.module:
            imports.append(sub.module)
    return symbols, imports


def _regex(lang: str, text: str) -> tuple[list[str], list[str]]:
    if lang == "csharp":
        types = [f"{m.group(1)} {m.group(2)}" for m in re.finditer(r"\b(class|interface|record|enum)\s+(\w+)", text)]
        methods = [
            f"{m.group(1)}()"
            for m in re.finditer(
                r"\b(?:public|internal|protected)\s+(?:static\s+|async\s+|virtual\s+|override\s+)*[\w<>\[\],?]+\s+(\w+)\s*\(",
                text,
            )
        ]
        imports = re.findall(r"^\s*using\s+([\w.]+)\s*;", text, flags=re.M)
        return types + methods, imports
    if lang in ("java", "kotlin"):
        types = [
            f"{m.group(1)} {m.group(2)}" for m in re.finditer(r"\b(class|interface|record|enum|object)\s+(\w+)", text)
        ]
        methods = [
            f"{m.group(1)}()"
            for m in re.finditer(
                r"\b(?:public|protected|fun)\s+(?:static\s+|final\s+|abstract\s+|synchronized\s+|suspend\s+)*"
                r"(?:[\w<>\[\],?.]+\s+)?(\w+)\s*\(",
                text,
            )
        ]
        imports = re.findall(r"^\s*import\s+(?:static\s+)?([\w.]+)", text, flags=re.M)
        return types + methods, imports
    if lang == "dart":
        types = [f"{m.group(1)} {m.group(2)}" for m in re.finditer(r"\b(class|mixin|enum|extension)\s+(\w+)", text)]
        functions = [
            f"function {m.group(1)}"
            for m in re.finditer(r"^(?:[\w<>?,\s]+\s)?(\w+)\s*\([^)]*\)\s*(?:async\s*)?\{", text, flags=re.M)
            if m.group(1) not in ("if", "for", "while", "switch", "catch")
        ]
        imports = re.findall(r"^\s*import\s+['\"]([^'\"]+)['\"]", text, flags=re.M)
        return types + functions, imports
    symbols = [
        f"{m.group(1)} {m.group(2)}"
        for m in re.finditer(r"export\s+(?:default\s+)?(function|class|const)\s+(\w+)", text)
    ]
    symbols += [f"function {m.group(1)}" for m in re.finditer(r"^\s*function\s+(\w+)\s*\(", text, flags=re.M)]
    imports = re.findall(r"from\s+['\"]([^'\"]+)['\"]", text) + re.findall(r"require\(['\"]([^'\"]+)['\"]\)", text)
    return symbols, imports


def _owners(root: Path) -> list[tuple[str, str]]:
    for rel in ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"):
        path = root / rel
        if path.is_file():
            rules = []
            for line in path.read_text(encoding="utf-8").splitlines():
                parts = line.split("#")[0].split()
                if len(parts) >= 2:
                    rules.append((parts[0].lstrip("/"), parts[1]))
            return rules
    return []


def _owner_for(path: str, rules: list[tuple[str, str]], authors: Counter[str] | None) -> str:
    match = ""
    for pattern, owner in rules:
        pat = pattern.rstrip("/")
        if pattern in ("*", "/*") or fnmatch(path, pat) or path.startswith(pat + "/") or fnmatch(path, f"*/{pat}"):
            match = owner  # a última regra que casa vence (semântica do CODEOWNERS)
    if match:
        return match
    return authors.most_common(1)[0][0] if authors else ""


def history(root: Path, months: int = 12) -> tuple[Counter[str], dict[str, Counter[str]]]:
    churn: Counter[str] = Counter()
    authors: dict[str, Counter[str]] = defaultdict(Counter)
    author = ""
    for line in _git(
        root, "log", f"--since={months}.months", "--no-merges", "--format=@@%an", "--name-only"
    ).splitlines():
        if line.startswith("@@"):
            author = line[2:]
        elif line.strip():
            churn[line.strip()] += 1
            authors[line.strip()][author] += 1
    return churn, authors


def scan(root: Path) -> list[FileInfo]:
    churn, authors = history(root)
    rules = _owners(root)
    out = []
    for rel in list_files(root):
        lang = LANGS.get(Path(rel).suffix)
        if lang is None:
            continue
        text = (root / rel).read_text(encoding="utf-8", errors="replace")
        symbols, imports = _python(text) if lang == "python" else _regex(lang, text)
        out.append(
            FileInfo(
                path=rel,
                lang=lang,
                loc=sum(1 for ln in text.splitlines() if ln.strip()),
                symbols=symbols,
                imports=imports,
                complexity=len(BRANCH.findall(text)),
                churn=churn.get(rel, 0),
                owner=_owner_for(rel, rules, authors.get(rel)),
                is_test=is_test(rel),
            )
        )
    return out


def dependencies(root: Path) -> dict[str, list[str]]:
    deps: dict[str, list[str]] = {}
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        deps["python"] = [re.split(r"[<>=!~\[; ]", d)[0] for d in (data.get("project") or {}).get("dependencies") or []]
    req = root / "requirements.txt"
    if req.is_file() and "python" not in deps:
        deps["python"] = [
            re.split(r"[<>=!~\[; ]", ln)[0]
            for ln in req.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")
        ]
    pkg = root / "package.json"
    if pkg.is_file():
        data = json.loads(pkg.read_text(encoding="utf-8"))
        deps["javascript"] = sorted({**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})})
    refs = [
        m
        for p in root.rglob("*.csproj")
        for m in re.findall(r'PackageReference\s+Include="([^"]+)"', p.read_text(encoding="utf-8", errors="replace"))
    ]
    if refs:
        deps["dotnet"] = sorted(set(refs))
    return deps


def entrypoints(files: list[FileInfo]) -> list[str]:
    return [f.path for f in files if Path(f.path).name in ENTRY_NAMES and not f.is_test]


def test_frameworks(root: Path, files: list[FileInfo], deps: dict[str, list[str]]) -> list[str]:
    found = []
    text_py = (root / "pyproject.toml").read_text(encoding="utf-8") if (root / "pyproject.toml").is_file() else ""
    if "pytest" in text_py or (root / "pytest.ini").is_file() or any(f.path.endswith("conftest.py") for f in files):
        found.append("pytest")
    if any("unittest" in f.imports for f in files if f.is_test):
        found.append("unittest")
    for name in ("xunit", "nunit", "MSTest.TestFramework"):
        if any(name.lower() in d.lower() for d in deps.get("dotnet", [])):
            found.append(name.split(".")[0].lower())
    for name in ("jest", "vitest", "mocha"):
        if name in deps.get("javascript", []):
            found.append(name)
    return found


def tool_configs(root: Path) -> dict[str, bool]:
    py = (root / "pyproject.toml").read_text(encoding="utf-8") if (root / "pyproject.toml").is_file() else ""
    return {
        "ruff": "[tool.ruff" in py or (root / "ruff.toml").is_file(),
        "black": "[tool.black" in py,
        "flake8": (root / ".flake8").is_file()
        or ((root / "setup.cfg").is_file() and "[flake8]" in (root / "setup.cfg").read_text(encoding="utf-8")),
        "mypy": "[tool.mypy" in py or (root / "mypy.ini").is_file(),
        "eslint": any(
            (root / n).is_file() for n in (".eslintrc", ".eslintrc.json", ".eslintrc.js", "eslint.config.js")
        ),
        "prettier": any((root / n).is_file() for n in (".prettierrc", ".prettierrc.json", "prettier.config.js")),
        "editorconfig": (root / ".editorconfig").is_file(),
    }


PIPELINE_TOOLS = {
    "lint": re.compile(
        r"\b(ruff|flake8|pylint|eslint|dotnet\s+format|black\s+--check|ng\s+lint|flutter\s+analyze|dart\s+analyze"
        r"|dart\s+format|spotless(Check|:check)|checkstyle)\b"
    ),
    "unit": re.compile(
        r"\b(pytest|dotnet\s+test|npm\s+(run\s+)?test|vitest|jest|mvnw?\s+(-\S+\s+)*(test|verify)|gradlew?\s+test"
        r"|ng\s+test|flutter\s+test)\b"
    ),
    "build": re.compile(
        r"\b(dotnet\s+build|npm\s+run\s+build|tsc\b|mvnw?\s+(-\S+\s+)*package|gradlew?\s+(build|assemble)"
        r"|ng\s+build|flutter\s+build)"
    ),
}


def pipeline_commands(root: Path) -> dict[str, list[dict[str, str]]]:
    """Comandos reais que o projeto já usa (CI, Makefile, scripts), para preferir aos da receita padrão."""
    sources: list[tuple[str, str]] = []
    for rel in ("Makefile", "tox.ini", "noxfile.py"):
        if (root / rel).is_file():
            sources += [
                (rel, ln.strip()) for ln in (root / rel).read_text(encoding="utf-8", errors="replace").splitlines()
            ]
    for wf in sorted((root / ".github" / "workflows").glob("*.y*ml")):
        sources += [(wf.relative_to(root).as_posix(), ln.strip()) for ln in wf.read_text(encoding="utf-8").splitlines()]
    pkg = root / "package.json"
    if pkg.is_file():
        scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts") or {}
        sources += [("package.json", f"npm run {k}  # {v}") for k, v in scripts.items()]
    found: dict[str, list[dict[str, str]]] = {}
    for origin, line in sources:
        cmd = re.sub(r"^(-\s*)?(run|script)\s*:\s*[|>]?\s*", "", line).strip().strip("\"'")
        if not cmd or cmd.startswith("#"):
            continue
        for stage, pattern in PIPELINE_TOOLS.items():
            if pattern.search(cmd):
                entry = {"command": cmd.split("  #")[0].strip(), "source": origin}
                if entry not in found.setdefault(stage, []):
                    found[stage].append(entry)
    return found


def coverage_lines(xml_text: str) -> dict[str, dict[int, int]]:
    """Cobertura linha a linha de um relatório Cobertura: arquivo → {linha: execuções}."""
    root = ET.fromstring(xml_text)  # noqa: S314
    sources = [s.text.rstrip("/") + "/" for s in root.iter("source") if s.text]
    out: dict[str, dict[int, int]] = {}
    for cls in root.iter("class"):
        name = (cls.get("filename") or "").replace("\\", "/")
        lines = out.setdefault(name, {})
        for line in cls.iter("line"):
            lines[int(line.get("number", "0"))] = int(line.get("hits", "0"))
        for src in sources:  # caminhos absolutos da raiz de código viram relativos ao repositório
            if src and name.startswith(src):
                out[name[len(src) :]] = lines
    return out


def changed_lines(root: Path, base: str) -> dict[str, set[int]]:
    """Linhas adicionadas/alteradas em relação à base (git diff -U0)."""
    out: dict[str, set[int]] = {}
    current = ""
    for line in _git(root, "diff", "-U0", base, "--").splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else ""
        elif line.startswith("@@") and current:
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if m:
                start, count = int(m.group(1)), int(m.group(2) or 1)
                out.setdefault(current, set()).update(range(start, start + count))
    return out


def summary_config(files: list[FileInfo]) -> dict[str, Any]:
    langs = Counter(f.lang for f in files if not f.is_test)
    return {"languages": dict(langs.most_common()), "files": len(files), "tests": sum(f.is_test for f in files)}
