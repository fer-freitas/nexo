"""Avaliação comparativa de skills e rotas com tarefas de referência do histórico real."""

from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.finops.costs import usage_cost_brl
from nexo.kernel.finops.money import brl
from nexo.kernel.platform import config
from nexo.kernel.runtime import yamlfile
from nexo.kernel.workspace import git
from nexo.modules.finops.app import costs as finops_costs
from nexo.modules.knowledge.domain.learning.model import VariantResult, compare, wilson
from nexo.modules.quality.adapters.runner import run_pipeline
from nexo.store.runs import now_iso, project_key

TEST_HINTS = ("tests/", "test/", ".Tests/", ".test.", ".spec.", "test_")


def _root(ctx: AppContext) -> Path:
    if ctx.project_root is None:
        raise NexoError("nenhum projeto Nexo encontrado")
    return ctx.project_root


def golden_dir(root: Path) -> Path:
    return root / ".nexo" / "evals" / "golden"


def _is_test(path: str) -> bool:
    return any(h in path for h in TEST_HINTS)


# ---------------- tarefas de referência (7.4)


def build_from_stories(ctx: AppContext) -> list[str]:
    root = _root(ctx)
    scope = project_key(root)
    created = []
    for run in ctx.conn.execute(
        "SELECT id, target_id, base_sha, branch FROM runs WHERE project = ? AND branch LIKE"
        " 'nexo/story/%' ORDER BY started_at",
        (scope,),
    ).fetchall():
        state_file = root / ".nexo" / "runs" / run["id"] / "story.json"
        item = ctx.conn.execute(
            "SELECT state FROM work_items WHERE project = ? AND id = ?", (scope, run["target_id"])
        ).fetchone()
        if not state_file.is_file() or item is None or item["state"] != "Concluido":
            continue
        st = json.loads(state_file.read_text(encoding="utf-8"))
        tests = {p: git.git(["show", f"{run['branch']}:{p}"], root, check=False) for p in st.get("locked", {})}
        from nexo.kernel.workspace import tasks as workspace_tasks

        tasks = [
            t
            for t in workspace_tasks.list_tasks(root)
            if t.story == run["target_id"]
            and not t.id.endswith(("-QA",))
            and "-FIX" not in t.id
            and not t.id.endswith("-INT")
        ]
        description = "\n\n".join(f"### {t.title}\n{t.description}" for t in tasks)
        acceptance = [a for t in tasks for a in t.acceptance]
        files = sorted({f for t in tasks for f in t.files_hint})
        created.append(
            _write_golden(
                root,
                f"G-{run['target_id']}",
                str(run["base_sha"]),
                str(root),
                run["target_id"],
                description or run["target_id"],
                acceptance,
                files,
                tests,
            )
        )
    ctx.audit("eval.golden_built", {"tasks": created, "source": "stories"})
    return created


def build_from_commit(ctx: AppContext, repo: Path, commit: str) -> str:
    """Repositório modelo indicado pelo time: o commit vira tarefa (base = pai; testes do commit = critério)."""
    root = _root(ctx)
    repo = repo.resolve()
    parent = git.git(["rev-parse", f"{commit}^"], repo)
    message = git.git(["log", "-1", "--format=%B", commit], repo)
    changed = git.git(["show", "--name-only", "--format=", commit], repo).splitlines()
    tests = {p: git.git(["show", f"{commit}:{p}"], repo) for p in changed if _is_test(p)}
    if not tests:
        raise NexoError(f"o commit {commit[:10]} não altera testes: não serve como tarefa de referência")
    gid = f"G-{repo.name}-{commit[:8]}"
    _write_golden(root, gid, parent, str(repo), None, message, [], [p for p in changed if not _is_test(p)], tests)
    ctx.audit("eval.golden_built", {"tasks": [gid], "source": "commit", "repo": repo.name})
    return gid


def _write_golden(
    root: Path,
    gid: str,
    base: str,
    repo: str,
    story: str | None,
    description: str,
    acceptance: list[str],
    files: list[str],
    tests: dict[str, str],
) -> str:
    folder = golden_dir(root) / gid
    if folder.exists():
        shutil.rmtree(folder)
    (folder / "tests").mkdir(parents=True)
    for path, content in tests.items():
        target = folder / "tests" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    yamlfile.dump(
        folder / "task.yaml",
        {
            "id": gid,
            "story": story,
            "base_sha": base,
            "repo": repo,
            "description": description,
            "acceptance": acceptance,
            "files_hint": files,
            "tests": sorted(tests),
        },
    )
    return gid


def list_golden(ctx: AppContext) -> list[dict[str, Any]]:
    return [
        yamlfile.load(p / "task.yaml")
        for p in sorted(golden_dir(_root(ctx)).glob("G-*"))
        if (p / "task.yaml").is_file()
    ]


# ---------------- execução (7.5)


@dataclass(frozen=True)
class Variant:
    name: str
    route: str  # tool/model
    extra_prompt: str = ""


