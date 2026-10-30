"""Reação depois do PR e merge automático no nível L3.

Para cada história entregue com PR aberto:
- CI com falha no commit atual → bug e ciclo de correção (conta nas N tentativas);
- revisão humana "changes requested" nova → idem, com o texto da revisão cercado como dado; texto suspeito de
  injeção não dispara correção automática, vai para humano;
- autonomia L3, CI todo verde e PR aprovado/sem bloqueio → merge (squash) do commit avaliado.
Roda no `nexo watch` e em `nexo pr sync`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError


def _state(root: Path, run_id: str) -> tuple[Path, dict[str, Any]]:
    path = root / ".nexo" / "runs" / run_id / "story.json"
    return path, (json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {})


def _merge_held(ctx: AppContext, story_id: str) -> list[str]:
    """Premissa provisória aberta ou spec desatualizada seguram o merge, mesmo com todo o resto verde."""
    from nexo.kernel import services

    if not services.available("demands.proposals"):
        return []
    try:
        return list(services.load("demands.proposals").lock_current(ctx, story_id))
    except NexoError:
        return []


def _report_status(ctx: AppContext, scm: Any, story_id: str, sha: str) -> None:
    """Sinal `nexo/gate` no commit, pela API, sem Actions. Opcional: é um sinal, não uma prova."""
    from nexo.kernel.platform import flags

    if not (sha and hasattr(scm, "set_status") and flags.enabled(ctx, "commit_status")):
        return
    held = _merge_held(ctx, story_id)
    state, text = ("failure", "; ".join(held)[:140]) if held else ("success", "portões do Nexo aprovados")
    try:
        scm.set_status(sha, state, text)
    except (NexoError, OSError) as exc:
        ctx.audit("scm.error", {"story": story_id, "error": str(exc)[:200]}, ref=story_id)


def sync(ctx: AppContext) -> list[dict[str, Any]]:
    from nexo.kernel import services
    from nexo.kernel.platform import flags
    from nexo.kernel.security.injection import check
    from nexo.kernel.workflow.autonomy import merges

    autonomy = services.load("execution.autonomy")
    from nexo.kernel import services

    RunOptions = services.get("execution.run", "RunOptions")
    from nexo.kernel import services

    StoryRunner = services.get("execution.story", "StoryRunner")
    from nexo.store.runs import RunStore, project_key

    root = ctx.project_root
    if root is None:
        raise NexoError("nenhum projeto Nexo encontrado")
    if not flags.enabled(ctx, "pr_reaction"):  # o watch também passa por aqui
        return []
    from nexo.modules.delivery.app import scm as delivery_scm

    if not delivery_scm.enabled(ctx):
        return []
    scm = delivery_scm.build(ctx)
    store = RunStore(ctx.conn, project_key(root))
    level = autonomy.level(ctx)
    actions: list[dict[str, Any]] = []
    for item in store.items():
        story_id = str(item["id"])
        if not story_id.startswith("US-") or item["state"] not in ("Concluido", "AguardandoHumano"):
            continue
        story_run = StoryRunner(ctx, RunOptions()).story_run(story_id)
        if story_run is None:
            continue
        run_id = story_run.id
        path, st = _state(root, run_id)
        url = str(st.get("pr") or "")
        if not url or st.get("pr_state") in ("merged", "closed"):
            continue
        number = int(url.rstrip("/").rsplit("/", 1)[1])
        pr = scm.pull(number)
        if pr.get("merged"):
            st["pr_state"] = "merged"
            path.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
            ctx.audit("scm.merged", {"story": story_id, "pr": url}, ref=story_id)
            actions.append({"story": story_id, "action": "merged_externally"})
            continue
        if pr.get("state") == "closed":
            st["pr_state"] = "closed"
            path.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
            continue
        sha = str((pr.get("head") or {}).get("sha") or "")
        runner = StoryRunner(ctx, RunOptions())
        blocked: list[str] = getattr(scm, "blocked_checks", lambda _sha: [])(sha) if sha else []
        if blocked and st.get("ci_blocked") != sha:  # CI nem rodou (infraestrutura): avisa, não abre correção
            st["ci_blocked"] = sha
            path.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
            ctx.audit("scm.ci_blocked", {"story": story_id, "pr": url, "checks": blocked}, ref=story_id)
            from nexo.kernel import services

            notify = services.get("notify.main", "notify")

            notify(
                ctx,
                "gate",
                f"{story_id}: CI do PR não rodou",
                f"Checks sem execução: {', '.join(blocked)}",
                ref=story_id,
            )
            actions.append({"story": story_id, "action": "ci_blocked", "checks": blocked})
        failed = scm.failed_checks(sha) if sha else []
        if failed and st.get("ci_handled") != sha:
            st["ci_handled"] = sha
            path.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
            report = runner.pr_feedback(story_id, "ci", [f"CI falhou no PR: {', '.join(failed)}"], f"ci:{scm.provider}")
            actions.append({"story": story_id, "action": "ci_fix", "state": report.state, "reason": report.reason})
            continue
        handled = set(st.get("reviews_handled") or [])
        changes = [
            r
            for r in scm.reviews(number)
            if r.get("state") == "CHANGES_REQUESTED"
            and str(r.get("id")) not in handled
            and (r.get("user") or {}).get("type") != "Bot"
        ]
        if changes:
            review = changes[-1]
            st["reviews_handled"] = sorted(handled | {str(r.get("id")) for r in changes})
            path.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
            who = f"{scm.provider}:{(review.get('user') or {}).get('login', '?')}"
            fenced, suspicion = check(f"revisão:{who}", str(review.get("body") or "pedido de mudança sem texto"))
            if suspicion is not None:
                ctx.audit(
                    "security.injection_suspect", {"source": "pr_review", "rules": list(suspicion.rules)}, ref=story_id
                )
                actions.append({"story": story_id, "action": "review_needs_human"})
                continue
            report = runner.pr_feedback(story_id, "review", [f"pedido de mudança no PR:\n{fenced}"], who)
            actions.append({"story": story_id, "action": "review_fix", "state": report.state, "reason": report.reason})
            continue
        _report_status(ctx, scm, story_id, sha)
        held = [*_merge_held(ctx, story_id), *(st.get("lineage_held") or [])]
        if held:
            actions.append({"story": story_id, "action": "merge_held", "reasons": held})
        if not held and merges(level) and item["state"] == "Concluido" and sha and flags.enabled(ctx, "auto_merge"):
            counts = scm.checks(sha)
            pending = sum(v for k, v in counts.items() if k not in ("success", "skipped", "neutral"))
            if counts.get("success") and not pending and pr.get("mergeable") is not False:
                result = scm.merge(number, sha, f"{story_id}: {pr.get('title', '')}")
                if result.get("merged"):
                    st["pr_state"] = "merged"
                    path.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
                    ctx.audit("scm.merged", {"story": story_id, "pr": url, "autonomy": level, "sha": sha}, ref=story_id)
                    actions.append({"story": story_id, "action": "merged"})
    return actions
