"""Aprendizado contínuo: coleta, curadoria de lições, mudança de padrão e aprendizado do roteador."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.platform import config
from nexo.kernel.secrets import credentials
from nexo.kernel.security.redaction import redact
from nexo.modules.execution.app import llm
from nexo.modules.knowledge.adapters import learning_sources
from nexo.modules.knowledge.app import brain, content
from nexo.modules.knowledge.domain.learning.model import (
    LESSON_TYPES,
    Lesson,
    RawEvent,
    RouteStats,
    adoptions,
    dedup_lessons,
    posterior,
)
from nexo.store.runs import now_iso, project_key

LESSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "lessons": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": list(LESSON_TYPES)},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                    "proposed_target": {"type": "string"},
                },
                "required": ["type", "title", "body", "evidence_refs"],
            },
        }
    },
    "required": ["lessons"],
}


def _root(ctx: AppContext) -> Path:
    if ctx.project_root is None:
        raise NexoError("nenhum projeto Nexo encontrado")
    return ctx.project_root


def _cfg(ctx: AppContext) -> dict[str, Any]:
    return dict(config.resolved(ctx).values)


# ---------------- coleta (7.1)


def _store(ctx: AppContext, scope: str, events: list[RawEvent]) -> int:
    new = 0
    for ev in events:
        cur = ctx.conn.execute(
            "INSERT OR IGNORE INTO learning_events(project, ref, source, text, meta, collected_at)"
            " VALUES (?,?,?,?,?,?)",
            (
                scope,
                ev.ref,
                ev.source,
                redact(ev.text),
                json.dumps(ev.meta, ensure_ascii=False, default=str),
                now_iso(),
            ),
        )
        new += cur.rowcount
    return new


def collect(ctx: AppContext, sources: list[str] | None = None) -> dict[str, Any]:
    root = _root(ctx)
    scope = project_key(root)
    cfg = _cfg(ctx)
    wanted = sources or ["pr_comments", "bugs", "human_fixes", "adrs", "incidents", "routing"]
    result: dict[str, Any] = {}
    for source in wanted:
        try:
            events = _collect_source(ctx, root, cfg, source)
        except NexoError as exc:
            result[source] = {"error": exc.message}
            ctx.audit("learning.collect_error", {"source": source, "error": exc.message})
            continue
        result[source] = {"found": len(events), "new": _store(ctx, scope, events)}
    ctx.audit("learning.collected", {k: v for k, v in result.items()})
    return result


def _collect_source(ctx: AppContext, root: Path, cfg: dict[str, Any], source: str) -> list[RawEvent]:
    lcfg = dict((cfg.get("learning") or {}).get("sources") or {})
    if source == "bugs":
        return learning_sources.local_bugs(root)
    if source == "human_fixes":
        return learning_sources.human_fixes(root)
    if source == "adrs":
        return learning_sources.adrs(root)
    if source == "routing":
        return routing_events(ctx)
    if source == "pr_comments":
        repo = str((cfg.get("scm") or {}).get("repo") or "")
        from nexo.modules.demands.app.tracker import github_token

        gh_token = github_token(ctx, required=False)
        if not repo or not gh_token:
            return []
        since = ctx.conn.execute(
            "SELECT MAX(json_extract(meta, '$.at')) FROM learning_events WHERE project = ? AND source = 'pr_comments'",
            (project_key(root),),
        ).fetchone()[0]
        return learning_sources.pr_comments(repo, gh_token, since)
    if source == "incidents":
        events: list[RawEvent] = []
        conf = dict(lcfg.get("confluence") or {})
        if conf.get("base_url"):
            token = credentials.get_secret("confluence")
            if not token:
                raise NexoError("credencial 'confluence' ausente: nexo login confluence")
            events += learning_sources.confluence(
                str(conf["base_url"]), str(conf.get("email", "")), token, str(conf.get("cql", "label = postmortem"))
            )
        snow = dict(lcfg.get("servicenow") or {})
        if snow.get("instance"):
            token = credentials.get_secret("servicenow")
            if not token:
                raise NexoError("credencial 'servicenow' ausente: nexo login servicenow")
            events += learning_sources.servicenow(
                str(snow["instance"]),
                str(snow.get("user", "")),
                token,
                str(snow.get("query", "state=7^ORDERBYDESCsys_updated_on")),
            )
        return events
    raise NexoError(f"fonte desconhecida: {source}")


# ---------------- curadoria (7.2)


def existing_lessons(ctx: AppContext) -> list[str]:
    texts = [f"{r['title']}\n{r['body']}" for r in ctx.conn.execute("SELECT title, body FROM lessons")]
    lessons_dir = ctx.brain_dir / "lessons"
    if lessons_dir.is_dir():
        texts += [
            p.read_text(encoding="utf-8", errors="replace") for p in lessons_dir.rglob("*.md") if p.name != "README.md"
        ]
    return texts


@dataclass
class ReviewReport:
    events: int = 0
    proposed: list[dict[str, Any]] = field(default_factory=list)
    duplicates: int = 0
    issues: list[str] = field(default_factory=list)


def review(ctx: AppContext, dry_run: bool = False) -> ReviewReport:
    root = _root(ctx)
    scope = project_key(root)
    batch_size = int((_cfg(ctx).get("learning") or {}).get("batch_size", 20))
    rows = ctx.conn.execute(
        "SELECT ref, source, text FROM learning_events WHERE project = ? AND processed_at IS NULL"
        " ORDER BY collected_at LIMIT ?",
        (scope, batch_size),
    ).fetchall()
    report = ReviewReport(events=len(rows))
    if not rows:
        return report
    existing = existing_lessons(ctx)
    from nexo.kernel.security.injection import check

    blocks = []
    for r in rows:  # S-03: comentários e documentos são dados; suspeitos ficam fora da curadoria
        fenced, suspicion = check(f"{r['source']}:{r['ref']}", str(r["text"])[:1500])
        if suspicion is not None:
            ctx.audit("security.injection_suspect", {"source": r["source"], "rules": list(suspicion.rules)})
            report.issues.append(f"{r['ref']}: possível injeção de prompt ({', '.join(suspicion.rules)}) — ignorado")
            continue
        blocks.append(f"### {r['ref']} ({r['source']})\n{fenced}")
    events_text = "\n\n".join(blocks) or "- nenhum evento utilizável"
    prompt = content.prompt(ctx, "curator").format(
        existing="\n".join(f"- {e.splitlines()[0]}" for e in existing if e) or "- nenhuma", events=events_text
    )
    result = llm.call_structured(
        ctx,
        "curator",
        "learning",
        prompt,
        LESSON_SCHEMA,
        root,
        facts={"kind": "docs"},
        data_class="internal",
        profile="economico",
        scope=scope,
    )
    refs = {r["ref"] for r in rows}
    candidates = [
        Lesson(
            str(x["type"]),
            str(x["title"]),
            str(x["body"]),
            [e for e in x.get("evidence_refs") or [] if e in refs],
            str(x.get("proposed_target") or ""),
        )
        for x in result.data.get("lessons") or []
    ]
    candidates = [c for c in candidates if c.evidence_refs]  # RN-91: lição sem evidência não segue
    new, dup = dedup_lessons(candidates, existing)
    report.duplicates = len(dup)
    for lesson in new:
        body = "\n".join(
            [
                f"**Tipo**: {lesson.type} · **Destino sugerido**: `{lesson.proposed_target or '—'}`",
                "",
                redact(lesson.body),
                "",
                "**Evidências**:",
                *[f"- `{e}`" for e in lesson.evidence_refs],
                "",
                "Aprovar = abrir PR no nexo-brain incorporando a lição (RN-91).",
            ]
        )
        issue = (
            None if dry_run else brain.create_issue(ctx, f"Lição: {lesson.title}", body, ["nexo", "licao", lesson.type])
        )
        if not dry_run:
            ctx.conn.execute(
                "INSERT INTO lessons(project, title, type, body, evidence, issue, created_at) VALUES (?,?,?,?,?,?,?)",
                (scope, lesson.title, lesson.type, lesson.body, json.dumps(lesson.evidence_refs), issue, now_iso()),
            )
        report.proposed.append({**lesson.__dict__, "issue": issue})
        if issue:
            report.issues.append(issue)
    if not dry_run:
        ctx.conn.executemany(
            "UPDATE learning_events SET processed_at = ? WHERE project = ? AND ref = ?",
            [(now_iso(), scope, r["ref"]) for r in rows],
        )
    ctx.audit(
        "learning.reviewed", {"events": len(rows), "proposed": len(new), "duplicates": len(dup), "dry_run": dry_run}
    )
    return report


def status(ctx: AppContext) -> dict[str, Any]:
    scope = project_key(_root(ctx))
    rows = ctx.conn.execute(
        "SELECT source, COUNT(*) AS n, SUM(processed_at IS NULL) AS pending FROM learning_events"
        " WHERE project = ? GROUP BY source",
        (scope,),
    ).fetchall()
    lessons = ctx.conn.execute("SELECT COUNT(*) FROM lessons WHERE project = ?", (scope,)).fetchone()[0]
    return {"sources": {r["source"]: {"events": r["n"], "pending": r["pending"]} for r in rows}, "lessons": lessons}


# ---------------- mudança de padrão no código (7.3)


def patterns(ctx: AppContext, min_commits: int = 2) -> list[dict[str, Any]]:
    root = _root(ctx)
    scope = project_key(root)
    found = adoptions(learning_sources.dependency_diffs(root), min_commits)
    out = []
    for a in found:
        ref = f"pattern:{a.ecosystem}:{a.package}"
        seen = ctx.conn.execute("SELECT 1 FROM learning_events WHERE project = ? AND ref = ?", (scope, ref)).fetchone()
        if seen:
            continue
        text = f"Adoção de {a.package} ({a.ecosystem}) em {len(a.commits)} commits: {', '.join(a.commits)}"
        _store(ctx, scope, [RawEvent("patterns", ref, text, {"package": a.package, "commits": list(a.commits)})])
        ctx.conn.execute(
            "UPDATE learning_events SET processed_at = ? WHERE project = ? AND ref = ?", (now_iso(), scope, ref)
        )
        body = (
            f"A biblioteca `{a.package}` ({a.ecosystem}) passou a ser adotada em {len(a.commits)} commits "
            f"({', '.join(a.commits)}).\n\nValidar: padronizar (skill/padrão da stack), avaliar licença e "
            "segurança (`check_dependency`) ou orientar alternativa aprovada."
        )
        issue = brain.create_issue(
            ctx, f"Mudança de padrão: adoção de {a.package}", body, ["nexo", "padrao", a.ecosystem]
        )
        out.append({"package": a.package, "ecosystem": a.ecosystem, "commits": list(a.commits), "issue": issue})
    ctx.audit("learning.patterns", {"new": len(out)})
    return out


# ---------------- roteador que aprende (7.6)


def route_stats(ctx: AppContext) -> dict[str, dict[str, RouteStats]]:
    """Resultados reais por rota × tipo de atividade: passos de dev e execuções de avaliação."""
    stats: dict[str, dict[str, RouteStats]] = {}
    rows = ctx.conn.execute(
        "SELECT s.tool || '/' || s.model AS route, s.outcome, json_extract(r.classification, '$.kind') AS kind"
        " FROM steps s JOIN runs r ON r.id = s.run_id WHERE s.role = 'developer' AND s.outcome IS NOT NULL"
    )
    for row in rows:
        if row["outcome"] in ("rate_limited", "interrupted"):
            continue  # cota e interrupção não medem qualidade da rota
        st = stats.setdefault(str(row["kind"] or "backend"), {}).setdefault(row["route"], RouteStats())
        if row["outcome"] == "success":
            st.successes += 1
        else:
            st.failures += 1
    for row in ctx.conn.execute("SELECT route, kind, success FROM eval_results"):
        st = stats.setdefault(str(row["kind"] or "backend"), {}).setdefault(row["route"], RouteStats())
        if row["success"]:
            st.successes += 1
        else:
            st.failures += 1
    return stats


def observed_for(ctx: AppContext, kind: str | None) -> dict[str, tuple[int, int]]:
    """Histórico usado pelo roteador quando `routing.learning: auto`."""
    if str((_cfg(ctx).get("routing") or {}).get("learning", "propose")) != "auto":
        return {}
    by_kind = route_stats(ctx).get(kind or "backend", {})
    return {route: (s.successes, s.failures) for route, s in by_kind.items()}


def routing_events(ctx: AppContext) -> list[RawEvent]:
    out = []
    for kind, routes in route_stats(ctx).items():
        for route, s in routes.items():
            n = s.successes + s.failures
            out.append(
                RawEvent(
                    "routing",
                    f"routing:{kind}:{route}:{n}",
                    f"Rota {route} em tarefas {kind}: {s.successes} sucesso(s) em {n}",
                    {"route": route, "kind": kind, "successes": s.successes, "failures": s.failures},
                )
            )
    return out


def propose_routing(ctx: AppContext) -> dict[str, Any]:
    """Proposta revisável de ajuste das prioridades de rota a partir do histórico (mudança via PR no cérebro)."""
    from nexo.modules.execution.app.agents import catalog

    cat = catalog(ctx)
    min_samples = int((_cfg(ctx).get("routing") or {}).get("min_samples", 5))
    changes = []
    for kind, routes in route_stats(ctx).items():
        for route_id, s in routes.items():
            route = cat.routes.get(route_id)
            n = s.successes + s.failures
            if route is None or n < min_samples:
                continue
            prior = cat.prior("developer", route.tier)
            post = posterior(prior, s)
            if abs(post - prior) >= 0.1:
                changes.append({"kind": kind, "route": route_id, "prior": prior, "posterior": post, "samples": n})
    issue = None
    if changes:
        lines = [
            f"- `{c['route']}` em `{c['kind']}`: {c['prior']:.2f} → {c['posterior']:.2f} ({c['samples']} amostras)"
            for c in changes
        ]
        issue = brain.create_issue(
            ctx,
            "Roteamento: ajuste de probabilidades pelo histórico",
            "\n".join(
                [
                    "Proposta gerada por `nexo eval report --propose`:",
                    "",
                    *lines,
                    "",
                    "Aprovar = atualizar `routing.yaml` (priors/regras) no nexo-brain.",
                ]
            ),
            ["nexo", "roteamento"],
        )
    ctx.audit("routing.proposal", {"changes": changes, "issue": issue})
    return {"changes": changes, "issue": issue}
