"""`nexo quality run` e `nexo quality ci` — a mesma receita local e no GitHub Actions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.platform import config
from nexo.kernel.quality.model import QualityReport, telemetry_summary
from nexo.modules.quality.adapters.runner import (
    ARGS_SEP,
    applies,
    dotnet_target,
    java_tools,
    load_recipe,
    python_env,
    run_pipeline,
)

SETUP = {
    "python": [{"uses": "actions/setup-python@v5", "with": {"python-version": "3.12", "cache": "pip"}}],
    "dotnet": [{"uses": "actions/setup-dotnet@v4", "with": {"dotnet-version": "8.0.x"}}],
    "react": [{"uses": "actions/setup-node@v4", "with": {"node-version": "22", "cache": "npm"}}],
    "angular": [{"uses": "actions/setup-node@v4", "with": {"node-version": "22", "cache": "npm"}}],
    "java": [{"uses": "actions/setup-java@v4", "with": {"distribution": "temurin", "java-version": "21"}}],
    "flutter": [{"uses": "subosito/flutter-action@v2", "with": {"channel": "stable", "cache": True}}],
}


def _stack(ctx: AppContext, stack: str | None) -> tuple[Path, str]:
    root = ctx.project_root
    if root is None:
        raise NexoError("nenhum projeto Nexo encontrado; rode `nexo init`")
    stacks = config.resolved(ctx).values.get("stacks") or []
    chosen = stack or (str(stacks[0]) if stacks else "")
    if not chosen:
        raise NexoError("stack não detectada; informe --stack")
    return root, chosen


def quality_params(quality_cfg: dict[str, Any]) -> dict[str, str]:
    """parâmetros das etapas opcionais (pontuação mínima de mutação, URL do ambiente para o DAST)."""
    return {
        "min_mutation_score": str(quality_cfg.get("min_mutation_score", 0)),
        "dast_url": str(quality_cfg.get("dast_url") or ""),
    }


def standards_params(ctx: AppContext) -> dict[str, str]:
    """regras desligadas (`standards.disabled`) só valem da org ou do squad; do projeto ou pessoal, ignoradas."""
    res = config.resolved(ctx)
    disabled = [str(r) for r in (res.values.get("standards") or {}).get("disabled") or []]
    origin = res.origin.get("standards.disabled", "default")
    if disabled and origin not in ("default", "org", "squad"):
        ctx.audit("policy.violation", {"rule": "standards-disabled-local", "level": origin})
        disabled = []
    return {"standards_disabled": ",".join(disabled)}


def run(ctx: AppContext, stack: str | None, only: list[str] | None, path: Path | None) -> QualityReport:
    root, chosen = _stack(ctx, stack)
    workdir = (path or root).resolve()
    out = root / ".nexo" / "quality" / "latest"
    params = {**quality_params(dict(config.resolved(ctx).values.get("quality") or {})), **standards_params(ctx)}
    report = run_pipeline(chosen, workdir, out, ctx.brain_dir if ctx.brain_dir.is_dir() else None, only, params=params)
    (out / "quality-report.json").write_text(
        json.dumps(report.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ctx.audit("quality.run", telemetry_summary(report))
    return report


def _shell(cmd: list[str]) -> str:
    def q(a: str) -> str:
        if "$" in a:
            return '"' + a.replace('"', '\\"') + '"'
        return a if all(c.isalnum() or c in "-_./=:{}@,%" for c in a) else "'" + a.replace("'", "'\\''") + "'"

    return " ".join(q(a) for a in cmd)


def workflow(stack: str, recipe: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    steps: list[dict[str, Any]] = [{"uses": "actions/checkout@v4"}, *SETUP.get(stack, [])]
    steps.append({"name": "Pasta de artefatos", "run": 'mkdir -p "$RUNNER_TEMP/nexo"'})
    # ferramentas que vêm com o Nexo (antes baixadas pelo uvx)
    steps.append({"name": "Ferramentas de segurança (pip)", "run": "python3 -m pip install --quiet pip-audit semgrep"})
    pyenv = python_env(root or Path(), ci=True) if stack == "python" else {}
    for stage in recipe["stages"]:
        tool = stage.get("tool")
        if tool == "gitleaks":
            steps.append(
                {
                    "name": "Segredos (gitleaks)",
                    "uses": "gitleaks/gitleaks-action@v2",
                    # o relatório fica no log do passo; sem artefato (a cota do Actions esgotada reprovava o passo)
                    # relatório no log do passo: sem artefato (cota do Actions) e sem comentar no PR (permissão só de
                    # leitura no workflow de qualidade) — os dois reprovavam o passo no teste real
                    "env": {
                        "GITHUB_TOKEN": "${{ secrets.GITHUB_TOKEN }}",
                        "GITLEAKS_ENABLE_UPLOAD_ARTIFACT": "false",
                        "GITLEAKS_ENABLE_COMMENTS": "false",
                    },
                }
            )
            continue
        param = str(stage.get("when_param") or "")
        if param in pyenv:  # instalação do projeto Python decidida pelo repositório na geração
            if not pyenv[param]:
                continue
        elif param:  # DAST depende de ambiente efêmero da entrega — fora do CI genérico
            continue
        if stage.get("builtin") == "standards":  # precisa do Nexo no runner; sem ele, vale o portão da história
            steps.append(
                {
                    "name": "standards (padrão de código)",
                    "run": 'if command -v nexo >/dev/null; then nexo quality standards check --changed-from "origin/'
                    '${GITHUB_BASE_REF:-main}"; else echo "nexo ausente no runner: o padrão é conferido no portão'
                    ' da história e na revisão do PR"; fi',
                }
            )
            continue
        subs = {
            "{out}": "$RUNNER_TEMP/nexo",
            "{semgrep_config}": str(recipe.get("semgrep_config", "p/default")),
            "{min_mutation_score}": "0",
            "{dotnet_target}": dotnet_target(root) if stack == "dotnet" and root is not None else "",
            **{"{" + k + "}": v for k, v in java_tools(root or Path(), ci=True).items()},
            **{"{" + k + "}": v for k, v in pyenv.items()},
            "{pip_audit}": "pip-audit",
            "{semgrep}": "semgrep",
        }
        run_cmd = [*stage["run"]]
        for extra in stage.get("optional_args") or []:  # decidido pelo repositório no momento da geração
            if root is not None and applies(extra, root) is None:
                run_cmd += extra["args"]
        lines = []
        for c in [*(stage.get("pre") or []), run_cmd]:
            cmd = c["run"] if isinstance(c, dict) else c
            words = [w for a in cmd for w in _sub(str(a), subs).split(ARGS_SEP) if w != ""]
            line = _shell(words)
            if isinstance(c, dict) and c.get("stdout"):  # saída para arquivo (ex.: pip freeze)
                line += f' > "{_sub(str(c["stdout"]), subs)}"'
            lines.append(line)
        body = "\n".join(lines)
        if stage.get("unless_exists"):  # ex.: cria o .venv só se o repositório não tiver um
            target = _sub(str(stage["unless_exists"]), subs)
            body = f"if [ ! -e {_shell([target])} ]; then\n" + "\n".join("  " + ln for ln in lines) + "\nfi"
        cond_file = stage.get("when_contains")
        if cond_file:  # o Actions não lê conteúdo de arquivo: a condição vira guarda no próprio passo
            path, text = str(cond_file["file"]), str(cond_file["text"])
            indented = "\n".join("  " + ln for ln in lines)
            body = (
                f"if grep -qF {_shell([text])} {path if any(c in path for c in '*?[') else _shell([path])}"
                f" 2>/dev/null; then\n{indented}\n"
                f"else\n  echo {_shell([f'não se aplica: {path} não contém {text}'])}\nfi"
            )
        step: dict[str, Any] = {"name": f"{stage['id']} ({stage.get('kind', '')})", "run": body, "shell": "bash"}
        cond = _condition(stage)
        if cond:
            step["if"] = cond
        if not stage.get("required", True):
            step["continue-on-error"] = True  # etapa opcional: ferramenta pode não estar no runner
        steps.append(step)
    steps.append(
        {
            "uses": "actions/upload-artifact@v4",
            "if": "always()",
            "continue-on-error": True,  # cota de armazenamento esgotada não reprova a qualidade (achado no CI real)
            "with": {"name": "nexo-quality", "path": "${{ runner.temp }}/nexo", "retention-days": 14},
        }
    )
    return {
        "name": "nexo-quality",
        "on": {"pull_request": None, "push": {"branches": ["main"]}},
        "permissions": {"contents": "read"},
        "jobs": {"quality": {"runs-on": "ubuntu-latest", "steps": steps}},
    }


def _sub(value: str, subs: dict[str, str]) -> str:
    for k, v in subs.items():
        value = value.replace(k, v)
    return value


def _condition(stage: dict[str, Any]) -> str | None:
    if "when_exists" in stage:
        return f"hashFiles('{stage['when_exists']}') != ''"
    if "when_glob" in stage:
        return f"hashFiles('{stage['when_glob']}') != ''"
    return None


def generate_ci(ctx: AppContext, stack: str | None) -> Path:
    root, chosen = _stack(ctx, stack)
    data = workflow(chosen, load_recipe(chosen, ctx.brain_dir if ctx.brain_dir.is_dir() else None), root)
    target = root / ".github" / "workflows" / "nexo-quality.yml"
    target.parent.mkdir(parents=True, exist_ok=True)
    y = YAML()
    y.default_flow_style = False
    y.width = 200
    with target.open("w", encoding="utf-8") as fh:
        fh.write("# Gerado por `nexo quality ci` — mesma receita usada localmente pelo Nexo.\n")
        y.dump(data, fh)
    ctx.audit("quality.ci_generated", {"stack": chosen, "path": str(target)})
    return target


def standards_check(ctx: AppContext, stack: str | None = None, changed_from: str | None = None) -> dict[str, Any]:
    """`nexo quality standards check`: só a etapa de padrão de código, no repositório inteiro ou no que mudou
    desde `ref`."""
    from nexo.kernel.workspace import codemap

    root, chosen = _stack(ctx, stack)
    out = root / ".nexo" / "quality" / "standards"
    changed = codemap.changed_lines(root, changed_from) if changed_from else None
    report = run_pipeline(
        chosen,
        root,
        out,
        ctx.brain_dir if ctx.brain_dir.is_dir() else None,
        ["standards"],
        changed=changed,
        params=standards_params(ctx),
    )
    stage = report.stage("standards")
    if stage is None:
        raise NexoError("etapa de padrão de código não encontrada na receita")
    ctx.audit("quality.run", telemetry_summary(report))
    return {
        "stack": chosen,
        "status": stage.status,
        "summary": stage.summary,
        "standards": telemetry_summary(report)["standards"],
        "violations": [
            {"rule": f.rule, "file": f.file, "line": f.line, "severity": f.severity, "message": f.message}
            for f in stage.findings
        ],
        "sarif": str(out / "standards.sarif"),
    }


def standards_rules(ctx: AppContext, stack: str | None = None) -> list[dict[str, Any]]:
    """Regras que valem para o projeto (fábrica → cérebro → projeto, menos as desligadas pela org)."""
    from nexo.modules.quality.adapters.standards import load_rules

    root, chosen = _stack(ctx, stack)
    disabled = tuple(s for s in standards_params(ctx)["standards_disabled"].split(",") if s)
    rules = load_rules(chosen, root, ctx.brain_dir if ctx.brain_dir.is_dir() else None, disabled)
    return [
        {"id": r.id, "title": r.title, "kind": r.kind, "severity": r.severity, "skill": r.skill, "paths": list(r.paths)}
        for r in rules
    ]
