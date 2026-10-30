"""`nexo discover`: mapa do código existente, baseline de achados, convenções locais e design as-is.

Tudo determinístico, sem LLM. Saída em `.nexo/map/` (ignorada pelo git: tem nomes de autores do histórico).
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from nexo.kernel import brownfield
from nexo.kernel.brownfield import FileInfo
from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.platform import config
from nexo.kernel.workspace import codemap, git

MAP_DIR = "map"


def map_dir(root: Path) -> Path:
    return root / ".nexo" / MAP_DIR


def load_map(root: Path) -> dict[str, Any] | None:
    path = map_dir(root) / "map.json"
    return dict(json.loads(path.read_text(encoding="utf-8"))) if path.is_file() else None


def load_files(root: Path) -> list[FileInfo]:
    data = load_map(root) or {}
    return [FileInfo(**f) for f in data.get("files") or []]


def load_baseline(root: Path) -> dict[str, Any] | None:
    path = map_dir(root) / "baseline.json"
    return dict(json.loads(path.read_text(encoding="utf-8"))) if path.is_file() else None


def _ensure_gitignore(root: Path) -> None:
    ignore = root / ".nexo" / ".gitignore"
    lines = ignore.read_text(encoding="utf-8").splitlines() if ignore.is_file() else []
    if f"{MAP_DIR}/" not in lines:
        ignore.write_text("\n".join([*lines, f"{MAP_DIR}/"]) + "\n", encoding="utf-8")


def _stack(ctx: AppContext) -> str:
    return str((config.resolved(ctx).values.get("stacks") or ["python"])[0])


def _baseline(ctx: AppContext, root: Path, out: Path) -> tuple[dict[str, Any], dict[str, float]]:
    """Roda o pipeline na versão atual: achados e testes que já falham viram baseline (não bloqueiam depois)."""
    from nexo.kernel import services

    run_pipeline = services.get("quality.runner", "run_pipeline")

    brain = ctx.brain_dir if ctx.brain_dir.is_dir() else None
    report = run_pipeline(_stack(ctx), root, out / "quality", brain)
    findings = sorted(
        {brownfield.fingerprint(s.id, f.rule, f.file, f.message) for s in report.stages for f in s.findings}
    )
    failing = sorted({t.classname + "::" + t.name for t in report.tests if t.status in ("failed", "error")})
    coverage: dict[str, float] = {}
    cov_xml = out / "quality" / "coverage.xml"
    if cov_xml.is_file():
        for path, lines in codemap.coverage_lines(cov_xml.read_text(encoding="utf-8")).items():
            if lines:
                coverage[path] = round(100 * sum(1 for h in lines.values() if h) / len(lines), 1)
    stages = {s.id: {"status": s.status, "summary": s.summary} for s in report.stages}
    return (
        {"commit": git.head(root) or "", "findings": findings, "failing_tests": failing, "stages": stages},
        coverage,
    )


def discover(ctx: AppContext, with_baseline: bool = True, as_is: bool = False) -> dict[str, Any]:
    root = ctx.project_root
    if root is None:
        raise NexoError("nenhum projeto Nexo encontrado; rode `nexo init`")
    started = time.monotonic()
    out = map_dir(root)
    out.mkdir(parents=True, exist_ok=True)
    _ensure_gitignore(root)
    files = codemap.scan(root)
    baseline: dict[str, Any] | None = None
    if with_baseline:
        baseline, coverage = _baseline(ctx, root, out)
        for f in files:
            f.coverage = next((v for k, v in coverage.items() if f.path.endswith(k) or k.endswith(f.path)), None)
        (out / "baseline.json").write_text(json.dumps(baseline, ensure_ascii=False, indent=1), encoding="utf-8")
    deps = codemap.dependencies(root)
    frameworks = codemap.test_frameworks(root, files, deps)
    tools = codemap.tool_configs(root)
    conv = brownfield.conventions(files, tools, frameworks)
    commands = codemap.pipeline_commands(root)
    hot = brownfield.hotspots(files)
    data: dict[str, Any] = {
        "generated_at": time.time(),
        "commit": git.head(root) or "",
        "summary": codemap.summary_config(files),
        "files": [asdict(f) for f in files],
        "entrypoints": codemap.entrypoints(files),
        "dependencies": deps,
        "test_frameworks": frameworks,
        "tools": tools,
        "conventions": conv,
        "commands": commands,
        "hotspots": [f.path for f in hot],
    }
    (out / "map.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    skill = _conventions_skill(out, conv, deps, commands)
    report: dict[str, Any] = {
        "files": len(files),
        "languages": data["summary"]["languages"],
        "tests": data["summary"]["tests"],
        "entrypoints": data["entrypoints"],
        "hotspots": data["hotspots"][:5],
        "conventions": conv,
        "commands": commands,
        "baseline": {
            "findings": len(baseline["findings"]),
            "failing_tests": len(baseline["failing_tests"]),
            "stages": baseline["stages"],
        }
        if baseline
        else None,
        "conventions_skill": str(skill),
        "seconds": round(time.monotonic() - started, 1),
    }
    if as_is:
        report["as_is"] = str(write_as_is(ctx, root, files, data))
    ctx.audit(
        "discover.done",
        {"files": len(files), "findings": report["baseline"]["findings"] if baseline else None},
    )
    return report


def _conventions_skill(out: Path, conv: dict[str, Any], deps: dict[str, list[str]], commands: dict[str, Any]) -> Path:
    """Proposta de skill `project-conventions` para revisão humana (`nexo extensions skill add` para adotar)."""
    folder = out / "project-conventions"
    folder.mkdir(parents=True, exist_ok=True)
    naming = "\n".join(f"- {lang}: {style}" for lang, style in conv["naming"].items()) or "- (não identificado)"
    libs = "\n".join(f"- {eco}: {', '.join(names[:15])}" for eco, names in deps.items()) or "- (nenhuma declarada)"
    cmds = (
        "\n".join(f"- {stage}: `{c[0]['command']}` ({c[0]['source']})" for stage, c in commands.items())
        or "- (não identificados)"
    )
    text = f"""---