def _eval_branch(ctx: AppContext, golden: dict[str, Any]) -> str:
    """Branch base da avaliação: commit base + testes de referência já presentes (e travados)."""
    root = _root(ctx)
    branch = f"nexo/eval/{golden['id']}"
    if git.branch_exists(root, branch):
        return branch
    repo = Path(str(golden["repo"]))
    if repo.resolve() != root.resolve():
        git.git(["fetch", "-q", str(repo), str(golden["base_sha"])], root)
    tmp = root / ".nexo" / "worktrees" / f"eval-{golden['id']}"
    git.add_worktree(root, tmp, branch, str(golden["base_sha"]))
    src = golden_dir(root) / str(golden["id"]) / "tests"
    for path in golden["tests"]:
        dest = tmp / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / path, dest)
    git.checkpoint(tmp, f"eval: testes de referência de {golden['id']}")
    git.git(["worktree", "remove", "--force", str(tmp)], root, check=False)
    return branch


def run(ctx: AppContext, task_id: str, variants: list[Variant], runs: int, kind: str) -> dict[str, Any]:
    from nexo.modules.execution.app.run import Orchestrator, RunOptions

    root = _root(ctx)
    scope = project_key(root)
    golden = next((g for g in list_golden(ctx) if g["id"] == task_id), None)
    if golden is None:
        raise NexoError(f"tarefa de referência {task_id} não encontrada (nexo eval tasks)")
    branch = _eval_branch(ctx, golden)
    stacks = config.resolved(ctx).values.get("stacks") or ["python"]
    batch = uuid.uuid4().hex[:8]
    pricing = finops_costs.pricing(ctx)
    for variant in variants:
        tool, _, model = variant.route.partition("/")
        for i in range(runs):
            safe = re.sub(r"[^A-Za-z0-9_-]", "_", variant.name)
            eid = f"EVAL-{batch}-{safe}-{i + 1}"
            yamlfile.dump(
                root / ".nexo" / "tasks" / f"{eid}.yaml",
                {
                    "title": f"Avaliação {task_id}",
                    "kind": "backend",
                    "description": golden["description"]
                    + (
                        "\n\nOs testes de aceite já existem e estão travados: faça-os passar."
                        if golden["tests"]
                        else ""
                    ),
                    "acceptance": golden.get("acceptance") or ["testes de referência passam"],
                    "files_hint": (golden.get("files_hint") or [])[:8],
                    "stack": str(stacks[0]),
                },
            )
            started = time.monotonic()
            opts = RunOptions(
                tool=tool,
                model=model or None,
                base_ref=branch,
                locked=frozenset(golden["tests"]),
                extra_prompt=variant.extra_prompt,
            )
            report = Orchestrator(ctx, opts).run_task(eid)
            seconds = time.monotonic() - started
            success = False
            detail: dict[str, Any] = {"state": report.state, "reason": report.reason}
            if report.state == "Concluido" and report.worktree:
                quality = run_pipeline(
                    str(stacks[0]),
                    Path(report.worktree),
                    root / ".nexo" / "evals" / "out" / eid,
                    ctx.brain_dir if ctx.brain_dir.is_dir() else None,
                    only=["unit"],
                )
                unit = quality.stage("unit")
                success = bool(unit and unit.status == "passed")
                detail["unit"] = unit.summary if unit else "sem etapa unit"
            cost = sum(
                usage_cost_brl(ln, pricing) for ln in finops_costs.usage_lines(ctx, project=scope) if ln.target == eid
            )
            ctx.conn.execute(
                "INSERT INTO eval_results(project, batch, task, kind, variant, route, success, cost_brl,"
                " seconds, detail, at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    scope,
                    batch,
                    task_id,
                    kind,
                    variant.name,
                    variant.route,
                    int(success),
                    round(cost, 4),
                    round(seconds, 2),
                    json.dumps(detail, ensure_ascii=False),
                    now_iso(),
                ),
            )
            _cleanup(root, eid, report.worktree)
    ctx.audit("eval.run", {"task": task_id, "batch": batch, "variants": [v.name for v in variants], "runs": runs})
    return report_batch(ctx, batch)


def _cleanup(root: Path, eid: str, worktree: str | None) -> None:
    (root / ".nexo" / "tasks" / f"{eid}.yaml").unlink(missing_ok=True)
    if worktree:
        git.git(["worktree", "remove", "--force", worktree], root, check=False)
    git.git(["branch", "-q", "-D", f"nexo/task/{eid}"], root, check=False)


