"""Métricas, estimativa, histórico para a nota de confiança e calibração dos portões (6.7)."""

from __future__ import annotations

import itertools
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.finops.costs import usage_cost_brl
from nexo.kernel.finops.metrics import Estimate, MetricsReport, StoryFacts, estimate, success_rate, summarize
from nexo.kernel.platform import config
from nexo.kernel.workspace import specs
from nexo.modules.finops.app import costs
from nexo.store.runs import project_key


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _transitions(ctx: AppContext, item: str) -> list[tuple[datetime, str, str]]:
    out = []
    for e in ctx.events.recent(limit=5000, ref=item):
        if e.type == "state.transition" and e.payload.get("item", e.payload.get("task")) == item:
            out.append((_ts(e.at), str(e.payload.get("from")), str(e.payload.get("to"))))
    return out


def _belongs(target: str, targets: set[str]) -> bool:
    return target in targets or any(target.startswith(f"{t}-") for t in targets)


def _story_state_file(root: Path, run_ids: list[str]) -> dict[str, Any]:
    for rid in reversed(run_ids):
        path = root / ".nexo" / "runs" / rid / "story.json"
        if path.is_file():
            return dict(json.loads(path.read_text(encoding="utf-8")))
    return {}


def story_facts(ctx: AppContext, only: str | None = None) -> list[StoryFacts]:
    root = ctx.project_root
    if root is None:
        raise NexoError("nenhum projeto Nexo encontrado")
    scope = project_key(root)
    cfg = config.resolved(ctx).values
    stack = str((cfg.get("stacks") or ["?"])[0])
    pricing = costs.pricing(ctx)
    lines = costs.usage_lines(ctx, project=scope)
    facts: list[StoryFacts] = []
    for folder in sorted(specs.specs_root(root).glob("F-*")):
        backlog = specs.load_backlog(folder) if folder.is_dir() else None
        if backlog is None:
            continue
        for story in backlog.stories:
            if only is not None and story.id != only:
                continue
            row = ctx.conn.execute(
                "SELECT state FROM work_items WHERE project = ? AND id = ?", (scope, story.id)
            ).fetchone()
            state = str(row["state"]) if row else "Pronto"
            tasks = {t.id for t in backlog.tasks if t.story == story.id}
            targets = {story.id, *tasks}
            mine = [ln for ln in lines if _belongs(ln.target, targets)]
            runs = [
                r["id"]
                for r in ctx.conn.execute(
                    "SELECT id FROM runs WHERE project = ? AND target_id = ? ORDER BY started_at", (scope, story.id)
                )
            ]
            st = _story_state_file(root, runs)
            trans = _transitions(ctx, story.id)
            start = next((t for t, _, to in trans if to == "EmDev"), None)
            first = trans[0][0] if trans else None
            done = next((t for t, _, to in trans if to == "Concluido"), None)
            waiting = 0.0
            for (t0, _, to), nxt in itertools.pairwise(trans):
                if to == "AguardandoHumano":
                    waiting += (nxt[0] - t0).total_seconds() / 3600
            clarifications = sum(
                1
                for e in ctx.events.recent(limit=5000)
                if e.type == "agent.clarification" and str(e.payload.get("task", "")) in targets | {f"{story.id}-QA"}
            )
            facts.append(
                StoryFacts(
                    story=story.id,
                    stack=stack,
                    points=story.points or 1,
                    cost_brl=round(sum(usage_cost_brl(ln, pricing) for ln in mine), 2),
                    state=state,
                    human_gate=any(to == "AguardandoHumano" for _, _, to in trans),
                    escalated=any(to == "Escalado" for _, _, to in trans),
                    bugs=len(st.get("bugs") or []),
                    attempts=int(st.get("attempts", 0)),
                    spec_returns=int(st.get("spec_returns", 0)),
                    clarifications=clarifications,
                    lead_hours=round((done - first).total_seconds() / 3600, 3) if done and first else None,
                    cycle_hours=round((done - start).total_seconds() / 3600, 3) if done and start else None,
                    waiting_human_hours=round(waiting, 3),
                    tokens=sum(ln.units.get("tokens_in", 0) + ln.units.get("tokens_out", 0) for ln in mine),
                    handoffs=sum(
                        int(r["n"])
                        for r in ctx.conn.execute(
                            "SELECT r.target_id, COUNT(h.seq) AS n FROM runs r JOIN handoffs h ON h.run_id = r.id"
                            " WHERE r.project = ? GROUP BY r.target_id",
                            (scope,),
                        )
                        if _belongs(str(r["target_id"]), targets)
                    ),
                    feature=backlog.feature,
                    started_at=first.isoformat() if first else None,
                    done_at=done.isoformat() if done else None,
                    cap_brl=float(st["cap_brl"]) if st.get("cap_brl") is not None else None,
                    estimate_brl=float(st["estimate_brl"]) if st.get("estimate_brl") is not None else None,
                )
            )
    return facts


def metrics(ctx: AppContext) -> MetricsReport:
    report = summarize(story_facts(ctx))
    ctx.audit("metrics.computed", {"stories": report.stories, "done": report.done})
    return report


def history_success(ctx: AppContext, stack: str | None = None) -> float:
    """Taxa de entrega certa (sem bug) da stack, com prior Beta(7,3) — entra na nota de confiança."""
    try:
        facts = [f for f in story_facts(ctx) if f.state == "Concluido" and (stack is None or f.stack == stack)]
    except NexoError:
        return success_rate(0, 0)
    return success_rate(sum(1 for f in facts if f.bugs == 0), len(facts))


