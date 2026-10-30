"""Ciclo de bug: registro, correção, ferramentas por fornecedor e devolução da spec."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from nexo.kernel import services
from nexo.kernel.backlog import BacklogTask
from nexo.kernel.finops import tokens
from nexo.kernel.gates.confidence import ReviewVerdict
from nexo.kernel.quality.model import ERROR, FAILED, PASSED, QualityReport
from nexo.kernel.quality.traceability import AcTrace
from nexo.kernel.runtime import yamlfile
from nexo.kernel.spec import questions as q
from nexo.kernel.workspace import specs
from nexo.modules.execution.app.story import tasks as story_tasks
from nexo.modules.execution.app.story.common import StoryReport
from nexo.store.runs import now_iso

templates = services.load("planning.templates")
feature_for_story = services.get("planning.clarify", "feature_for_story")

if TYPE_CHECKING:
    from nexo.modules.execution.app.story.runner import StoryRunner


def fix_kind(tasks: list[Any]) -> str:
    """Correção é de código: tipo da primeira tarefa de código da história (achado no e2e Java: a primeira era o
    contrato, e a correção que precisava criar o controller saía como `contract`)."""
    code = [t.kind for t in tasks if t.kind not in ("contract", "test", "docs")]
    return code[0] if code else "backend"


def locked_failures(report: QualityReport, locked: dict[str, str]) -> list[str]:
    """Falhas dos testes de aceite travados (QA), com a mensagem: a mesma lista em duas tentativas seguidas
    indica que o problema pode ser o teste, não o código."""
    out: list[str] = []
    for stage in report.stages:
        for t in stage.failed_tests:
            for path in locked:
                dotted = path.rsplit(".", 1)[0].replace("/", ".")
                if (
                    (t.file and t.file.replace("\\", "/").endswith(path))
                    or t.classname.endswith(path)
                    or (t.classname and dotted.endswith(t.classname))
                ):
                    out.append(f"{t.name}: {tokens.condense(t.message, 200)}")
                    break
    return sorted(out)


def bug(
    r: StoryRunner,
    story_id: str,
    run_id: str,
    st: dict[str, Any],
    source: str,
    failures: list[str],
    report: QualityReport,
    traceability: list[AcTrace],
    reviews: list[ReviewVerdict] | None = None,
) -> StoryReport | None:
    if st["attempts"] >= r.max_attempts:
        return r._escalate(story_id, run_id, st, f"{r.max_attempts} tentativas esgotadas: {'; '.join(failures)}")
    disputed = locked_failures(report, dict(st.get("locked") or {}))
    if disputed and st["attempts"] >= 1 and disputed == st.get("last_locked_failures"):
        # A correção não mudou nada nos testes de aceite travados — o valor
        # esperado do teste pode estar errado (o dev não pode alterá-lo). Mais tentativas só gastariam.
        st["locked_dispute"] = True
        return r._escalate(
            story_id,
            run_id,
            st,
            "os testes de aceite travados falharam igual depois da correção; o teste pode estar errado: "
            f"{disputed[0]}. Confira o valor esperado; se o teste estiver errado, corrija-o e rode "
            f"`resume {story_id}` (a correção humana do teste fica registrada na auditoria)",
        )
    st["last_locked_failures"] = disputed
    evidence = {
        # só a linha do erro e os quadros do projeto chegam ao agente, não a pilha inteira
        "failed_tests": [
            f"{t.classname}::{t.name}: {tokens.condense(t.message, 400)}" for s in report.stages for t in s.failed_tests
        ][:20],
        "stages": [f"{s.id}: {s.summary}" for s in report.stages if s.status in (FAILED, ERROR)],
        "findings": [f"{f.file}:{f.line} {f.rule} {f.message}" for s in report.stages for f in s.findings][:20],
        "acceptance": [f"{t.ac}: {t.status}" for t in traceability if t.status != PASSED],
        "reviews": [f"{r.reviewer}: {f.get('message')}" for r in reviews or [] for f in r.findings][:20],
    }
    bug = create(r, story_id, st, source, failures, [], evidence)
    event = "bug"
    r._transition(story_id, event, f"{bug}: {failures[0]}")
    st["pending_bug"] = bug
    st["phase"] = "bugfix"
    return None


def run_bugfix(
    r: StoryRunner, story_id: str, run_id: str, st: dict[str, Any], tasks: list[BacklogTask], worktree: Path
) -> StoryReport | None:
    bug_id = st.get("pending_bug")
    if not bug_id:
        st["phase"] = "quality"
        return None
    st["attempts"] += 1
    attempt = st["attempts"]
    bug = yamlfile.load(r.root / ".nexo" / "bugs" / f"{bug_id}.yaml")
    spec = specs.load_spec(feature_for_story(r.root, story_id))
    fix_id = f"{story_id}-FIX{attempt}"
    lines = [
        f"Corrigir o bug {bug_id} da história {story_id} (tentativa {attempt}/{r.max_attempts}).",
        "",
        "## Falhas",
        *[f"- {f}" for f in bug.get("failures", [])],
    ]
    for key, title in (
        ("failed_tests", "Testes falhando"),
        ("stages", "Etapas"),
        ("findings", "Achados"),
        ("acceptance", "Critérios"),
        ("reviews", "Revisão"),
    ):
        items = (bug.get("evidence") or {}).get(key) or []
        if items:
            lines += ["", f"## {title}", *[f"- {i}" for i in items]]
    lines += ["", "Os testes de aceite estão travados: corrija o código de produção, não os testes."]
    files = sorted({f for t in tasks for f in t.files_hint})
    story_tasks.write(
        r,
        fix_id,
        f"Corrigir {bug_id}",
        fix_kind(tasks),
        "\n".join(lines),
        [f"{t}" for t in (bug.get("evidence") or {}).get("acceptance", [])] or ["pipeline verde"],
        files,
        story_id,
        spec,
    )
    # Escada de escalonamento: 1 = rota padrão; 2 = modelo mais forte; 3 = outra ferramenta.
    profile = "maxima-qualidade" if attempt >= 2 else None
    exclude = frozenset(tools_for_vendors(r, st["vendors"])) if attempt >= 3 else frozenset()
    r.ctx.audit(
        "story.bugfix",
        {
            "story": story_id,
            "bug": bug_id,
            "attempt": attempt,
            "profile": profile,
            "exclude_tools": sorted(exclude),
        },
        ref=story_id,
    )
    rep = story_tasks.run_one(r, fix_id, st, profile=profile, exclude_tools=exclude)
    if rep.state != "Concluido":
        return story_tasks.problem(r, story_id, run_id, st, fix_id, rep)
    problem = story_tasks.merge(r, story_id, run_id, st, worktree, [fix_id])
    if problem is not None:
        return problem
    set_bug(r, bug_id, "Resolvido")
    st["pending_bug"] = None
    r._transition(story_id, "implemented")
    st["phase"] = "quality"
    return None


def tools_for_vendors(r: StoryRunner, vendors: list[str]) -> set[str]:
    from nexo.modules.execution.app.agents import catalog

    cat = catalog(r.ctx)
    return {r.tool for r in cat.routes.values() if r.model_vendor in vendors and r.tool_vendor == r.model_vendor}


def create(
    r: StoryRunner,
    story_id: str,
    st: dict[str, Any],
    source: str,
    failures: list[str],
    files: list[str],
    evidence: dict[str, Any],
) -> str:
    folder = r.root / ".nexo" / "bugs"
    folder.mkdir(parents=True, exist_ok=True)
    n = len(list(folder.glob("B-*.yaml"))) + 1
    bug_id = f"B-{n}"
    yamlfile.dump(
        folder / f"{bug_id}.yaml",
        {
            "id": bug_id,
            "story": story_id,
            "source": source,
            "class": "code",
            "attempt": st["attempts"] + 1,
            "failures": failures,
            "evidence": evidence,
            "state": "Aberto",
            "created_at": now_iso(),
        },
    )
    r.store.upsert_item(
        bug_id,
        "bug",
        f"{story_id}: {failures[0][:80] if failures else source}",
        r.project,
        f"local:{story_id}",
        "Aberto",
    )
    st["bugs"].append(bug_id)
    severity = "alta" if source in ("quality", "review", "human") else "media"
    doc = templates.render(
        r.ctx,
        "bug",
        {
            "id": bug_id,
            "title": failures[0][:120] if failures else source,
            "severity": severity,
            "origin": {"quality": "pipeline", "review": "revisao", "human": "portao-humano"}.get(source, source),
            "class": "codigo",
            "story": story_id,
            "attempt": st["attempts"] + 1,
            "max_attempts": int((r.cfg.get("bug_loop") or {}).get("max_attempts", 3)),
            "failures": failures,
            "steps": [f"nexo quality run --path {', '.join(files)}" if files else "nexo quality run"],
            "evidence": [{"kind": k, "ref": "; ".join(map(str, v))} for k, v in evidence.items() if v]
            or [{"kind": "falha", "ref": f} for f in failures[:3]],
            "impact": f"história {story_id} não pode ser concluída",
        },
        r.root,
    )
    (folder / f"{bug_id}.md").write_text(doc.text, encoding="utf-8")
    description = doc.tracker_description()
    r.sync.ensure(
        bug_id,
        "bug",
        f"{story_id}: {failures[0][:120] if failures else source}",
        "EmDev",
        description=description,
        parent=story_id,
    )
    r.ctx.audit("bug.created", {"bug": bug_id, "story": story_id, "source": source, "failures": failures}, ref=story_id)
    r.ui("bug", {"bug": bug_id, "failures": failures})
    return bug_id


def set_bug(r: StoryRunner, bug_id: str, state: str) -> None:
    path = r.root / ".nexo" / "bugs" / f"{bug_id}.yaml"
    data = yamlfile.load(path)
    data["state"] = state
    yamlfile.dump(path, data)
    r.store.set_item_state(bug_id, state)
    r.sync.transition(bug_id, "Concluido" if state == "Resolvido" else "EmDev")


def spec_return(
    r: StoryRunner, story_id: str, run_id: str, st: dict[str, Any], folder: Path, issue: str
) -> StoryReport:
    st["spec_returns"] += 1
    st["spec_stable"] = False
    if st["spec_returns"] > r.max_spec_returns:
        return r._escalate(story_id, run_id, st, f"especificação ambígua repetidamente: {issue}")
    qs = specs.load_questions(folder)
    qs = q.merge_new(
        qs, [{"text": f"[{story_id}] {issue}", "severity": "blocking", "section": "acceptance", "source": "agent"}]
    )
    specs.save_questions(folder, qs)
    r._transition(story_id, "spec_ambiguity", issue)
    st["phase"] = "quality"
    r._save(run_id, st)
    r._notify(story_id, "especificação precisa de esclarecimento", issue)
    return r._report(story_id, run_id, f"voltou ao refinamento: {issue}")
