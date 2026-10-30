"""Leitura de dependências vulneráveis e desatualizadas pelas ferramentas de cada stack (F25), em JSON.

- pip (F51, no lugar do uv): `pip-audit -f json` sobre o `pip freeze` do ambiente do projeto (vulnerabilidades) e
  `pip list --outdated --format json` (versões); sem ambiente ainda, o pip-audit resolve o requirements.txt;
- npm: `npm audit --json` e `npm outdated --json`;
- dotnet: `dotnet list package --vulnerable|--outdated --format json`;
- pub (Flutter/Dart): `osv-scanner` no `pubspec.lock` e `dart pub outdated --json` (F49);
- maven: `osv-scanner` no `pom.xml` (F49). Gradle fica na etapa `sca-gradle` da qualidade: sem comando padrão que
  troque a versão no build.gradle, a atualização segue pela história normal.
Os parsers são puros (texto → achados) para testar com saídas reais gravadas.
"""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nexo.modules.deps.domain.deps import DepFinding, Vuln, newest

Runner = Callable[[list[str], Path], str]


def _run(cmd: list[str], cwd: Path) -> str:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=False, timeout=600)
    return proc.stdout  # as ferramentas saem com código ≠ 0 quando acham problema; o JSON vale mesmo assim


def detect(root: Path) -> list[str]:
    managers = []
    if (root / "pyproject.toml").is_file() or list(root.glob("requirements*.txt")):
        managers.append("pip")
    if (root / "package.json").is_file():
        managers.append("npm")
    if list(root.glob("*.csproj")) or list(root.glob("*.sln")):
        managers.append("dotnet")
    if (root / "pubspec.lock").is_file():
        managers.append("pub")
    if (root / "pom.xml").is_file():
        managers.append("maven")
    return managers


# ------------------------------------------------------------------ parsers


def parse_pip_audit(text: str, direct: set[str]) -> list[DepFinding]:
    data = json.loads(text or "{}")
    out = []
    for dep in data.get("dependencies") or []:
        vulns = dep.get("vulns") or []
        if not vulns:
            continue
        fixes = [v for x in vulns for v in x.get("fix_versions") or []]
        ghsa = [
            Vuln(
                next((a for a in x.get("aliases") or [] if a.startswith("CVE-")), str(x.get("id"))),
                "high",  # pip-audit não informa severidade: tratado como alto até a triagem
                "",
            )
            for x in vulns
        ]
        name = str(dep["name"]).lower()
        out.append(DepFinding("pip", name, str(dep["version"]), newest(fixes), "security", name in direct, ghsa))
    return out


def parse_pip_outdated(text: str, direct: set[str]) -> list[DepFinding]:
    """`pip list --outdated --format json` (o mesmo formato que o `uv pip list` produzia)."""
    out = []
    for row in json.loads(text or "[]"):
        name = str(row["name"]).lower()
        out.append(DepFinding("pip", name, str(row["version"]), str(row["latest_version"]), "outdated", name in direct))
    return out


def parse_npm_audit(text: str) -> list[DepFinding]:
    data = json.loads(text or "{}")
    out = []
    for name, v in (data.get("vulnerabilities") or {}).items():
        fix = v.get("fixAvailable")
        target = str(fix.get("version")) if isinstance(fix, dict) and fix.get("name") == name else ""
        vulns = [
            Vuln(
                str(x.get("url", "")).rsplit("/", 1)[-1] or str(x.get("source")),
                str(x.get("severity", "medium")),
                str(x.get("url", "")),
            )
            for x in v.get("via") or []
            if isinstance(x, dict)
        ]
        current = ""
        out.append(DepFinding("npm", name, current, target, "security", bool(v.get("isDirect")), vulns))
    return out


def parse_npm_outdated(text: str) -> list[DepFinding]:
    out = []
    for name, v in (json.loads(text or "{}") or {}).items():
        current = str(v.get("current") or v.get("wanted") or "")
        out.append(DepFinding("npm", name, current, str(v.get("latest", "")), "outdated", True))
    return out


def _dotnet_packages(text: str) -> list[dict[str, Any]]:
    data = json.loads(text or "{}")
    return [
        pkg
        for project in data.get("projects") or []
        for fw in project.get("frameworks") or []
        for pkg in fw.get("topLevelPackages") or []
    ]