def estimate_feature(ctx: AppContext, target: str | None, points: int | None) -> dict[str, Any]:
    root = ctx.project_root
    if root is None:
        raise NexoError("nenhum projeto Nexo encontrado")
    if points is None:
        if not target:
            raise NexoError("informe uma feature aprovada (F-n) ou --points")
        backlog = specs.load_backlog(specs.feature_dir(root, target))
        if backlog is None:
            raise NexoError(f"{target} ainda não tem backlog aprovado")
        points = sum(s.points or 1 for s in backlog.stories)
    done = [f for f in story_facts(ctx) if f.state == "Concluido" and f.points]
    prior = float((config.resolved(ctx).values.get("estimate") or {}).get("prior_brl_per_point", 15.0))
    est: Estimate = estimate(
        points,
        [f.cost_brl / f.points for f in done],
        [f.cycle_hours / f.points for f in done if f.cycle_hours is not None],
        prior,
    )
    ctx.audit("finops.estimate", {"target": target, "points": points, "brl": est.brl, "samples": est.samples})
    return est.__dict__


def calibrate(ctx: AppContext) -> dict[str, Any]:
    """Compara notas do portão com as decisões humanas e propõe limiar (aprovação no nexo-brain)."""
    approved: list[float] = []
    rejected: list[float] = []
    scores: dict[str, float] = {}
    for e in ctx.events.recent(limit=20000):
        if e.type != "gate.decision":
            continue
        story = str(e.payload.get("story", ""))
        if "score" in e.payload and e.payload.get("decision") in ("human", "auto"):
            scores[story] = float(e.payload["score"])
        elif e.payload.get("decision") == "approved" and story in scores:
            approved.append(scores[story])
        elif e.payload.get("decision") == "rejected" and story in scores:
            rejected.append(scores[story])
    current = float(((config.resolved(ctx).values.get("gates") or {}).get("thresholds") or {}).get("code", 0.95))
    samples = len(approved) + len(rejected)
    if samples < 10:
        proposal = current
        note = f"histórico insuficiente ({samples} decisões humanas; mínimo 10) — manter {current}"
    else:
        proposal = round(max([*rejected, 0.0]) + 0.01, 2) if rejected else round(min(approved) - 0.01, 2)
        proposal = min(0.99, max(0.8, proposal))
        note = "limiar mínimo que não teria aprovado automaticamente nenhuma entrega reprovada por humano"
    result = {
        "samples": samples,
        "approved": len(approved),
        "rejected": len(rejected),
        "current": current,
        "proposal": proposal,
        "note": note,
    }
    ctx.audit("gates.calibration", result)
    return result


def blocked_items(ctx: AppContext) -> list[tuple[str, str]]:
    """Tarefas/histórias bloqueadas cuja condição já passou (cota renovada, mês virou ou limite aumentado)."""
    root = ctx.project_root
    if root is None:
        return []
    scope = project_key(root)
    import time

    out = []
    for row in ctx.conn.execute("SELECT id, type FROM work_items WHERE project = ? AND state = 'Bloqueado'", (scope,)):
        run = ctx.conn.execute(
            "SELECT blocked_until, reason FROM runs WHERE project = ? AND target_id = ?"
            " ORDER BY started_at DESC LIMIT 1",
            (scope, row["id"]),
        ).fetchone()
        if run is None:
            continue
        until = run["blocked_until"]
        reason = str(run["reason"] or "")
        budget_ok = "orçamento" in reason and any(
            costs.budget_status(ctx).tool_ok(t) for t in ("claude", "copilot", "devin")
        )
        if (until and until <= time.time()) or budget_ok:
            out.append((str(row["id"]), str(row["type"])))
    return out


def baseline(ctx: AppContext, since: str, until: str) -> dict[str, Any]:
    """I-16: mede no tracker as histórias fechadas antes do piloto (lead/cycle time, vazão, bugs) e guarda como
    linha de base do projeto; com telemetria ligada, envia ao central para a comparação no dashboard."""
    from dataclasses import asdict
    from datetime import date

    from nexo.kernel.finops.metrics import baseline_summary
    from nexo.kernel.platform import telemetry
    from nexo.modules.demands.app import tracker as integrations_tracker

    try:
        if date.fromisoformat(since) >= date.fromisoformat(until):
            raise NexoError("período vazio: --since precisa ser anterior a --until")
    except ValueError as exc:
        raise NexoError("datas da linha de base no formato AAAA-MM-DD") from exc
    root = ctx.project_root
    if root is None:
        raise NexoError("nenhum projeto Nexo encontrado")
    tracker = integrations_tracker.build_tracker(ctx)
    if tracker is None:
        raise NexoError("linha de base vem do tracker: configure `tracker` no projeto (nexo tracker check)")
    items = [asdict(i) for i in tracker.history(since, until)]
    summary = {**baseline_summary(items, since, until), "tracker": tracker.name}
    out = root / ".nexo" / "metrics" / "baseline.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    ctx.audit("metrics.baseline", {"since": since, "until": until, "stories": summary["stories"]})
    summary["sent"] = False
    if telemetry.enabled(ctx):
        try:
            telemetry.send_baseline(ctx, summary)
            summary["sent"] = True
        except NexoError as exc:
            summary["send_error"] = exc.message
    return summary
