"""Executor de receitas de qualidade."""

from __future__ import annotations

import glob as globlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Any

from nexo.kernel.quality.model import ERROR, FAILED, PASSED, SKIPPED, Finding, QualityReport, StageResult
from nexo.kernel.runtime.resources import load_yaml
from nexo.kernel.runtime.system import windows_command
from nexo.modules.quality.adapters import parsers

DEFAULT_TIMEOUT = 900


def load_recipe(stack: str, brain_dir: Path | None = None) -> dict[str, Any]:
    recipe = load_yaml(f"recipes/{stack}.yaml", brain_dir)
    common = load_yaml("recipes/common.yaml", brain_dir)
    recipe["stages"] = [*recipe.get("stages", []), *common.get("stages", [])]
    return recipe


def _fmt(value: str, subs: dict[str, str]) -> str:
    for k, v in subs.items():
        value = value.replace("{" + k + "}", v)
    return value


def applies(stage: dict[str, Any], workdir: Path) -> str | None:
    """Motivo para pular a etapa, ou None se ela se aplica."""
    params = stage.get("_params") or {}
    if "when_exists" in stage and not (workdir / _fmt(str(stage["when_exists"]), params)).exists():
        return f"{stage['when_exists']} ausente"
    if "unless_exists" in stage and (workdir / _fmt(str(stage["unless_exists"]), params)).exists():
        return f"{_fmt(str(stage['unless_exists']), params)} já existe"
    if "when_glob" in stage and not list(workdir.glob(stage["when_glob"])):
        return f"nenhum arquivo {stage['when_glob']}"
    if "when_param" in stage and not str(stage.get("_params", {}).get(stage["when_param"], "")):
        return f"parâmetro {stage['when_param']} não configurado"
    if "when_contains" in stage:
        cond = stage["when_contains"]
        if not _contains(workdir, str(cond["file"]), str(cond["text"])):
            return f"{cond['file']} não contém {cond['text']}"
    return None


def _contains(workdir: Path, pattern: str, text: str) -> bool:
    """`pattern` é um arquivo ou um curinga (`build.gradle*`): basta um deles conter o texto."""
    paths = list(workdir.glob(pattern)) if any(c in pattern for c in "*?[") else [workdir / pattern]
    return any(p.is_file() and text in p.read_text(encoding="utf-8", errors="replace") for p in paths)


def _command(stage: dict[str, Any], workdir: Path, subs: dict[str, str]) -> list[str]:
    """Comando da etapa com os argumentos opcionais que se aplicam. Argumento vazio depois da substituição sai: alvo vazio = padrão da ferramenta."""
    args = [str(a) for a in stage["run"]]
    for extra in stage.get("optional_args") or []:
        if applies({**extra, "_params": subs}, workdir) is None:
            args += [str(a) for a in extra["args"]]
    return expand(args, subs)


PYTHON_LOCAL = [".venv/", "venv/", "*.egg-info/", ".pytest_cache/", "__pycache__/", ".ruff_cache/", ".coverage"]
ARGS_SEP = "\x1f"  # valor de substituição com vários argumentos (ex.: `-r requirements-dev.txt`)


def expand(args: list[Any], subs: dict[str, str]) -> list[str]:
    """Substitui os campos; valor com vários argumentos vira vários; argumento vazio sai (padrão da ferramenta)."""
    out: list[str] = []
    for a in args:
        out += [c for c in _fmt(str(a), subs).split(ARGS_SEP) if c != ""]
    return out