def parse_dotnet(vulnerable: str, outdated: str) -> list[DepFinding]:
    latest = {str(p["id"]): str(p.get("latestVersion", "")) for p in _dotnet_packages(outdated)}
    out = []
    for p in _dotnet_packages(vulnerable):
        vulns = [
            Vuln(
                str(v.get("advisoryurl", "")).rsplit("/", 1)[-1],
                str(v.get("severity", "Moderate")).lower(),
                str(v.get("advisoryurl", "")),
            )
            for v in p.get("vulnerabilities") or []
        ]
        pid = str(p["id"])
        out.append(
            DepFinding("dotnet", pid, str(p.get("resolvedVersion", "")), latest.get(pid, ""), "security", True, vulns)
        )
    for p in _dotnet_packages(outdated):
        out.append(
            DepFinding(
                "dotnet",
                str(p["id"]),
                str(p.get("resolvedVersion", "")),
                str(p.get("latestVersion", "")),
                "outdated",
                True,
            )
        )
    return out


def parse_osv_scanner(text: str, manager: str, direct: set[str] | None = None) -> list[DepFinding]:
    """`osv-scanner scan source --format json`: um achado por pacote, versão de correção pelo evento `fixed`."""
    data = json.loads(text or "{}")
    out = []
    for result in data.get("results") or []:
        for pkg in result.get("packages") or []:
            info = pkg.get("package") or {}
            name = str(info.get("name", ""))
            vulns, fixes = [], []
            for v in pkg.get("vulnerabilities") or []:
                cve = next((a for a in v.get("aliases") or [] if str(a).startswith("CVE-")), str(v.get("id")))
                severity = str((v.get("database_specific") or {}).get("severity") or "high").lower()
                vulns.append(
                    Vuln(cve, "medium" if severity == "moderate" else severity, f"https://osv.dev/{v.get('id')}")
                )
                fixes += [
                    str(e["fixed"])
                    for a in v.get("affected") or []
                    if (a.get("package") or {}).get("name") == name
                    for r in a.get("ranges") or []
                    for e in r.get("events") or []
                    if "fixed" in e
                ]
            if vulns:
                is_direct = name in direct if direct is not None else True
                out.append(
                    DepFinding(manager, name, str(info.get("version", "")), newest(fixes), "security", is_direct, vulns)
                )
    return out


def parse_pub_outdated(text: str) -> list[DepFinding]:
    out = []
    for p in (json.loads(text or "{}") or {}).get("packages") or []:
        current = str((p.get("current") or {}).get("version") or "")
        latest = str((p.get("latest") or {}).get("version") or "")
        if current and latest and current != latest:
            out.append(DepFinding("pub", str(p["package"]), current, latest, "outdated", p.get("kind") == "direct"))
    return out


def _pub_direct(root: Path) -> set[str]:
    lock = root / "pubspec.lock"
    text = lock.read_text(encoding="utf-8") if lock.is_file() else ""
    return set(re.findall(r'^  ([a-z0-9_]+):\n    dependency: "direct', text, re.M))


def _pip_direct(root: Path) -> set[str]:
    """Dependências declaradas pelo projeto (pyproject e requirements*.in/.txt de primeiro nível)."""
    specs: list[str] = []
    if (root / "pyproject.toml").is_file():
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        specs = list((data.get("project") or {}).get("dependencies") or [])
        for group in (data.get("dependency-groups") or {}).values():
            specs += [s for s in group if isinstance(s, str)]
        for group in ((data.get("project") or {}).get("optional-dependencies") or {}).values():
            specs += list(group)
    sources = sorted(root.glob("requirements*.in")) or sorted(root.glob("requirements*.txt"))
    for path in sources:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line and not line.startswith(("-", "--")):
                specs.append(line)
    return {re.split(r"[\s<>=!~\[;]", s, maxsplit=1)[0].lower().replace("_", "-") for s in specs if s}


def _pip_env(root: Path) -> tuple[str, str]:
    """(Python do ambiente do projeto, pip-audit que vem com o Nexo)."""
    from nexo.kernel import services

    python_env = services.get("quality.runner", "python_env")
    from nexo.kernel.runtime import toolenv

    env = python_env(root)
    found, _problem = toolenv.ensure("pip-audit")
    return env["venv_python"], found or "pip-audit"


PIN = re.compile(r"(?im)^(?P<name>[A-Za-z0-9_.-]+)(?P<extra>\[[^\]]*\])?\s*(==|>=|~=|<=|<|>)\s*[^\s;#\\]+")


