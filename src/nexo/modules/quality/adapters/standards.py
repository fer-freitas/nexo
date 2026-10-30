"""Etapa `standards` do pipeline: lê as regras (fábrica, cérebro, projeto) e os arquivos e roda a verificação.

Roda no próprio processo, sem ferramenta externa. Grava o resultado em SARIF na pasta de artefatos (evidência) e
devolve os achados no formato do pipeline: regra `error` vira achado alto (bloqueia), `warning` vira médio.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from ruamel.yaml.error import YAMLError

from nexo.kernel.errors import NexoError
from nexo.kernel.quality.model import ERROR, FAILED, PASSED, Finding, StageResult
from nexo.kernel.quality.standards import EXTENSIONS, SEVERITY, Rule, check, merge_layers, parse_rules, to_sarif
from nexo.kernel.runtime import yamlfile
from nexo.kernel.runtime.resources import embedded_dir

MAX_BYTES = 1_000_000
IGNORED = ("node_modules/", "bin/", "obj/", ".venv/", "dist/", "build/", ".nexo/", ".git/")


def _layer(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        data = yamlfile.load(path)
        return [dict(r) for r in data.get("rules") or []]
    except (YAMLError, ValueError, TypeError, AttributeError) as exc:  # YAML inválido ou formato errado
        raise NexoError(f"regras de padrão inválidas em {path}: {str(exc).splitlines()[0]}") from exc


def load_rules(stack: str, workdir: Path, brain_dir: Path | None = None, disabled: tuple[str, ...] = ()) -> list[Rule]:
    """Fábrica (`resources/standards`) → cérebro da org (`standards/`) → projeto (`.nexo/standards.yaml`)."""
    layers: list[list[dict[str, Any]]] = []
    for base in [embedded_dir("standards"), *([brain_dir / "standards"] if brain_dir else [])]:
        layers += [_layer(base / "common.yaml"), _layer(base / f"{stack}.yaml")]
    layers.append(_layer(workdir / ".nexo" / "standards.yaml"))
    try:
        return parse_rules(merge_layers(*layers), disabled)
    except ValueError as exc:
        raise NexoError(f"regra de padrão inválida: {exc}") from exc


def repo_files(workdir: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=workdir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        files = proc.stdout.splitlines()
    else:
        files = [p.relative_to(workdir).as_posix() for p in workdir.rglob("*") if p.is_file()]
    return sorted(f for f in files if not f.startswith(IGNORED) and "/node_modules/" not in f)


def run(
    stage: dict[str, Any],
    workdir: Path,
    out: Path,
    stack: str,
    brain_dir: Path | None = None,
    changed: set[str] | None = None,
    disabled: tuple[str, ...] = (),
) -> StageResult:
    result = StageResult(str(stage["id"]), "standards", PASSED, bool(stage.get("required", True)))
    start = time.perf_counter()
    try:
        rules = load_rules(stack, workdir, brain_dir, disabled)
    except NexoError as exc:  # regra mal escrita não derruba a execução: a etapa fica em erro com o motivo
        result.status, result.summary = ERROR, exc.message
        return result
    existing = set(repo_files(workdir))
    targets = sorted(f for f in (changed if changed is not None else existing) if f in existing)
    exts = EXTENSIONS.get(stack, ())
    texts: dict[str, str] = {}
    for rel in targets:
        path = workdir / rel
        if rel.endswith(exts) and path.is_file() and path.stat().st_size <= MAX_BYTES:
            texts[rel] = path.read_text(encoding="utf-8", errors="replace")
    violations = check(texts, rules, stack, existing)
    (out / "standards.sarif").write_text(json.dumps(to_sarif(violations, rules), ensure_ascii=False), encoding="utf-8")
    result.findings = [Finding(v.rule, v.message, SEVERITY[v.severity], v.file, v.line) for v in violations]
    result.checked = len(texts)
    errors = [v for v in violations if v.severity == "error"]
    result.status = FAILED if errors else PASSED
    result.summary = (
        f"{len(texts)} arquivo(s), {len(rules)} regra(s): {len(violations)} violação(ões), {len(errors)} erro(s)"
    )
    result.seconds = round(time.perf_counter() - start, 2)
    return result
