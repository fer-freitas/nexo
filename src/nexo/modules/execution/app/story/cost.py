"""Custo da história e da feature: plano, projeção, teto, portão de custo e decisões sobre ele."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from nexo.kernel import services
from nexo.kernel.backlog import BacklogTask
from nexo.kernel.errors import NexoError
from nexo.kernel.finops.money import brl
from nexo.kernel.platform import identity
from nexo.kernel.workspace import specs
from nexo.modules.execution.app.story import decisions as story_decisions
from nexo.modules.execution.app.story.common import StoryReport

approval = services.load("delivery.approval")
feature_for_story = services.get("planning.clarify", "feature_for_story")

if TYPE_CHECKING:
    from nexo.modules.execution.app.story.runner import StoryRunner


def settings(r: StoryRunner) -> dict[str, Any]:
    return dict(((r.cfg.get("budget") or {}).get("story_cap")) or {})


def plan(r: StoryRunner, story_id: str) -> dict[str, Any]:
    """Estimativa (R$ por ponto, histórico ou prior) e teto da história."""
    from nexo.kernel import services

    metrics = services.load("finops.metrics")

    _, _, backlog, _ = r._load(story_id)
    story = next((s for s in backlog.stories if s.id == story_id), None)
    points = int(story.points) if story and story.points else 1
    est = metrics.estimate_feature(r.ctx, None, points)
    cfg = settings(r)
    fixed = cfg.get("fixed_brl")
    cap = (
        float(fixed)
        if fixed is not None
        else max(float(cfg.get("floor_brl", 20.0)), float(est["brl_high"]) * float(cfg.get("factor", 1.5)))
    )
    return {
        "estimate_brl": float(est["brl"]),
        "estimate_high_brl": float(est["brl_high"]),
        "cap_brl": round(cap, 2),
        "cost_brl": 0.0,
    }


def story_cost(r: StoryRunner, story_id: str) -> tuple[float, dict[str, float]]:
    from nexo.kernel import services

    finops_costs = services.load("finops.costs")

    costs = finops_costs.story_costs(r.ctx, story_id)
    return round(sum(costs.values()), 2), costs


def projection(
    r: StoryRunner, st: dict[str, Any], tasks: list[BacklogTask], cost: float, costs: dict[str, float]
) -> float:
    """Custo final previsto: acumulado + tarefas restantes × custo médio das concluídas."""
    ids = [t.id for t in tasks]
    done = [t for t in ids if st["tasks"].get(t) == "Concluido"]
    remaining = len(ids) - len(done)
    if st.get("phase") in ("quality", "bugfix") or remaining <= 0:
        return cost
    spent = [sum(v for k, v in costs.items() if k == t or k.startswith(f"{t}-")) for t in done]
    avg = sum(spent) / len(spent) if spent else float(st.get("estimate_brl") or 0) / max(1, len(ids))
    return round(cost + remaining * avg, 2)


def check(
    r: StoryRunner, story_id: str, run_id: str, st: dict[str, Any], tasks: list[BacklogTask]
) -> StoryReport | None:
    if "cap_brl" not in st:  # Histórias iniciadas antes da
        st.update(plan(r, story_id))
    cost, costs = story_cost(r, story_id)
    cap = float(st["cap_brl"])
    st["cost_brl"] = cost
    st["projection_brl"] = projection(r, st, tasks, cost, costs)
    r.ui("cost", {"story": story_id, "cost": cost, "cap": cap, "projection": st["projection_brl"]})
    alert = float(settings(r).get("alert", 0.8))
    if alert * cap <= cost < cap and st.get("cost_alerted_cap") != cap:
        st["cost_alerted_cap"] = cap
        r.ctx.audit("story.cost_alert", {"story": story_id, "cost_brl": cost, "cap_brl": cap}, ref=story_id)
        r._notify(story_id, f"{alert:.0%} do teto de custo", f"{brl(cost)} de {brl(cap)}")
    if cost >= cap:
        return gate(r, story_id, run_id, st, "story", cost, cap)
    fcap = feature_cap(r, story_id)
    if fcap is not None:
        fcost = feature_cost(r, story_id)
        if fcost >= fcap:
            return gate(r, story_id, run_id, st, "feature", fcost, fcap)
    r._save(run_id, st)
    return None


def feature_budget_file(r: StoryRunner, story_id: str) -> Path:
    folder = feature_for_story(r.root, story_id)
    if folder is None:
        raise NexoError(f"feature de {story_id} não encontrada")
    return cast("Path", folder / "budget.json")


def feature_cap(r: StoryRunner, story_id: str) -> float | None:
    path = feature_budget_file(r, story_id)
    if path.is_file():
        return float(json.loads(path.read_text(encoding="utf-8"))["cap_brl"])
    fixed = (r.cfg.get("budget") or {}).get("feature_cap_brl")
    return float(fixed) if fixed is not None else None


def feature_cost(r: StoryRunner, story_id: str) -> float:
    folder = feature_for_story(r.root, story_id)
    backlog = specs.load_backlog(folder) if folder else None
    stories = [s.id for s in backlog.stories] if backlog else [story_id]
    return round(sum(story_cost(r, s)[0] for s in stories), 2)


def gate_reason(r: StoryRunner, story_id: str, gate: dict[str, Any]) -> str:
    scope = "da feature" if gate["kind"] == "feature" else "da história"
    if gate.get("request"):
        return (
            f"teto de custo {scope} atingido: {brl(gate['cost'])} de {brl(gate['cap'])} — "
            f"aguardando aprovação externa {gate['request']}"
        )
    return (
        f"teto de custo {scope} atingido: {brl(gate['cost'])} de {brl(gate['cap'])} — "
        f"`nexo approve {story_id} --raise <percentual>` ou `--economic`,"
        f" ou `nexo reject {story_id} --reason ...`"
    )


def gate(
    r: StoryRunner, story_id: str, run_id: str, st: dict[str, Any], kind: str, cost: float, cap: float
) -> StoryReport:
    """Portão de custo: pausa sem contar como falha nem tentativa."""
    gate: dict[str, Any] = {"kind": kind, "from": r.store.item_state(story_id), "cost": cost, "cap": cap}
    st["cost_gate"] = gate
    r._save(run_id, st)
    r._transition(story_id, "cost_gate", f"teto de custo {brl(cap)} atingido")
    if approval.mode(r.ctx, "cost") == "external":
        gate["request"] = approval.request(
            r.ctx,
            "cost",
            story_id,
            f"teto de custo atingido: {brl(cost)} de {brl(cap)}",
            {"cost_brl": cost, "cap_brl": cap, "scope": kind},
        )
        r._save(run_id, st)
    r.ctx.audit(
        "gate.decision",
        {"gate": "cost", "story": story_id, "decision": "human", "kind": kind, "cost_brl": cost, "cap_brl": cap},
        ref=story_id,
    )
    reason = gate_reason(r, story_id, gate)
    r._notify(story_id, "teto de custo atingido", reason)
    return r._report(story_id, run_id, reason)


def approve(
    r: StoryRunner, story_id: str, run_id: str, st: dict[str, Any], raise_pct: float | None, economic: bool
) -> StoryReport:
    gate = st["cost_gate"]
    cfg = settings(r)
    if raise_pct is None and not economic:
        raise NexoError("portão de custo: use --raise <percentual> ou --economic")
    pct = float(raise_pct) if raise_pct is not None else float(cfg.get("economic_raise_pct", 25.0))
    if pct <= 0:
        raise NexoError("--raise deve ser um percentual positivo")
    new = round(float(gate["cap"]) * (1 + pct / 100), 2)
    limit = float(cfg.get("self_raise_factor", 2.0)) * float(st.get("estimate_brl") or 0)
    if gate["kind"] == "feature" or new > limit:
        approver = story_decisions.check_segregation(r, st)  # RF-134: acima de 2× a estimativa, outra pessoa aprova
    else:
        approver = identity.current(r.ctx)
    return raise_cap(r, story_id, run_id, st, pct, economic, approver.id, approver.verified)


def raise_cap(
    r: StoryRunner, story_id: str, run_id: str, st: dict[str, Any], pct: float, economic: bool, by: str, verified: bool
) -> StoryReport:
    gate = st["cost_gate"]
    old = float(gate["cap"])
    new = round(old * (1 + pct / 100), 2)
    if gate["kind"] == "feature":
        feature_budget_file(r, story_id).write_text(json.dumps({"cap_brl": new}), encoding="utf-8")
    else:
        st["cap_brl"] = new
    if economic:
        st["profile_override"] = "economico"
    event = {"EmDev": "cost_resume_dev", "EmQA": "cost_resume_qa", "EmReview": "cost_resume_review"}[gate["from"]]
    del st["cost_gate"]
    r._save(run_id, st)
    r._transition(story_id, event, f"teto elevado para {brl(new)}")
    r.ctx.audit(
        "gate.decision",
        {
            "gate": "cost",
            "story": story_id,
            "decision": "approved",
            "kind": gate["kind"],
            "approver": by,
            "approver_verified": verified,
            "old_cap_brl": old,
            "new_cap_brl": new,
            "economic": economic,
        },
        ref=story_id,
    )
    return r._drive(story_id, run_id)


def reject(r: StoryRunner, story_id: str, run_id: str, st: dict[str, Any], reason: str, by: str) -> StoryReport:
    if len(reason.strip()) < 5:
        raise NexoError("--reason é obrigatório para reprovar")
    gate = st.pop("cost_gate")
    r._save(run_id, st)
    r._transition(story_id, "cost_reject", reason)
    r.ctx.audit(
        "gate.decision",
        {
            "gate": "cost",
            "story": story_id,
            "decision": "rejected",
            "kind": gate["kind"],
            "approver": by,
            "reason": reason,
            "cost_brl": gate["cost"],
            "cap_brl": gate["cap"],
        },
        ref=story_id,
    )
    r.store.update_run(run_id, state="Escalado", reason=f"interrompida por custo: {reason}")
    return r._report(story_id, run_id, f"interrompida por custo: {reason}")
