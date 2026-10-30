"""`nexo quality run` — a mesma receita local e no GitHub Actions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from nexo.kernel import services
from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.platform import config
from nexo.kernel.quality.model import QualityReport, telemetry_summary

run_pipeline = services.get("quality.runner", "run_pipeline")

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
    return cast("QualityReport", report)


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
    from nexo.kernel import services

    load_rules = services.get("quality.standards", "load_rules")

    root, chosen = _stack(ctx, stack)
    disabled = tuple(s for s in standards_params(ctx)["standards_disabled"].split(",") if s)
    rules = load_rules(chosen, root, ctx.brain_dir if ctx.brain_dir.is_dir() else None, disabled)
    return [
        {"id": r.id, "title": r.title, "kind": r.kind, "severity": r.severity, "skill": r.skill, "paths": list(r.paths)}
        for r in rules
    ]
