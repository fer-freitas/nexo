"""Fase de qualidade e revisões: receita, testes instáveis, revisores, rastreabilidade e limiares."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nexo.kernel import services
from nexo.kernel.errors import NexoError
from nexo.kernel.gates.confidence import GateInput, ReviewVerdict, decide, risk_areas
from nexo.kernel.quality.model import ERROR, FAILED, PASSED, QualityReport, telemetry_summary
from nexo.kernel.quality.traceability import AcTrace, ac_ids_in, trace
from nexo.kernel.runtime import yamlfile
from nexo.kernel.spec.model import Spec
from nexo.kernel.workspace import git
from nexo.modules.execution.app import llm
from nexo.modules.execution.app.quality import quality_params, standards_params
from nexo.modules.execution.app.story import bugs as story_bugs
from nexo.modules.execution.app.story import publish as story_publish
from nexo.modules.execution.app.story.common import (
    REVIEW_SCHEMA,
    StoryReport,
    _is_manifest,
    is_test_file,
    sha256_file,
)
from nexo.store.runs import now_iso, project_key

approval = services.load("delivery.approval")
content = services.load("knowledge.content")
run_pipeline = services.get("quality.runner", "run_pipeline")

if TYPE_CHECKING:
    from nexo.modules.execution.app.story.runner import StoryRunner


TEST_KINDS = ("unit", "integration", "e2e", "mutation")


def environment_errors(report: QualityReport) -> list[Any]:
    """Etapas obrigatórias que nem conseguiram rodar. Com o build reprovado (código que não compila), o erro dos
    testes é consequência do código e vai para o ciclo de correção, não para escalada de ambiente."""
    build_failed = any(s.required and s.kind == "build" and s.status == FAILED for s in report.stages)
    return [
        s for s in report.stages if s.required and s.status == ERROR and not (build_failed and s.kind in TEST_KINDS)
    ]


def autofix(r: StoryRunner, run_id: str, st: dict[str, Any], worktree: Path, attempt: int) -> None:
    """Correção determinística de lint (ex.: ordem de imports, formatação) no código alterado pelo dev, antes do
    pipeline: não gasta uma rodada de agente com o que a ferramenta corrige sozinha.
    Testes travados do QA ficam de fora (o hash deles é a garantia do test-first)."""
    from nexo.kernel import services

    lint_files = services.get("quality.runner", "lint_files")

    locked = set(st.get("locked") or {})
    files = [
        f
        for f in git.changed_files(worktree, st["base"])
        if f not in locked and not is_test_file(f) and (worktree / f).is_file()
    ]
    if not files:
        return
    lint_files(r._stack(), worktree, r._out(run_id, f"autofix-{attempt}"), files, r._brain())
    before = git.head(worktree)
    if git.checkpoint(worktree, "nexo: correções automáticas de lint") != before:
        r.ctx.audit("story.autofix", {"story": st.get("story", ""), "files": files[:20]}, ref=run_id)


def run_quality(
    r: StoryRunner, story_id: str, run_id: str, st: dict[str, Any], spec: Spec, folder: Path, worktree: Path
) -> StoryReport | None:
    if r.store.item_state(story_id) == "EmDev":
        r._transition(story_id, "implemented", "retomada na fase de qualidade")
    attempt = st["attempts"]
    from nexo.kernel import services
    from nexo.kernel.workspace import codemap

    discover = services.load("planning.discover")

    quality_cfg = dict(r.cfg.get("quality") or {})
    autofix(r, run_id, st, worktree, attempt)
    baseline = discover.load_baseline(r.root)
    diff_only = baseline is not None or bool(quality_cfg.get("diff_only"))
    report = run_pipeline(
        r._stack(),
        worktree,
        r._out(run_id, f"quality-{attempt}"),
        r._brain(),
        baseline=baseline,
        changed=codemap.changed_lines(worktree, st["base"]) if diff_only else None,
        commands={str(k): str(v) for k, v in (quality_cfg.get("commands") or {}).items()},
        params={**quality_params(quality_cfg), **standards_params(r.ctx)},
    )
    report.commit = git.head(worktree) or ""
    report = retry_flaky(r, report, worktree, run_id, attempt)
    save(r, run_id, report)
    r.ctx.audit("quality.run", {"story": story_id, "run": run_id, **telemetry_summary(report)}, ref=story_id)
    env_errors = environment_errors(report)
    if env_errors:
        return r._escalate(
            story_id,
            run_id,
            st,
            "ambiente sem condições de validar: " + "; ".join(f"{s.id} ({s.summary})" for s in env_errors),
        )
    locked_ok = all((worktree / f).is_file() and sha256_file(worktree / f) == h for f, h in st["locked"].items())
    ac_ids = r._story_acs(story_id, spec)
    file_tags = {
        f: ac_ids_in((worktree / f).read_text(encoding="utf-8", errors="replace"))
        for f in st["locked"]
        if (worktree / f).is_file()
    }
    traceability = trace(ac_ids, report.tests, file_tags)
    write_traceability(r, folder, story_id, traceability, report.commit, run_id)
    changed = git.changed_files(worktree, st["base"])
    gate_input = GateInput(
        report,
        traceability,
        [],
        locked_ok,
        changed,
        git.diff_lines(worktree, st["base"]),
        new_dependencies=[f for f in changed if _is_manifest(f)],
        history_success=r._history(),
        spec_stable=bool(st.get("spec_stable", True)),
        min_coverage=float((r.cfg.get("quality") or {}).get("min_coverage", 0)),
        min_diff_coverage=float((r.cfg.get("quality") or {}).get("min_diff_coverage", 0)),
    )
    pre = decide(gate_input, r.risk_map, r.always_human, threshold(r))
    if pre.hard_failures:
        return story_bugs.bug(r, story_id, run_id, st, "quality", pre.hard_failures, report, traceability)
    r._transition(story_id, "qa_passed")
    if st.get("dependency_only") and all(_is_manifest(f) for f in changed):
        # só manifesto/lockfile mudou — o pipeline (testes, SCA) é a evidência; revisão de IA não agrega
        r.ctx.audit("story.reviews_skipped", {"story": story_id, "reason": "só dependências"}, ref=story_id)
        verdicts: list[ReviewVerdict] = []
        spec_issue = ""
    else:
        problem = story_publish.docs(r, story_id, run_id, st, spec, worktree)  # documentação no mesmo PR
        if problem is not None:
            return problem
        verdicts, spec_issue = reviews(r, story_id, run_id, st, spec, report, traceability, worktree)
    if spec_issue:
        return story_bugs.spec_return(r, story_id, run_id, st, folder, spec_issue)
    gate_input.reviews = verdicts
    result = decide(
        gate_input,
        r.risk_map,
        r.always_human,
        threshold(r),
        (r.cfg.get("gates") or {}).get("weights"),
    )
    if st.get("characterization_missing") and result.decision != "fail":
        result.decision = "human"
        result.human_areas = [*result.human_areas, "código legado sem caracterização"]
    from nexo.kernel import services

    injection_suspects = services.get("planning.plan", "injection_suspects")

    if injection_suspects(folder) and result.decision != "fail":  # S-03: insumo externo suspeito
        result.decision = "human"
        result.human_areas = [*result.human_areas, "insumo externo com possível injeção de prompt"]
    if st.get("review_unavailable") and result.decision != "fail":
        result.decision = "human"
        result.human_areas = [*result.human_areas, "revisão independente indisponível"]
    from nexo.kernel.workflow.autonomy import auto_gate
    from nexo.modules.execution.app import autonomy

    st["autonomy"] = autonomy.level(r.ctx)  # autonomia conquistada do squad × área
    if result.decision == "auto" and not auto_gate(st["autonomy"]):
        result.decision = "human"
        result.human_areas = [*result.human_areas, f"autonomia {st['autonomy']}: aprovação humana"]
    st["gate"] = {
        "decision": result.decision,
        "score": result.score,
        "breakdown": result.breakdown,
        "human_areas": result.human_areas,
        "hard_failures": result.hard_failures,
        "threshold": result.threshold,
    }
    r.ctx.audit("gate.decision", {"gate": "code", "story": story_id, **st["gate"]}, ref=story_id)
    r.ui("gate", st["gate"])
    if result.decision == "fail":
        return story_bugs.bug(
            r, story_id, run_id, st, "review", result.hard_failures, report, traceability, reviews=verdicts
        )
    st["phase"] = "done"
    if result.decision == "auto":
        st["approved_by"] = "automático"
        r._save(run_id, st)
        r._transition(story_id, "auto_approve", f"confiança {result.score:.2f}")
        r.store.update_run(run_id, state="Concluido", ended_at=now_iso())
        story_publish.as_built(r, story_id)
        return r._report(story_id, run_id, f"aprovado automaticamente (confiança {result.score:.2f})")
    reason = (
        f"áreas que exigem humano: {', '.join(result.human_areas)}"
        if result.human_areas
        else f"confiança {result.score:.2f} < {result.threshold:.2f}"
    )
    r._transition(story_id, "needs_human", reason)
    r.store.update_run(run_id, state="AguardandoHumano")
    if approval.mode(r.ctx, "quality") == "external":
        st["external_approval"] = approval.request(
            r.ctx, "quality", story_id, f"portão de qualidade: {reason}", {"score": result.score}
        )
        r._save(run_id, st)
        return r._report(story_id, run_id, f"{reason} — aprovação externa {st['external_approval']}")
    from contextlib import suppress

    from nexo.kernel import services

    remote_approval = services.load("delivery.remote_approval")

    link = None
    with suppress(NexoError, OSError):  # sem central, segue a aprovação pelo terminal
        link = remote_approval.request(r.ctx, r.root, story_id, run_id, "code")
    r._notify(story_id, "portão aguardando humano", reason, link)
    return r._report(story_id, run_id, reason)


def retry_flaky(r: StoryRunner, report: QualityReport, worktree: Path, run_id: str, attempt: int) -> QualityReport:
    """Falha de teste que passa na reexecução = instabilidade de ambiente."""
    for i, stage in enumerate(report.stages):
        if stage.status == FAILED and stage.kind in ("unit", "integration", "e2e"):
            again = run_pipeline(
                r._stack(),
                worktree,
                r._out(run_id, f"quality-{attempt}-retry"),
                r._brain(),
                only=[stage.id],
            ).stage(stage.id)
            if again and again.status == PASSED:
                again.summary += " (instável: passou na reexecução)"
                report.stages[i] = again
                r.ctx.audit("quality.flaky", {"stage": stage.id, "run": run_id}, ref=run_id)
    return report


def reviews(
    r: StoryRunner,
    story_id: str,
    run_id: str,
    st: dict[str, Any],
    spec: Spec,
    report: QualityReport,
    traceability: list[AcTrace],
    worktree: Path,
) -> tuple[list[ReviewVerdict], str]:
    diff = git.diff(worktree, st["base"])
    acceptance = "\n".join(
        f"- {a.id}: {a.title} (Quando {'; '.join(a.when)} Então {'; '.join(a.then)})"
        for a in spec.acceptance
        if a.id in r._story_acs(story_id, spec)
    )
    quality = "\n".join(f"- {s.id}: {s.status} — {s.summary}" for s in report.stages)
    trace_text = "\n".join(f"- {t.ac}: {t.status} ({', '.join(t.tests) or 'sem teste'})" for t in traceability)
    vendors = tuple(st.get("vendors") or [])
    prompt = content.prompt(r.ctx, "reviewer", story_id).format(
        story=story_id, title=story_id, acceptance=acceptance, quality=quality, traceability=trace_text, diff=diff
    )
    try:
        rev = llm.call_structured(
            r.ctx,
            "reviewer",
            story_id,
            prompt,
            REVIEW_SCHEMA,
            worktree,
            data_class=spec.data_class or "confidential",
            read_files=True,
            ui=r.ui,
            author_vendors=vendors,
            scope=project_key(r.root),
        )
    except NexoError as exc:
        if "nenhuma rota" not in exc.message:
            raise
        # ADR-0007: sem fornecedor independente disponível → revisão determinística + portão humano
        st["review_unavailable"] = exc.message
        r.ctx.audit("story.review_unavailable", {"story": story_id, "reason": exc.message}, ref=story_id)
        r.ui("info", {"message": "revisão independente indisponível: portão humano obrigatório"})
        return [], ""
    areas = risk_areas(git.changed_files(worktree, st["base"]), [], r.risk_map)
    tool_findings = "; ".join(f"{s.id}: {len(s.findings)}" for s in report.stages if s.kind == "security")
    sec_prompt = content.prompt(r.ctx, "security-reviewer", story_id).format(
        data_class=spec.data_class or "confidential",
        areas=", ".join(areas) or "nenhuma",
        tool_findings=tool_findings,
        diff=diff,
    )
    sec = llm.call_structured(
        r.ctx,
        "security-reviewer",
        story_id,
        sec_prompt,
        REVIEW_SCHEMA,
        worktree,
        data_class=spec.data_class or "confidential",
        read_files=True,
        ui=r.ui,
        author_vendors=vendors,
        scope=project_key(r.root),
    )
    verdicts = [
        ReviewVerdict("reviewer", str(rev.data["verdict"]), tuple(rev.data.get("findings") or [])),
        ReviewVerdict("security-reviewer", str(sec.data["verdict"]), tuple(sec.data.get("findings") or [])),
    ]
    st["reviews"] = [{"reviewer": v.reviewer, "verdict": v.verdict, "findings": list(v.findings)} for v in verdicts]
    r.ctx.audit("story.reviews", {"story": story_id, "reviews": st["reviews"]}, ref=story_id)
    spec_issue = ""
    if rev.data.get("spec_issue"):
        reasons = "; ".join(str(f.get("message")) for f in rev.data.get("findings") or [] if f.get("message"))
        if reasons or str(rev.data["verdict"]) != "approve":
            spec_issue = reasons or "especificação ambígua"
        else:  # aprovou, sem apontamento e marcou ambiguidade: resposta contraditória (achado no autoteste)
            detail = {"story": story_id, "reason": "aprovado sem apontamento"}
            r.ctx.audit("review.spec_issue_ignored", detail, ref=story_id)
    return verdicts, spec_issue


def save(r: StoryRunner, run_id: str, report: QualityReport) -> None:
    path = r.root / ".nexo" / "runs" / run_id / "quality-report.json"
    path.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def write_traceability(
    r: StoryRunner, folder: Path, story_id: str, traces: list[AcTrace], commit: str, run_id: str
) -> None:
    path = folder / "traceability.yaml"
    data = yamlfile.load(path) or {"matrix": []}
    by_ac = {t.ac: t for t in traces}
    for row in data.get("matrix", []):
        t = by_ac.get(row.get("ac"))
        if t is not None:
            row["tests"] = t.tests
            row["status"] = t.status
            row["evidence"] = [f"commit {commit[:12]}", f".nexo/runs/{run_id}/quality-report.json"]
    yamlfile.dump(path, data)


def threshold(r: StoryRunner) -> float:
    gates = r.cfg.get("gates") or {}
    return float((gates.get("thresholds") or {}).get("code", gates.get("default_threshold", 0.95)))