def python_env(workdir: Path, base_python: str = "", *, ci: bool = False) -> dict[str, str]:
    """F51 (pip no lugar do uv): ambiente virtual do projeto e o que instalar nele.

    - `.venv` do projeto (ou `venv`/`env` se já existir); criado com o Python do Nexo (ou `quality.python`).
    - Instala pelo que o projeto tiver: requirements-dev.txt › requirements.txt › pyproject (extra `dev` ou
      grupo `dev`); com requirements e pyproject, o projeto entra depois, editável e sem dependências (hashes).
    """
    import sys
    import tomllib

    windows = sys.platform == "win32" and not ci
    name = next((n for n in ("venv", "env") if (workdir / n / "pyvenv.cfg").is_file()), ".venv")
    if (workdir / ".venv").exists():
        name = ".venv"
    local_scripts = workdir / name / ("Scripts" if windows else "bin")
    scripts = str(local_scripts)
    venv_python = str(local_scripts / ("python.exe" if windows else "python"))
    if ci:  # o workflow gerado roda no Ubuntu: caminho com barras, em qualquer sistema que gere
        scripts, venv_python = str(PurePosixPath(name, "bin")), str(PurePosixPath(name, "bin", "python"))
    base = base_python or ("python" if ci else _base_python())
    reqs = next(
        (f for f in ("requirements-dev.txt", "requirements/dev.txt", "requirements.txt") if (workdir / f).is_file()), ""
    )
    project: dict[str, Any] = {}
    if (workdir / "pyproject.toml").is_file():
        try:
            project = dict(tomllib.loads((workdir / "pyproject.toml").read_text(encoding="utf-8")))
        except ValueError:
            project = {}
    has_project = "project" in project or (workdir / "setup.py").is_file()
    install: list[str] = []
    editable: list[str] = []
    if reqs:
        install = ["-r", reqs]
        editable = ["-e", "."] if has_project else []
    elif has_project:
        extras = (project.get("project") or {}).get("optional-dependencies") or {}
        groups = project.get("dependency-groups") or {}
        install = ["-e", ".[dev]" if "dev" in extras else "."] + (["--group", "dev"] if "dev" in groups else [])
    return {
        "python": base,
        "venv": name,
        "venv_python": venv_python,
        "venv_scripts": scripts,
        "pip_install": ARGS_SEP.join(install),
        "pip_project": ARGS_SEP.join(editable),
    }


def companion_tool(name: str) -> str:
    """Caminho da ferramenta em ambiente próprio (semgrep, pip-audit), sem instalar: a etapa com `provision`
    instala na primeira vez."""
    from nexo.kernel.runtime import toolenv

    target = toolenv.path_for(name)
    if target.is_file():
        return str(target)
    beside = Path(sys.executable).parent / (f"{name}.exe" if sys.platform == "win32" else name)
    if beside.is_file():
        return str(beside)
    return shutil.which(name) or str(target)


def _base_python() -> str:
    """Python que cria o ambiente do projeto: o do próprio Nexo; no binário empacotado, o do sistema."""
    import shutil
    import sys

    if not getattr(sys, "frozen", False):
        return sys.executable
    return shutil.which("python3") or shutil.which("python") or shutil.which("py") or "python"


def _tool_missing(tool: str, workdir: Path) -> bool:
    """Wrapper do projeto (caminho absoluto, ex. mvnw) conta se existir; o resto precisa estar no PATH."""
    return not Path(tool).is_file() if Path(tool).is_absolute() else shutil.which(tool) is None


def run_stage(stage: dict[str, Any], workdir: Path, out: Path, subs: dict[str, str]) -> StageResult:
    result = StageResult(
        str(stage["id"]), str(stage.get("kind", stage["id"])), SKIPPED, bool(stage.get("required", True))
    )
    skip = applies({**stage, "_params": subs}, workdir)
    if skip:
        result.summary = f"não se aplica: {skip}"
        return result
    provision = str(stage.get("provision") or "")
    if provision:  # ferramenta em ambiente próprio, instalada com pip na primeira vez (como o uvx fazia)
        from nexo.kernel.runtime import toolenv

        found, problem = toolenv.ensure(provision)
        if problem:
            result.status = ERROR if result.required else SKIPPED
            result.summary = problem
            return result
        subs = {**subs, provision.replace("-", "_"): found}
    tool = _fmt(str(stage.get("tool") or ""), subs)
    if tool and _tool_missing(tool, workdir):
        result.status = ERROR if result.required else SKIPPED
        result.summary = f"ferramenta ausente: {tool}"
        return result
    env_extra = {k: _fmt(str(v), subs) for k, v in (stage.get("env") or {}).items()}
    stale = coverage_file({"coverage": stage["coverage"]}, subs, workdir) if stage.get("coverage") else None
    if stale is not None:
        stale.unlink()  # relatório de uma execução anterior não vale como cobertura desta (ex. Maven com falha)
    start = time.perf_counter()
    log_lines: list[str] = []
    try:
        for pre in stage.get("pre") or []:
            # `{run: [...], stdout: arquivo}` grava a saída (ex.: `pip freeze` para o pip-audit)
            pre_cmd = pre["run"] if isinstance(pre, dict) else pre
            proc = _exec(expand(pre_cmd, subs), workdir, env_extra, int(stage.get("timeout", DEFAULT_TIMEOUT)))
            if isinstance(pre, dict) and pre.get("stdout") and proc.returncode == 0:
                target = Path(_fmt(str(pre["stdout"]), subs))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(proc.stdout, encoding="utf-8")
            log_lines.append(proc.stdout + proc.stderr)
            if proc.returncode != 0:
                result.status, result.summary = ERROR, f"preparação falhou ({proc.returncode})"
                result.log = "".join(log_lines)[-4000:]
                return result
        cmd = _command(stage, workdir, subs)
        proc = _exec(cmd, workdir, env_extra, int(stage.get("timeout", DEFAULT_TIMEOUT)))
        log_lines.append(proc.stdout + proc.stderr)
        _interpret(stage, result, proc, subs, workdir)
    except subprocess.TimeoutExpired:
        result.status, result.summary = ERROR, "tempo limite excedido"
    except (OSError, ValueError) as exc:
        result.status, result.summary = ERROR, f"{type(exc).__name__}: {exc}"
    result.seconds = round(time.perf_counter() - start, 2)
    result.log = "".join(log_lines)[-4000:]
    return result