def pin_version(root: Path, package: str, version: str) -> tuple[list[str], bool]:
    """Troca a versão de `package` nos requirements*.txt (==), requirements*.in e pyproject (>=).
    Devolve (arquivos alterados, há requirements com hashes)."""
    norm = package.lower().replace("_", "-")
    changed: list[str] = []
    hashed = False
    for path in [
        *sorted(root.glob("requirements*.in")),
        *sorted(root.glob("requirements*.txt")),
        root / "pyproject.toml",
    ]:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        hashed = hashed or (path.suffix == ".txt" and "--hash=" in text)

        def repl(m: re.Match[str], path: Path = path) -> str:
            if m.group("name").lower().replace("_", "-") != norm:
                return m.group(0)
            op = "==" if path.suffix == ".txt" and not path.name.endswith(".in") else ">="
            return f"{m.group('name')}{m.group('extra') or ''}{op}{version}"

        new = (
            PIN.sub(repl, text)
            if path.suffix != ".toml"
            else re.sub(
                rf'(?i)"{re.escape(package)}(\[[^\]]*\])?\s*(==|>=|~=)\s*[^";]+',
                lambda m: f'"{package}{m.group(1) or ""}>={version}',
                text,
            )
        )
        if new != text:
            path.write_text(new, encoding="utf-8")
            changed.append(path.name)
    return changed, hashed


def pip_bump(root: Path, package: str, version: str) -> tuple[bool, str]:
    """Atualização pelo Nexo (F51): troca a versão de `package` nos requirements*.txt/.in e no pyproject e instala.
    Arquivo com hashes (pip-compile) é regerado pelo pip-tools do ambiente, se houver; senão a tarefa avisa."""
    import subprocess

    changed, hashed = pin_version(root, package, version)
    venv_python, _ = _pip_env(root)
    notes = []
    if hashed and (root / "requirements.in").is_file():
        proc = subprocess.run(
            [venv_python, "-m", "piptools", "compile", "--generate-hashes", "--quiet", "requirements.in"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=900,
        )
        notes.append(f"pip-compile → saída {proc.returncode}")
    elif hashed:
        notes.append("requirements com hashes sem requirements.in: regere o arquivo travado do projeto")
    proc = subprocess.run(
        [venv_python, "-m", "pip", "install", "--quiet", f"{package}=={version}"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=900,
    )
    ok = bool(changed) and proc.returncode == 0
    detail = f"{package} → {version} em {', '.join(changed) or 'nenhum arquivo'}; pip install → saída {proc.returncode}"
    return ok, "; ".join([detail, *notes, (proc.stderr or "")[-400:]]).strip("; ")


def _npm_current(root: Path, name: str) -> str:
    lock = root / "package-lock.json"
    if not lock.is_file():
        return ""
    data = json.loads(lock.read_text(encoding="utf-8"))
    return str(((data.get("packages") or {}).get(f"node_modules/{name}") or {}).get("version", ""))


# ------------------------------------------------------------------ varredura


def scan(root: Path, run: Runner | None = None) -> list[DepFinding]:
    run = run or _run
    findings: list[DepFinding] = []
    for manager in detect(root):
        if manager == "pip":
            direct = _pip_direct(root)
            venv_python, pip_audit = _pip_env(root)
            if Path(venv_python).is_file():  # ambiente do projeto já existe (o pipeline cria): audita o instalado
                frozen = run([venv_python, "-m", "pip", "freeze", "--exclude-editable"], root)
                req_file = root / ".nexo" / "deps" / "requirements-freeze.txt"
                req_file.parent.mkdir(parents=True, exist_ok=True)
                req_file.write_text(frozen, encoding="utf-8")
                audit_cmd = [pip_audit, "-f", "json", "--progress-spinner", "off", "--no-deps", "--disable-pip"]
                findings += parse_pip_audit(run([*audit_cmd, "-r", str(req_file)], root), direct)
                findings += parse_pip_outdated(
                    run([venv_python, "-m", "pip", "list", "--outdated", "--format", "json"], root), direct
                )
            else:  # sem ambiente: o pip-audit resolve os requirements declarados
                for req in sorted(root.glob("requirements*.txt"))[:1]:
                    findings += parse_pip_audit(
                        run([pip_audit, "-f", "json", "--progress-spinner", "off", "-r", req.name], root), direct
                    )
        elif manager == "npm":
            audit = parse_npm_audit(run(["npm", "audit", "--json"], root))
            for f in audit:
                f.current = _npm_current(root, f.package)
            findings += audit
            findings += parse_npm_outdated(run(["npm", "outdated", "--json"], root))
        elif manager == "dotnet":
            findings += parse_dotnet(
                run(["dotnet", "list", "package", "--vulnerable", "--format", "json"], root),
                run(["dotnet", "list", "package", "--outdated", "--format", "json"], root),
            )
        elif manager == "pub":
            osv = ["osv-scanner", "scan", "source", "--format", "json", "--lockfile=pubspec.lock"]
            findings += parse_osv_scanner(run(osv, root), "pub", _pub_direct(root))
            findings += parse_pub_outdated(run(["dart", "pub", "outdated", "--json"], root))
        elif manager == "maven":
            osv = ["osv-scanner", "scan", "source", "--format", "json", "--lockfile=pom.xml"]
            findings += parse_osv_scanner(run(osv, root), "maven")
    return findings