def report_batch(ctx: AppContext, batch: str | None = None) -> dict[str, Any]:
    scope = project_key(_root(ctx))
    if batch is None:
        row = ctx.conn.execute(
            "SELECT batch FROM eval_results WHERE project = ? ORDER BY id DESC LIMIT 1", (scope,)
        ).fetchone()
        if row is None:
            raise NexoError("nenhuma avaliação executada ainda")
        batch = str(row["batch"])
    rows = ctx.conn.execute(
        "SELECT * FROM eval_results WHERE project = ? AND batch = ? ORDER BY id", (scope, batch)
    ).fetchall()
    by_variant: dict[str, VariantResult] = {}
    task = kind = ""
    for r in rows:
        v = by_variant.setdefault(r["variant"], VariantResult(r["variant"], 0, 0))
        v.runs += 1
        v.successes += int(r["success"])
        v.cost_brl.append(float(r["cost_brl"]))
        v.seconds.append(float(r["seconds"]))
        task, kind = str(r["task"]), str(r["kind"])
    variants = list(by_variant.values())
    summary = [
        {
            "variant": v.variant,
            "runs": v.runs,
            "success_rate": round(v.rate, 3),
            "ci90": wilson(v.successes, v.runs),
            "avg_cost_brl": round(sum(v.cost_brl) / v.runs, 3),
            "avg_seconds": round(sum(v.seconds) / v.runs, 1),
        }
        for v in variants
    ]
    comparison = compare(variants[0], variants[1]) if len(variants) == 2 else None
    return {"batch": batch, "task": task, "kind": kind, "variants": summary, "comparison": comparison}


# ---------------- portão de regressão de prompts e skills (Q-07)


def gate(
    ctx: AppContext,
    route: str,
    runs: int,
    baseline: Path | None,
    max_drop: float = 0.10,
    max_cost_increase: float = 0.25,
    write: Path | None = None,
) -> dict[str, Any]:
    """Roda todas as tarefas de referência com o conteúdo atual (Nexo + cérebro) e compara com a linha de base.

    Regressão = taxa de sucesso geral cai mais que `max_drop`, tarefa que passava deixa de passar em todas as
    execuções, ou custo médio sobe mais que `max_cost_increase`. Usado no PR do cérebro e no release do Nexo.
    """
    from nexo import __version__
    from nexo.kernel.brain import cached_status

    tasks = list_golden(ctx)
    if not tasks:
        raise NexoError("nenhuma tarefa de referência (nexo eval tasks --from-repo/--from-stories)")
    results: dict[str, dict[str, Any]] = {}
    for golden in tasks:
        rep = run(ctx, str(golden["id"]), [Variant("atual", route)], runs, "gate")
        v = rep["variants"][0]
        results[str(golden["id"])] = {
            "success_rate": v["success_rate"],
            "avg_cost_brl": v["avg_cost_brl"],
            "runs": v["runs"],
        }
    rates = [r["success_rate"] for r in results.values()]
    costs = [r["avg_cost_brl"] for r in results.values()]
    current: dict[str, Any] = {
        "nexo": __version__,
        "brain": str((cached_status(ctx.brain_dir) or {}).get("version") or ""),
        "route": route,
        "runs": runs,
        "success_rate": round(sum(rates) / len(rates), 3),
        "avg_cost_brl": round(sum(costs) / len(costs), 4),
        "tasks": results,
    }
    regressions: list[str] = []
    base: dict[str, Any] | None = None
    if baseline is not None and baseline.is_file():
        base = dict(json.loads(baseline.read_text(encoding="utf-8")))
        drop = float(base.get("success_rate", 0)) - current["success_rate"]
        if drop > max_drop:
            regressions.append(
                f"taxa de sucesso caiu {drop:.0%} ({base['success_rate']:.0%} → {current['success_rate']:.0%})"
            )
        for tid, old in dict(base.get("tasks") or {}).items():
            new = results.get(tid)
            if new is not None and float(old.get("success_rate", 0)) > 0 and new["success_rate"] == 0:
                regressions.append(f"{tid} deixou de passar")
        old_cost = float(base.get("avg_cost_brl") or 0)
        if old_cost and current["avg_cost_brl"] > old_cost * (1 + max_cost_increase):
            regressions.append(f"custo médio subiu de {brl(old_cost)} para {brl(current['avg_cost_brl'])}")
    if write is not None:
        write.parent.mkdir(parents=True, exist_ok=True)
        write.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    ctx.audit(
        "eval.gate",
        {
            "route": route,
            "success_rate": current["success_rate"],
            "regressions": len(regressions),
            "baseline": base is not None,
        },
    )
    return {**current, "baseline": base, "regressions": regressions, "passed": not regressions}


def propose(ctx: AppContext, report: dict[str, Any]) -> str | None:
    from nexo.modules.knowledge.app import brain

    comp = report.get("comparison") or {}
    if not comp.get("winner"):
        return None
    lines = [
        f"Avaliação `{report['batch']}` na tarefa `{report['task']}` ({report['kind']}):",
        "",
        "| variante | execuções | sucesso | IC 90% | custo médio | tempo médio |",
        "|---|---|---|---|---|---|",
        *[
            f"| {v['variant']} | {v['runs']} | {v['success_rate']:.0%} | {v['ci90'][0]:.2f}–{v['ci90'][1]:.2f} | "
            f"{brl(v['avg_cost_brl'])} | {v['avg_seconds']} s |"
            for v in report["variants"]
        ],
        "",
        f"**Resultado**: {comp['verdict']}",
        "",
        "Aprovar = promover a variante vencedora no nexo-brain.",
    ]
    return brain.create_issue(
        ctx, f"Avaliação: {comp['winner']} vence em {report['task']}", "\n".join(lines), ["nexo", "avaliacao"]
    )