name: project-conventions
description: Convenções reais deste repositório (extraídas pelo nexo discover). Use ao alterar código existente.
---
# Convenções do projeto

> Proposta gerada por `nexo discover` — revise antes de adotar:
> `nexo extensions skill add .nexo/map/project-conventions --scope project`.

## Política
- **Código novo** (arquivos e módulos novos): siga os padrões da org (skills de stack).
- **Alteração em código existente**: siga as convenções locais abaixo, mesmo que difiram do padrão da org.
- Não refatore estilo de trechos que a tarefa não pede (mudança mínima; o portão avalia só o diff).

## Nomenclatura observada
{naming}

## Testes
- Frameworks: {", ".join(conv["test_frameworks"]) or "(nenhum detectado)"}
- Pasta de testes: `{conv["test_dir"] or "(não identificada)"}`
- Antes de mudar comportamento sem teste, escreva testes de caracterização do comportamento atual.

## Ferramentas configuradas
{", ".join(conv["tools"]) or "(nenhuma)"}

## Bibliotecas em uso (prefira as existentes a introduzir novas)
{libs}

## Comandos do projeto
{cmds}
"""
    (folder / "SKILL.md").write_text(text, encoding="utf-8")
    (folder / "extension.yaml").write_text(
        "name: project-conventions\ntype: skill\nscope: project\nversion: 0.1.0\n"
        'description: "Convenções reais deste repositório"\n',
        encoding="utf-8",
    )
    return folder


def write_as_is(ctx: AppContext, root: Path, files: list[FileInfo], data: dict[str, Any]) -> Path:
    """Design técnico as-is pelo template."""
    from nexo.modules.planning.app import templates

    modules: dict[str, list[FileInfo]] = {}
    for f in files:
        if not f.is_test:
            modules.setdefault(f.path.rsplit("/", 1)[0] if "/" in f.path else ".", []).append(f)
    langs = ", ".join(f"{k} ({v})" for k, v in data["summary"]["languages"].items())
    covered = [f.coverage for f in files if f.coverage is not None]
    context = {
        "feature": root.name,
        "mode": "as-is",
        "context": f"Repositório com {data['summary']['files']} arquivos de código ({langs}) e "
        f"{data['summary']['tests']} arquivos de teste. Pontos de entrada: "
        + (", ".join(f"`{e}`" for e in data["entrypoints"]) or "não identificados")
        + ".",
        "solution": "Documento as-is gerado a partir do código atual; descreve como o sistema funciona hoje.",
        "components": [
            {
                "name": mod,
                "responsibility": "; ".join(s for f in fs for s in f.symbols[:3])[:200] or f"{len(fs)} arquivo(s)",
                "change": "existente",
            }
            for mod, fs in sorted(modules.items())
        ],
        "impacted": [],
        "hotspots": data["hotspots"][:10],
        "coverage": f"{sum(covered) / len(covered):.0f}% (média por arquivo)" if covered else "não medida",
        "testing": "Frameworks: " + (", ".join(data["test_frameworks"]) or "nenhum detectado"),
    }
    rendered = templates.render(ctx, "technical-design", context, root)
    path = map_dir(root) / "design-tecnico-as-is.md"
    note = (
        "\n> Seções a completar pelo time (o código não revela): " + ", ".join(rendered.missing) + "\n"
        if rendered.missing
        else ""
    )
    path.write_text(rendered.text + note, encoding="utf-8")
    return path


def impacted_low_coverage(root: Path, targets: list[str], threshold: float) -> list[FileInfo]:
    """Arquivos de produção já existentes que a história vai tocar e que têm pouca ou nenhuma cobertura."""
    by_path = {f.path: f for f in load_files(root)}
    out = []
    for t in targets:
        f = by_path.get(t)
        if f is not None and not f.is_test and (f.coverage is None or f.coverage < threshold):
            out.append(f)
    return out


def context_for(root: Path, targets: list[str], budget: int) -> str:
    files = load_files(root)
    if not files:
        return ""
    return brownfield.context_text(brownfield.related(files, targets), budget)


def ensure_map(ctx: AppContext, root: Path) -> None:
    """Gera o mapa do código (sem IA, sem linha de base) se ainda não existe: a IA planeja e explica sabendo onde
    está cada coisa (achado na gravação: "não há mapa do repositório" aparecia como dúvida da tarefa)."""
    if load_map(root) is not None:
        return
    try:
        discover(ctx, with_baseline=False)
        ctx.audit("discover.auto", {"reason": "mapa ausente"})
    except Exception as exc:  # mapa é apoio: falhar aqui nunca impede planejar ou explicar
        ctx.audit("discover.auto_failed", {"error": str(exc)[:200]})


def repo_overview(root: Path, query: str, budget: int = 6000) -> str | None:
    """Mapa ranqueado para o breakdown: arquivos mais relacionados ao texto da spec primeiro (tokens contados)."""
    import re

    files = [f for f in load_files(root) if not f.is_test]
    if not files:
        return None
    words = {w for w in re.findall(r"[a-zA-Z_]{4,}", query.lower())}

    def score(f: FileInfo) -> int:
        text = (f.path + " " + " ".join(f.symbols)).lower()
        return sum(1 for w in words if w in text)

    ranked = sorted(files, key=lambda f: (-score(f), -f.hotspot, f.path))
    return brownfield.context_text(ranked, budget)
