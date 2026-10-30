"""Pacote de evidências por release: um dossiê verificável da feature para auditoria e regulador.

`nexo audit evidence build F-n` monta uma pasta com:
- `index.md`: relatório legível (requisitos e quem aprovou, histórias, portões, testes, segurança, PRs, custo,
  rastreabilidade e integridade da trilha);
- `dados.json`: o mesmo conteúdo estruturado;
- `documentos/`: SDD, requisitos, design técnico, discovery, ADRs e a matriz de rastreabilidade da feature;
- `qualidade/<história>/`: relatórios do pipeline de qualidade (testes, cobertura, SARIF, SBOM…);
- `trilha.jsonl`: eventos de auditoria da feature, das histórias e das tarefas, com `prev_hash` e `hash`;
- `.evidencia/manifest.json`: hash SHA-256 de cada arquivo, assinado quando há chave (mesma assinatura do cérebro).

`nexo audit evidence verify <pasta>` confere hashes e assinatura. Nada é enviado para fora da máquina.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexo.kernel import brain as brain_adapter
from nexo.kernel import services
from nexo.kernel.context import AppContext
from nexo.kernel.finops.money import brl
from nexo.kernel.platform import config, flags
from nexo.kernel.runtime import yamlfile
from nexo.kernel.workspace import specs

ready = services.load("planning.ready")

META_DIR = ".evidencia"
DOCS = (
    "spec.md",
    "requisitos.md",
    "requisito-tecnico.md",
    "analise-tecnica.md",
    "design-tecnico.md",
    "feature.md",
    "epic.md",
    "discovery.md",
)
DATA = ("spec.yaml", "roles.yaml", "traceability.yaml", "backlog.yaml", "discovery.yaml", "security.yaml")
QUALITY_SUFFIXES = (".json", ".xml", ".sarif", ".txt", ".html")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _events_for(ctx: AppContext, refs: set[str]) -> list[dict[str, Any]]:
    out = []
    marks = ",".join("?" for _ in refs)
    rows = ctx.conn.execute(
        f"SELECT seq, at, actor, type, ref, payload, prev_hash, hash FROM events WHERE ref IN ({marks}) ORDER BY seq",  # noqa: S608
        tuple(sorted(refs)),
    )
    for r in rows:
        out.append(
            {
                "seq": r["seq"],
                "at": r["at"],
                "actor": r["actor"],
                "type": r["type"],
                "ref": r["ref"],
                "payload": json.loads(r["payload"]),
                "prev_hash": r["prev_hash"],
                "hash": r["hash"],
            }
        )
    return out


def _stories(ctx: AppContext, planner: Any, fid: str, folder: Path) -> list[dict[str, Any]]:
    from nexo.kernel import services

    RunOptions = services.get("execution.run", "RunOptions")
    from nexo.kernel import services

    StoryRunner = services.get("execution.story", "StoryRunner")

    backlog = specs.load_backlog(folder)
    if backlog is None:
        return []
    runner = StoryRunner(ctx, RunOptions())
    stories = []
    for story in backlog.stories:
        run = runner.story_run(story.id)
        state = runner._load_state(run.id) if run is not None else {}
        tasks = [t.id for t in backlog.tasks if t.story == story.id]
        stories.append(
            {
                "id": story.id,
                "title": story.title,
                "points": story.points,
                "state": planner.store.item_state(story.id),
                "acceptance": list(story.ac_refs),
                "tasks": tasks,
                "run": run.id if run is not None else None,
                "branch": state.get("branch"),
                "pr": state.get("pr"),
                "attempts": state.get("attempts"),
            }
        )
    return stories


def _cost(ctx: AppContext, refs: set[str]) -> float:
    """Custo de uso (R$) dos passos da feature: histórias, tarefas e seus derivados (QA, correções)."""
    from nexo.kernel import services
    from nexo.kernel.finops.costs import usage_cost_brl

    costs = services.load("finops.costs")

    pricing = costs.pricing(ctx)
    total = 0.0
    for line in costs.usage_lines(ctx):
        if line.target in refs or any(line.target.startswith(r + "-") for r in refs):
            try:
                total += usage_cost_brl(line, pricing)
            except ValueError:
                continue
    return total


def _gates(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for e in events:
        if e["type"] != "gate.decision":
            continue
        p = e["payload"]
        out.append(
            {
                "at": e["at"],
                "ref": e["ref"],
                "gate": p.get("gate"),
                "decision": p.get("decision"),
                "approver": p.get("approver") or e["actor"],
                "verified": p.get("approver_verified", p.get("verified")),
                "reason": p.get("reason", ""),
            }
        )
    return out


def _quality(run_dir: Path) -> dict[str, Any]:
    """Resumo do último relatório de qualidade da história: status por etapa, cobertura e achados."""
    report = run_dir / "quality-report.json"
    if not report.is_file():
        return {}
    data = json.loads(report.read_text(encoding="utf-8"))
    stages = [
        {
            "id": st.get("id"),
            "kind": st.get("kind"),
            "status": st.get("status"),
            "coverage": st.get("coverage"),
            "findings": len(st.get("findings") or []),
        }
        for st in data.get("stages") or []
        if isinstance(st, dict)
    ]
    passed = bool(stages) and all(st["status"] in ("passed", "skipped") for st in stages)
    return {"passed": passed, "commit": data.get("commit", ""), "stages": stages}


def build(ctx: AppContext, fid: str, out: Path | None = None, key: str | None = None) -> dict[str, Any]:
    flags.require(ctx, "evidence")
    from nexo.kernel import services

    Planner = services.get("planning.plan", "Planner")

    planner = Planner(ctx)
    folder = specs.feature_dir(planner.root, fid)
    spec = specs.load_spec(folder)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    base_dir = out or planner.root / ".nexo" / "evidencias"
    target, n = base_dir / f"{fid}-{stamp}", 1
    while target.exists():  # dois pacotes no mesmo segundo: sufixo em vez de erro
        n += 1
        target = base_dir / f"{fid}-{stamp}-{n}"
    docs_dir = target / "documentos"
    docs_dir.mkdir(parents=True)
    copied = []
    for name in (*DOCS, *DATA):
        src = folder / name
        if src.is_file():
            shutil.copy2(src, docs_dir / name)
            copied.append(name)
    if (folder / "adr").is_dir():
        shutil.copytree(folder / "adr", docs_dir / "adr")
        copied.append("adr/")
    for sub in ("refinamento", "perf"):  # Definition of Ready e resultados do teste de performance
        if (folder / sub).is_dir():
            shutil.copytree(folder / sub, docs_dir / sub)
            copied.append(f"{sub}/")
    stories = _stories(ctx, planner, fid, folder)
    refs = {fid, *(s["id"] for s in stories), *(t for s in stories for t in s["tasks"])}
    events = _events_for(ctx, refs)
    with (target / "trilha.jsonl").open("w", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n")
    for s in stories:
        if not s["run"]:
            continue
        run_dir = planner.root / ".nexo" / "runs" / s["run"]
        s["quality"] = _quality(run_dir)
        dest = target / "qualidade" / s["id"]
        for f in sorted(run_dir.rglob("*")) if run_dir.is_dir() else []:
            if f.is_file() and f.suffix in QUALITY_SUFFIXES and f.stat().st_size < 20_000_000:
                rel = f.relative_to(run_dir)
                (dest / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest / rel)
    chain = ctx.events.verify()
    roles = yamlfile.load(folder / "roles.yaml") if (folder / "roles.yaml").is_file() else {}
    discovery = yamlfile.load(folder / "discovery.yaml") if (folder / "discovery.yaml").is_file() else {}
    cost = _cost(ctx, refs)
    data: dict[str, Any] = {
        "feature": fid,
        "title": spec.title,
        "generated_at": _now(),
        "generated_by": ctx.actor,
        "state": planner.store.item_state(fid),
        "data_class": spec.data_class,
        "discovery": {k: discovery.get(k) for k in ("decision", "decision_reason", "decided_by") if discovery},
        "approvals": roles.get("approvals", []),
        "sessions": [{k: s.get(k) for k in ("id", "participants", "closed_at")} for s in roles.get("sessions", [])],
        "rejections": roles.get("rejections", []),
        "gates": _gates(events),
        "stories": stories,
        "documents": copied,
        "audit": {
            "chain_ok": chain.ok,
            "checked": chain.checked,
            "events": len(events),
            "first_hash": events[0]["hash"] if events else None,
            "last_hash": events[-1]["hash"] if events else None,
        },
        "cost_brl": round(cost, 2),
        "performance": [ready.perf_status(planner.root, folder, s["id"]) for s in stories],
    }
    (target / "dados.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (target / "index.md").write_text(render(data), encoding="utf-8")
    version = target.name
    key = key or str((config.resolved(ctx).values.get("evidence") or {}).get("sign_key") or "") or None
    if key:
        from nexo.kernel.secrets import keyvault

        brain_adapter.sign(target, key if keyvault.is_ref(key) else Path(key).expanduser(), version, META_DIR)
    else:
        brain_adapter.write_manifest(target, version, META_DIR)
    ctx.audit(
        "evidence.built",
        {"feature": fid, "stories": len(stories), "events": len(events), "signed": bool(key), "chain_ok": chain.ok},
        ref=fid,
    )
    return {"folder": str(target), "signed": bool(key), **{k: data[k] for k in ("feature", "audit")}}


def _yes(value: Any) -> str:
    return "sim" if value else "não"


def render(d: dict[str, Any]) -> str:
    """Relatório legível do pacote (pt-BR)."""
    lines = [
        f"# Evidências da entrega {d['feature']} — {d['title']}",
        "",
        f"Gerado em {d['generated_at']} por {d['generated_by']} · estado da feature: **{d['state'] or '—'}**"
        f" · classificação de dados: {d['data_class'] or '—'}",
        "",
        "## Integridade",
        "",
        f"- Trilha de auditoria íntegra: **{_yes(d['audit']['chain_ok'])}** "
        f"({d['audit']['checked']} eventos verificados na cadeia; {d['audit']['events']} desta entrega em"
        " `trilha.jsonl`).",
        f"- Primeiro hash: `{d['audit']['first_hash']}` · último hash: `{d['audit']['last_hash']}`",
        "- Hash de cada arquivo do pacote em `.evidencia/manifest.json` (assinado quando gerado com chave);"
        " confira com `nexo audit evidence verify <pasta>`.",
        "",
    ]
    if d["discovery"]:
        disc = d["discovery"]
        lines += [
            "## Discovery",
            "",
            f"Decisão: **{disc.get('decision')}** por {disc.get('decided_by')} — {disc.get('decision_reason')}",
            "",
        ]
    lines += ["## Aprovações do pacote (fluxo por papéis)", ""]
    if d["approvals"]:
        lines += ["| Parte | Aprovado por | Identidade verificada | Quando | Hash da parte |", "|---|---|---|---|---|"]
        lines += [
            f"| {a.get('part')} | {a.get('by')} | {_yes(a.get('verified'))} | {a.get('at')} | `{a.get('digest')}` |"
            for a in d["approvals"]
        ]
    else:
        lines.append("Fluxo por papéis não usado nesta feature; a aprovação da spec está nos portões abaixo.")
    if d["sessions"]:
        lines += ["", "Sessões conjuntas:"]
        for s in d["sessions"]:
            who = "; ".join(f"{k}: {', '.join(v)}" for k, v in (s.get("participants") or {}).items() if v)
            lines.append(f"- {s.get('id')} ({who}) encerrada em {s.get('closed_at') or '—'}")
    if d["rejections"]:
        lines += ["", "Rejeições e retrabalho:"]
        lines += [f"- {r.get('part')} por {r.get('by')} em {r.get('at')}: {r.get('reason')}" for r in d["rejections"]]
    lines += ["", "## Portões e decisões", ""]
    if d["gates"]:
        lines += ["| Item | Portão | Decisão | Por | Verificado | Motivo |", "|---|---|---|---|---|---|"]
        lines += [
            f"| {g['ref']} | {g['gate']} | {g['decision']} | {g['approver']} | {_yes(g['verified'])} | {g['reason']} |"
            for g in d["gates"]
        ]
    else:
        lines.append("Nenhuma decisão de portão registrada.")
    lines += ["", "## Histórias", ""]
    if d["stories"]:
        lines += [
            "| História | Estado | Pontos | Critérios | Tentativas | Qualidade | PR |",
            "|---|---|---|---|---|---|---|",
        ]
        for s in d["stories"]:
            q = s.get("quality") or {}
            passed = q.get("passed")
            quality = "—" if not q else ("aprovada" if passed else "reprovada" if passed is False else "ver relatório")
            lines.append(
                f"| {s['id']} — {s['title']} | {s['state'] or '—'} | {s['points']} | {', '.join(s['acceptance'])}"
                f" | {s.get('attempts') or '—'} | {quality} | {s.get('pr') or '—'} |"
            )
    else:
        lines.append("A feature ainda não tem backlog aprovado.")
    perf = d.get("performance") or []
    if perf:
        lines += ["", "## Performance (teste de carga)", ""]
        lines += ["| História | Exigido | Situação | Medido (p95 / p99 / erro) | Limites |", "|---|---|---|---|---|"]
        for p in perf:
            m, lim = p.get("measured") or {}, p.get("limits") or {}
            measured = f"{m.get('p95_ms')} ms / {m.get('p99_ms')} ms / {m.get('erro_max_pct')}%" if m else "—"
            limits = f"{lim.get('p95_ms')} ms / {lim.get('p99_ms')} ms / {lim.get('erro_max_pct')}%" if lim else "—"
            status = p.get("status", "—") + (f" ({p['why']})" if p.get("why") else "")
            lines.append(f"| {p['story']} | {_yes(p.get('required'))} | {status} | {measured} | {limits} |")
    lines += [
        "",
        "## Custo",
        "",
        f"Custo de uso medido dos passos desta entrega: **{brl(d['cost_brl'])}**.",
        "",
        "## Documentos",
        "",
        *[f"- `documentos/{name}`" for name in d["documents"]],
        "",
        "Relatórios de qualidade de cada história (testes, cobertura, SAST, SBOM…) em `qualidade/<história>/`.",
        "",
    ]
    return "\n".join(lines)


def verify(ctx: AppContext, folder: Path) -> dict[str, Any]:
    """Confere hashes (sempre) e assinatura (quando houver) contra `brain.trusted_keys` e `evidence.trusted_keys`
    (este só da org ou do squad)."""
    from nexo.kernel.config.cascade import resolve

    cfg = config.resolved(ctx).values
    # chaves de evidência só da org/squad: o dev não passa a confiar na própria chave pela config pessoal
    layers = [layer for layer in config.load_layers(ctx) if layer.level in ("default", "org", "squad")]
    org_evidence = dict(resolve(layers).values.get("evidence") or {})
    trusted = [
        *[str(k) for k in (cfg.get("brain") or {}).get("trusted_keys") or []],
        *[str(k) for k in org_evidence.get("trusted_keys") or []],
    ]
    result = brain_adapter.check_manifest(folder, trusted, META_DIR)
    ctx.audit("evidence.verified", {"folder": folder.name, "ok": result["ok"], "signed": result["signed"]})
    return result


def audit_export(
    ctx: AppContext, ref: str | None = None, since: str | None = None, until: str | None = None
) -> list[dict[str, Any]]:
    """Trilha de auditoria com os hashes da cadeia: de um item (e seus derivados) ou de um período (AAAA-MM-DD)."""
    sql = "SELECT seq, at, actor, type, ref, payload, prev_hash, hash FROM events WHERE 1 = 1"
    params: list[Any] = []
    if ref:
        sql += " AND (ref = ? OR ref LIKE ?)"
        params += [ref, f"{ref}-%"]
    if since:
        sql += " AND at >= ?"
        params.append(since)
    if until:
        sql += " AND at < ?"
        params.append(until + "T99")  # inclui o dia inteiro
    rows = ctx.conn.execute(sql + " ORDER BY seq", params).fetchall()
    ctx.audit("audit.exported", {"ref": ref or "", "since": since or "", "until": until or "", "events": len(rows)})
    return [
        {
            "seq": r["seq"],
            "at": r["at"],
            "actor": r["actor"],
            "type": r["type"],
            "ref": r["ref"],
            "payload": json.loads(r["payload"]),
            "prev_hash": r["prev_hash"],
            "hash": r["hash"],
        }
        for r in rows
    ]