def _exec(cmd: list[str], cwd: Path, env_extra: dict[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
    import os

    env = {**os.environ, **env_extra, "NO_COLOR": "1", "CI": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(
        windows_command(cmd),
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
        check=False,
    )


def _interpret(
    stage: dict[str, Any],
    result: StageResult,
    proc: subprocess.CompletedProcess[str],
    subs: dict[str, str],
    workdir: Path,
) -> None:
    parser = stage.get("parser", "exitcode")
    report = Path(_fmt(str(stage["report"]), subs)) if stage.get("report") else None
    if report is not None and not report.is_absolute():
        report = workdir / report  # relatório gravado pela ferramenta dentro do projeto (ex. surefire)
    if parser == "flutter_machine":  # eventos JSON na saída do `flutter test --machine`
        result.tests = parsers.flutter_machine(proc.stdout)
        failed = len(result.failed_tests)
        if not result.tests and proc.returncode != 0:
            result.status, result.summary = ERROR, f"testes não rodaram (saída {proc.returncode})"
        else:
            result.status = FAILED if failed or proc.returncode != 0 else PASSED
            result.summary = f"{len(result.tests)} teste(s), {failed} falha(s)"
    elif parser == "junit" or parser == "trx":
        # relatório com curinga (Maven/Gradle gravam um XML por classe de teste)
        reports = (
            sorted(Path(p) for p in globlib.glob(str(report), recursive=True))  # noqa: PTH207
            if report is not None and any(c in str(report) for c in "*?[")
            else ([report] if report is not None and report.is_file() else [])
        )
        if not reports:
            result.status = ERROR if proc.returncode != 0 else FAILED
            result.summary = f"relatório de testes não gerado (saída {proc.returncode}){_first_error(proc)}"
            return
        result.tests = []
        for path in reports:
            text = path.read_text(encoding="utf-8", errors="replace")
            result.tests += parsers.junit(text) if parser == "junit" else parsers.trx(text)
        failed = len(result.failed_tests)
        result.status = FAILED if failed or proc.returncode != 0 else PASSED
        result.summary = f"{len(result.tests)} teste(s), {failed} falha(s)"
    elif parser == "sarif":
        text = report.read_text(encoding="utf-8") if report is not None and report.is_file() else ""
        if not text and proc.returncode not in (0, 1):
            result.status, result.summary = ERROR, f"falha ao executar (saída {proc.returncode})"
            return
        result.findings = parsers.sarif(text, stage.get("default_severity"))
        blocking = _blocking(stage, result.findings)
        result.status = FAILED if blocking else PASSED
        result.summary = f"{len(result.findings)} achado(s), {len(blocking)} alto/crítico"
    elif parser == "mutation":  # F15 (Q-01): mutmut (cicd stats) ou Stryker (mutation report)
        data = json.loads(report.read_text(encoding="utf-8")) if report is not None and report.is_file() else None
        if data is None:
            result.status, result.summary = ERROR, f"relatório de mutação não gerado (saída {proc.returncode})"
            return
        score = parsers.mutation_score(data)
        result.mutation_score = score
        minimum = float(_fmt(str(stage.get("min_score", "0")), subs) or 0)
        result.status = FAILED if score is not None and score < minimum else PASSED
        result.summary = f"pontuação de mutação {score:.0%}" if score is not None else "sem mutantes avaliados"
        if score is not None and score < minimum:
            result.findings = [Finding("mutation", f"pontuação {score:.0%} < mínimo {minimum:.0%}", "high")]
    elif parser == "dotnet_vulnerable":
        result.findings = parsers.dotnet_vulnerable(proc.stdout)
        blocking = [f for f in result.findings if f.severity in ("high", "critical")]
        result.status = FAILED if blocking or proc.returncode != 0 else PASSED
        result.summary = f"{len(result.findings)} pacote(s) vulnerável(is)"
    else:
        result.status = PASSED if proc.returncode == 0 else FAILED
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-1:] or [""]
        result.summary = "ok" if proc.returncode == 0 else tail[0][:200]
        if proc.returncode != 0 and stage.get("kind") == "build":
            first = _first_error(proc)  # ex. Maven termina com "[ERROR] -> [Help 1]"; o erro real vem antes
            if first and first[2:] not in result.summary:
                result.summary = first[2:]
        if proc.returncode != 0 and stage.get("default_severity"):
            result.findings = [Finding(str(stage["id"]), result.summary, str(stage["default_severity"]))]
    coverage = coverage_file(stage, subs, workdir)
    if coverage is not None:
        reader = {"lcov": parsers.lcov, "jacoco": parsers.jacoco}.get(
            str(stage.get("coverage_format")), parsers.cobertura
        )
        result.coverage = reader(coverage.read_text(encoding="utf-8"))


ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _first_error(proc: subprocess.CompletedProcess[str]) -> str:
    """Primeira linha de erro da saída (ex. erro de compilação do teste), para a escalada dizer o porquê."""
    errors = [
        line.strip(" ✘[]").strip()
        for line in ANSI.sub("", (proc.stdout or "") + "\n" + (proc.stderr or "")).splitlines()
        if re.search(r"\berror\b|\bERROR\b|FAILURE|Exception", line)
    ]
    # a linha que aponta arquivo e posição diz mais que o cabeçalho ("COMPILATION ERROR :")
    located = [e for e in errors if re.search(r"\.\w+[:(\[]\s*\[?\d+", e)]
    best = (located or errors or [""])[0]
    return ": " + best[:200] if best else ""


def coverage_file(stage: dict[str, Any], subs: dict[str, str], workdir: Path) -> Path | None:
    """Relatório de cobertura da etapa: `coverage` (caminho) ou, sem ele, o primeiro do `coverage_glob`. Caminho
    relativo é do projeto (ex. `target/site/jacoco/jacoco.xml` do Maven)."""
    paths: list[Path] = []
    for key in ("coverage", "coverage_glob"):
        if stage.get(key):
            pattern = Path(_fmt(str(stage[key]), subs))
            pattern = pattern if pattern.is_absolute() else workdir / pattern
            paths += (
                [Path(p) for p in sorted(globlib.glob(str(pattern), recursive=True))]  # noqa: PTH207
                if key == "coverage_glob"
                else [pattern]
            )
    return next((p for p in paths if p.is_file()), None)


def java_tools(workdir: Path, *, ci: bool = False) -> dict[str, str]:
    """Wrapper do projeto quando existe (versão fixada pelo time); senão a ferramenta instalada. No CI (Linux, raiz do
    repositório) o wrapper vai relativo; localmente, absoluto (vale qualquer pasta de trabalho)."""
    windows = os.name == "nt" and not ci
    tools = {}
    for name, wrapper, win_ext in (("mvn", "mvnw", ".cmd"), ("gradle", "gradlew", ".bat")):
        path = workdir / (wrapper + win_ext if windows else wrapper)
        if not path.is_file():
            tools[name] = name
        else:
            tools[name] = f"./{wrapper}" if ci else str(path.resolve())
    return tools


def dotnet_target(workdir: Path) -> str:
    """Solução da raiz; sem ela, o projeto de testes (relativo); sem nenhum, vazio (a ferramenta decide)."""
    for pattern in ("*.slnx", "*.sln"):
        found = sorted(workdir.glob(pattern))
        if found:
            return found[0].name
    tests = sorted(
        p
        for p in workdir.rglob("*Tests.csproj")
        if not {"bin", "obj", "node_modules"} & set(p.relative_to(workdir).parts)
    )
    return tests[0].relative_to(workdir).as_posix() if tests else ""


def run_pipeline(
    stack: str,
    workdir: Path,
    out: Path,
    brain_dir: Path | None = None,
    only: list[str] | None = None,
    baseline: dict[str, Any] | None = None,
    changed: dict[str, set[int]] | None = None,
    commands: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
) -> QualityReport:
    """`baseline`, `changed` e `commands`: código existente — achados antigos não bloqueiam, lint só no que
    mudou, cobertura das linhas alteradas e comandos do próprio projeto para etapas de código de saída."""
    recipe = load_recipe(stack, brain_dir)
    out.mkdir(parents=True, exist_ok=True)
    subs = {
        "out": str(out.resolve()),
        "semgrep_config": str(recipe.get("semgrep_config", "p/default")),
        "min_mutation_score": "0",
        "dast_url": "",
        "dotnet_target": dotnet_target(workdir) if stack == "dotnet" else "",
        "pip_audit": companion_tool("pip-audit"),
        "semgrep": companion_tool("semgrep"),
        **(java_tools(workdir) if stack == "java" else {}),
        **(python_env(workdir, str((params or {}).get("python") or "")) if stack == "python" else {}),
        **{k: str(v) for k, v in (params or {}).items()},  # parâmetros da configuração (quality.*)
    }
    if stack == "python":  # o que o pip cria no projeto nunca vira alteração nem commit
        from nexo.kernel.workspace import git as git_workspace

        git_workspace.exclude_locally(workdir, PYTHON_LOCAL)
    report = QualityReport(stack)
    for stage in recipe["stages"]:
        if only and stage["id"] not in only and stage.get("kind") not in only and not stage.get("setup"):
            continue
        stage = _adapt(stage, changed, commands)
        if stage.get("_skip"):
            report.stages.append(
                StageResult(str(stage["id"]), str(stage.get("kind", stage["id"])), SKIPPED, False, str(stage["_skip"]))
            )
            continue
        if stage.get("builtin") == "standards":  # verificação do próprio Nexo, no processo
            from nexo.modules.quality.adapters import standards

            disabled = tuple(s for s in subs.get("standards_disabled", "").split(",") if s)
            result = standards.run(stage, workdir, out, stack, brain_dir, set(changed) if changed else None, disabled)
        else:
            result = run_stage(stage, workdir, out, subs)
        _relative_findings(result, workdir)
        if baseline is not None:
            _apply_baseline(result, baseline, stage)
        if changed is not None:
            _diff_coverage(stage, result, subs, changed, workdir)
        report.stages.append(result)
    return report


def _adapt(
    stage: dict[str, Any], changed: dict[str, set[int]] | None, commands: dict[str, str] | None
) -> dict[str, Any]:
    stage = dict(stage)
    override = (commands or {}).get(str(stage["id"]))
    if override and stage.get("parser", "exitcode") == "exitcode":
        stage["run"] = shlex.split(override)  # comando real do projeto; só para etapas de código de saída
        stage.pop("tool", None)
    exts = stage.get("changed_only")
    if changed is not None and exts:
        files = sorted(f for f in changed if any(f.endswith(e) for e in exts))
        if not files:
            stage["_skip"] = "nenhum arquivo alterado desta linguagem"
        else:
            run = list(stage["run"])
            stage["run"] = [*run[:-1], *files] if run and run[-1] == "." else [*run, *files]
    return stage


def _uri_to_path(uri: str) -> str:
    """URI de arquivo do SARIF → caminho com barras (`file:///D:/a/x.py` → `D:/a/x.py`)."""
    from urllib.parse import unquote, urlparse

    text = uri.replace("\\", "/")
    if text.startswith("file:"):
        text = unquote(urlparse(text).path)
    if re.match(r"^/[A-Za-z]:/", text):  # Windows: /D:/… → D:/…
        text = text[1:]
    return text


def _relative_findings(result: StageResult, workdir: Path) -> None:
    """Caminhos relativos: o mesmo achado no repositório e na worktree tem a mesma identidade (Windows incluso)."""
    root = str(workdir.resolve()).replace("\\", "/").rstrip("/")
    for f in result.findings:
        path = _uri_to_path(f.file)
        if path.lower().startswith(root.lower() + "/"):  # letra de drive e caixa variam no Windows
            path = path[len(root) + 1 :]
        f.file = path


def _blocking(stage: dict[str, Any], findings: list[Finding]) -> list[Finding]:
    if stage.get("fail_on") == "any":
        return list(findings)
    return [f for f in findings if f.severity in ("high", "critical")]


def _apply_baseline(result: StageResult, baseline: dict[str, Any], stage: dict[str, Any]) -> None:
    from nexo.kernel.brownfield import fingerprint

    known = set(baseline.get("findings") or [])
    if result.findings:
        new = [f for f in result.findings if fingerprint(result.id, f.rule, f.file, f.message) not in known]
        result.preexisting += len(result.findings) - len(new)
        result.findings = new
        blocking = _blocking(stage, new)
        if result.status == FAILED and not blocking:
            result.status = PASSED
        if stage.get("parser") == "sarif":
            result.summary = f"{len(new)} achado(s) novo(s), {len(blocking)} bloqueante(s)"
    failing_known = set(baseline.get("failing_tests") or [])
    if result.tests and result.status == FAILED:
        new_failures = [t for t in result.failed_tests if f"{t.classname}::{t.name}" not in failing_known]
        result.preexisting += len(result.failed_tests) - len(new_failures)
        if not new_failures:
            result.status = PASSED
    if result.preexisting:
        result.summary += f" · {result.preexisting} preexistente(s) ignorado(s)"


def _diff_coverage(
    stage: dict[str, Any], result: StageResult, subs: dict[str, str], changed: dict[str, set[int]], workdir: Path
) -> None:
    from nexo.kernel.brownfield import diff_coverage
    from nexo.kernel.workspace.codemap import coverage_lines

    path = coverage_file(stage, subs, workdir)
    if path is None:
        return
    text = path.read_text(encoding="utf-8")
    fmt = str(stage.get("coverage_format") or "cobertura")
    by_line = (
        parsers.lcov_lines(text)
        if fmt == "lcov"
        else parsers.jacoco_lines(text)
        if fmt == "jacoco"
        else coverage_lines(text)
    )
    value, hit, total = diff_coverage(changed, by_line)
    result.diff_coverage = value
    if value is not None:
        result.summary += f" · cobertura do diff {value:.0f}% ({hit}/{total} linhas)"


def prepare_env(stack: str, workdir: Path, out: Path, brain_dir: Path | None = None) -> list[StageResult]:
    """Etapas de preparo (`setup: true`, ex.: `.venv` e instalação no Python) num worktree novo, antes do agente:
    sem isso o agente não roda os testes."""
    recipe = load_recipe(stack, brain_dir)
    stages = [s for s in recipe["stages"] if s.get("setup")]
    if not stages:
        return []
    return run_pipeline(stack, workdir, out, brain_dir, [str(s["id"]) for s in stages]).stages


def lint_files(stack: str, workdir: Path, out: Path, files: list[str], brain_dir: Path | None = None) -> list[Finding]:
    """Lint só dos arquivos informados, com correção automática quando a receita a define (`fix`).
    Usado antes de travar testes escritos por agentes: teste travado com problema de lint seria um impasse."""
    if not files:
        return []
    recipe = load_recipe(stack, brain_dir)
    out.mkdir(parents=True, exist_ok=True)
    subs = {
        "out": str(out.resolve()),
        "semgrep_config": str(recipe.get("semgrep_config", "p/default")),
        **(python_env(workdir) if stack == "python" else {}),
        **(java_tools(workdir) if stack == "java" else {}),
    }
    changed: dict[str, set[int]] = {f: set() for f in files}
    remaining: list[Finding] = []
    for raw in recipe["stages"]:
        if raw.get("kind") != "lint" or not raw.get("changed_only") or raw.get("skip_on_new_tests"):
            continue
        stage = _adapt(raw, changed, None)
        if stage.get("_skip"):
            continue
        targets = [f for f in files if any(f.endswith(e) for e in raw["changed_only"])]
        if raw.get("fix"):
            _exec(
                [*expand(raw["fix"], subs), *targets],
                workdir,
                {},
                int(raw.get("timeout", DEFAULT_TIMEOUT)),
            )
        result = run_stage(stage, workdir, out, subs)
        _relative_findings(result, workdir)
        if result.status in (FAILED, ERROR):
            remaining += result.findings or [Finding(str(raw["id"]), result.summary, "medium")]
    return remaining
