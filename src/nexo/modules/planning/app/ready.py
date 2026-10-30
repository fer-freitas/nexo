"""Definition of Ready e teste de performance.

`nexo ready`: confere o refinamento das histórias contra o modelo único. `nexo quality perf`: gera o script k6
com os limites
do refinamento, confere que o script não afrouxou os limites e compara o resultado do teste com eles.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from nexo.kernel import ready as ready_domain
from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.platform import config, flags
from nexo.kernel.runtime import yamlfile
from nexo.kernel.workspace import specs
from nexo.modules.planning.app.clarify import feature_for_story

MODES = ("required", "warn", "off")


def _root(ctx: AppContext) -> Path:
    if ctx.project_root is None:
        raise NexoError("rode dentro de um repositório com o Nexo (nexo init)")
    return ctx.project_root


def mode(ctx: AppContext) -> str:
    """`gates.definition_of_ready`: required (bloqueia a execução), warn (avisa) ou off. Padrão: warn."""
    value = str((config.resolved(ctx).values.get("gates") or {}).get("definition_of_ready") or "warn")
    return value if value in MODES else "warn"


def floors(ctx: AppContext) -> dict[str, float]:
    gates = config.resolved(ctx).values.get("gates") or {}
    raw = gates.get("ready_floors") or {}
    return {k: float(v) for k, v in raw.items() if k in ("coverage", "mutation") and isinstance(v, int | float)}


def _story(root: Path, story_id: str) -> tuple[Path, list[str], list[str]]:
    folder = feature_for_story(root, story_id)
    backlog = specs.load_backlog(folder) if folder else None
    story = next((s for s in backlog.stories if s.id == story_id), None) if backlog else None
    if folder is None or backlog is None or story is None:
        raise NexoError(f"história {story_id} não encontrada no backlog")
    kinds = [t.kind for t in backlog.tasks if t.story == story_id]
    return folder, list(story.ac_refs), kinds


def refinement_path(folder: Path, story_id: str) -> Path:
    return folder / "refinamento" / f"{story_id}.yaml"


def load_refinement(root: Path, story_id: str) -> dict[str, Any] | None:
    folder, _, _ = _story(root, story_id)
    path = refinement_path(folder, story_id)
    return dict(yamlfile.load(path)) if path.is_file() else None


def init(ctx: AppContext, story_id: str) -> Path:
    flags.require(ctx, "ready")
    folder, acs, kinds = _story(_root(ctx), story_id)
    path = refinement_path(folder, story_id)
    if path.exists():  # a aprovação do plano já cria; `ready --init` não falha por isso
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    data = ready_domain.skeleton(story_id, acs, kinds)
    contracts = sorted((folder / "contracts").glob("*.*")) if (folder / "contracts").is_dir() else []
    if contracts and data["testes"]["contrato"].get("aplica"):  # contrato gerado no planejamento já vem indicado
        data["testes"]["contrato"]["artefato"] = contracts[0].relative_to(_root(ctx)).as_posix()
    yamlfile.dump(path, data)
    ctx.audit("ready.initialized", {"story": story_id})
    return path


def init_all(ctx: AppContext, story_ids: list[str]) -> list[str]:
    """A DoR de cada história nasce na aprovação do plano, pré-preenchida pela spec — o tech
    lead só completa; ninguém precisa digitar `ready --init`. Devolve as histórias com refinamento novo."""
    if mode(ctx) == "off":
        return []
    created = []
    for sid in story_ids:
        try:
            folder, _, _ = _story(_root(ctx), sid)
        except NexoError:  # história sem backlog local
            continue
        if not refinement_path(folder, sid).exists():
            init(ctx, sid)
            created.append(sid)
    return created


def check(ctx: AppContext, story_id: str) -> dict[str, Any]:
    root = _root(ctx)
    folder, acs, kinds = _story(root, story_id)
    path = refinement_path(folder, story_id)
    data = dict(yamlfile.load(path)) if path.is_file() else None
    rep = ready_domain.check(data, story_id, acs, kinds, floors(ctx))
    result = {
        "story": story_id,
        "ready": rep.ready,
        "problems": rep.problems,
        "warnings": rep.warnings,
        "refinement": path.as_posix(),
    }
    ctx.audit("ready.checked", {"story": story_id, "ready": rep.ready, "problems": len(rep.problems)}, ref=story_id)
    return result


def check_many(ctx: AppContext, target: str) -> list[dict[str, Any]]:
    """`US-x` confere uma história; `F-x` confere todas as histórias da feature."""
    flags.require(ctx, "ready")
    root = _root(ctx)
    if target.upper().startswith("F-"):
        folder = specs.feature_dir(root, target.upper())
        backlog = specs.load_backlog(folder)
        if backlog is None:
            raise NexoError(f"feature {target} sem backlog")
        return [check(ctx, s.id) for s in backlog.stories]
    return [check(ctx, target)]


def gate(ctx: AppContext, story_id: str) -> str | None:
    """Portão antes da execução da história: devolve o motivo do bloqueio (modo required) ou None."""
    m = mode(ctx)
    if m == "off" or not flags.enabled(ctx, "ready"):
        return None
    result = check(ctx, story_id)
    if result["ready"]:
        return None
    reason = f"Definition of Ready não atendida: {'; '.join(result['problems'][:5])}"
    if m == "warn":
        ctx.audit("ready.warned", {"story": story_id, "problems": len(result["problems"])}, ref=story_id)
        return None
    return reason


# ---------------------------------------------------------------- performance


def _perf(ctx: AppContext, story_id: str) -> tuple[Path, dict[str, Any]]:
    flags.require(ctx, "ready")
    root = _root(ctx)
    folder, _, _ = _story(root, story_id)
    data = load_refinement(root, story_id)
    perf = ((data or {}).get("testes") or {}).get("performance") or {}
    if not perf.get("aplica"):
        raise NexoError(f"{story_id}: o refinamento não prevê teste de performance")
    problems: list[str] = []
    ready_domain._check_performance(perf, problems.append)
    if problems:
        raise NexoError(f"{story_id}: performance incompleta no refinamento — {'; '.join(problems)}")
    return folder, perf


def script_path(root: Path, story_id: str) -> Path:
    return root / "tests" / "load" / f"{story_id.lower()}.js"


def write_script(ctx: AppContext, story_id: str, force: bool = False) -> Path:
    _, perf = _perf(ctx, story_id)
    path = script_path(_root(ctx), story_id)
    if path.exists() and not force:
        raise NexoError(f"script já existe: {path.relative_to(_root(ctx))} (use --force para refazer)")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ready_domain.k6_script(story_id, perf), encoding="utf-8")
    ctx.audit("perf.script", {"story": story_id})
    return path


def check_result(ctx: AppContext, story_id: str, summary: Path) -> dict[str, Any]:
    """Compara o resultado do k6 (`k6 run --summary-export`) com os limites e registra na feature."""
    folder, perf = _perf(ctx, story_id)
    root = _root(ctx)
    script = script_path(root, story_id)
    problems = []
    if script.is_file():
        problems = ready_domain.script_matches(script.read_text(encoding="utf-8"), perf["limites"])
    try:
        data = json.loads(summary.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise NexoError(f"resultado do k6 ilegível: {exc}") from exc
    result = ready_domain.evaluate_summary(data, perf["limites"])
    result["problems"] = problems + result["problems"]
    result["passed"] = not result["problems"]
    result.update({"story": story_id, "at": time.time(), "scenario": perf.get("cenario"), "load": perf["carga"]})
    out = folder / "perf" / f"{story_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    ctx.audit(
        "perf.checked",
        {
            "story": story_id,
            "passed": result["passed"],
            **{k: v for k, v in result["measured"].items() if v is not None},
        },
        ref=story_id,
    )
    return result


def perf_status(root: Path, folder: Path, story_id: str) -> dict[str, Any]:
    """Situação do teste de performance de uma história para o pacote de evidências."""
    path = refinement_path(folder, story_id)
    perf = (((dict(yamlfile.load(path)) if path.is_file() else {}).get("testes") or {}).get("performance")) or {}
    if not perf:
        return {"story": story_id, "required": False, "status": "sem refinamento"}
    if not perf.get("aplica"):
        return {
            "story": story_id,
            "required": False,
            "status": "não se aplica",
            "why": perf.get("justificativa_nao_aplica"),
        }
    res_path = folder / "perf" / f"{story_id}.json"
    if not res_path.is_file():
        return {"story": story_id, "required": True, "status": "pendente"}
    res = json.loads(res_path.read_text(encoding="utf-8"))
    return {
        "story": story_id,
        "required": True,
        "status": "aprovado" if res.get("passed") else "reprovado",
        "measured": res.get("measured"),
        "limits": res.get("limits"),
    }
