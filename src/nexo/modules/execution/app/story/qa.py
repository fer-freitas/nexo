"""Fase de QA test-first e testes de caracterização do código existente."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from nexo.kernel import services
from nexo.kernel.backlog import BacklogTask
from nexo.kernel.quality.model import ERROR, FAILED, TestCase
from nexo.kernel.quality.traceability import ac_ids_in
from nexo.kernel.spec.gherkin import render_feature
from nexo.kernel.spec.model import Spec
from nexo.kernel.workspace import git, specs
from nexo.modules.execution.app.run import GIT_LOCK
from nexo.modules.execution.app.story import tasks as story_tasks
from nexo.modules.execution.app.story.common import StoryReport, is_test_file, is_test_task, sha256_file

feature_for_story = services.get("planning.clarify", "feature_for_story")
run_pipeline = services.get("quality.runner", "run_pipeline")

if TYPE_CHECKING:
    from nexo.modules.execution.app.story.runner import StoryRunner


def run_qa(
    r: StoryRunner, story_id: str, run_id: str, st: dict[str, Any], spec: Spec, tasks: list[BacklogTask], worktree: Path
) -> StoryReport | None:
    folder = feature_for_story(r.root, story_id)
    backlog = specs.load_backlog(folder) if folder else None
    story = next(s for s in backlog.stories if s.id == story_id) if backlog else None
    acs = [a for a in spec.acceptance if story and a.id in story.ac_refs]
    qa_id = f"{story_id}-QA{st['qa_attempts'] + 1 if st['qa_attempts'] else ''}"
    feedback = st.pop("qa_feedback", "")
    test_tasks = [t for t in tasks if is_test_task(t)]
    planned = "".join(
        f"\n### {t.id} — {t.title}\n{t.description}\n"
        + "".join(f"- {c}\n" for c in t.acceptance)
        + (f"Arquivos: {', '.join(t.files_hint)}\n" if t.files_hint else "")
        for t in test_tasks
    )
    description = (
        f"Testes de aceite da história {story_id} ({story.title if story else ''}).\n\n"
        f"```gherkin\n{render_feature(story_id, story.title if story else '', acs)}```\n"
        + (f"\nContratos: {', '.join(c.path for c in spec.contracts)}\n" if spec.contracts else "")
        + (
            "\n## Testes previstos no plano (são seus: o desenvolvedor não altera os testes depois)\n" + planned
            if planned
            else ""
        )
        + (f"\n## Correção pedida pelo Nexo\n{feedback}\n" if feedback else "")
    )
    files = sorted({f for t in tasks for f in t.files_hint})
    story_tasks.write(
        r,
        qa_id,
        f"Testes de aceite {story_id}",
        "test",
        description,
        [f"{a.id}: {a.title}" for a in acs],
        files,
        story_id,
        spec,
    )
    report = story_tasks.run_one(r, qa_id, st, role="qa-author", policy="test-writer")
    if report.state != "Concluido":
        return story_tasks.problem(r, story_id, run_id, st, qa_id, report)
    before = git.head(worktree) or ""
    story_tasks.merge(r, story_id, run_id, st, worktree, [qa_id])
    new_tests = [
        f for f in git.git(["diff", "--name-only", f"{before}..HEAD"], worktree).splitlines() if is_test_file(f)
    ]
    tagged: set[str] = set()
    for f in new_tests:
        tagged |= (
            ac_ids_in((worktree / f).read_text(encoding="utf-8", errors="replace"))
            if (worktree / f).is_file()
            else set()
        )
    missing = [a.id for a in acs if a.id not in tagged]
    if not new_tests or missing:
        st["qa_attempts"] += 1
        if st["qa_attempts"] >= 2:
            return r._escalate(
                story_id, run_id, st, f"QA não cobriu os critérios: {', '.join(missing) or 'nenhum teste'}"
            )
        st["qa_feedback"] = (
            f"Faltam testes marcados para: {', '.join(missing)}." if missing else "Nenhum arquivo de teste foi criado."
        )
        return None
    lint = lint_new_tests(r, run_id, worktree, new_tests, "qa")
    if lint:
        st["qa_attempts"] += 1
        if st["qa_attempts"] >= 2:
            return r._escalate(story_id, run_id, st, f"testes de aceite com problemas de lint: {lint[0]}")
        st["qa_feedback"] = "Corrija o lint dos testes (o Nexo já aplicou as correções automáticas): " + "; ".join(
            lint[:5]
        )
        return None
    st["locked"] = {f: sha256_file(worktree / f) for f in new_tests if (worktree / f).is_file()}
    red = run_pipeline(r._stack(), worktree, r._out(run_id, "qa-red"), r._brain(), only=["unit"])
    unit = red.stage("unit")
    st["qa_red"] = bool(unit and unit.status in (FAILED, ERROR))
    if not st["qa_red"]:
        r.ui("info", {"message": "aviso: os testes de aceite já passam antes da implementação"})
    r.ctx.audit(
        "story.qa",
        {"story": story_id, "tests": new_tests, "locked": list(st["locked"]), "red_before_impl": st["qa_red"]},
        ref=story_id,
    )
    st["phase"] = "characterize" if to_characterize(r, tasks) else "tasks"
    return None


def to_characterize(r: StoryRunner, tasks: list[BacklogTask]) -> list[Any]:
    from nexo.kernel import services

    discover = services.load("planning.discover")

    cfg = dict(r.cfg.get("brownfield") or {})
    if not cfg.get("characterization", True) or discover.load_map(r.root) is None:
        return []
    threshold = float(cfg.get("characterization_below", 60))
    targets = sorted({f for t in tasks for f in t.files_hint})
    return cast("list[Any]", discover.impacted_low_coverage(r.root, targets, threshold))


def characterize(
    r: StoryRunner, story_id: str, run_id: str, st: dict[str, Any], spec: Spec, tasks: list[BacklogTask], worktree: Path
) -> StoryReport | None:
    from nexo.kernel import services

    discover = services.load("planning.discover")

    impacted = to_characterize(r, tasks)
    if not impacted:
        st["phase"] = "tasks"
        return None
    char_id = f"{story_id}-CHAR{int(st.get('char_attempts', 0)) + 1}"  # tarefa nova a cada tentativa
    budget = int((r.cfg.get("brownfield") or {}).get("context_chars", 3000))
    context = discover.context_for(r.root, [f.path for f in impacted], budget)
    feedback = st.get("char_feedback", "")
    story_tasks.write(
        r,
        char_id,
        f"Caracterização do código existente ({story_id})",
        "test",
        f"Arquivos com pouca cobertura que a história {story_id} vai alterar:\n{context}"
        + (f"\n\nTentativa anterior: {feedback}" if feedback else ""),
        ["comportamento atual das funções públicas dos arquivos listados", "testes passam sem mudar o código"],
        [f.path for f in impacted],
        story_id,
        spec,
    )
    report = story_tasks.run_one(r, char_id, st, role="characterization", policy="test-writer")
    if report.state != "Concluido":
        return story_tasks.problem(r, story_id, run_id, st, char_id, report)
    before = git.head(worktree) or ""
    story_tasks.merge(r, story_id, run_id, st, worktree, [char_id])
    new_tests = [
        f for f in git.git(["diff", "--name-only", f"{before}..HEAD"], worktree).splitlines() if is_test_file(f)
    ]
    lint = lint_new_tests(r, run_id, worktree, new_tests, "characterization")
    failing = [f"lint: {x}" for x in lint] or characterization_failures(r, story_id, run_id, st, worktree, new_tests)
    if not new_tests or failing:
        if new_tests:  # teste que não descreve o comportamento atual não pode ficar no branch
            git.git(["rm", "-q", "--", *new_tests], worktree)
            git.checkpoint(worktree, f"nexo: remove caracterização inválida de {story_id}")
        st["char_attempts"] = int(st.get("char_attempts", 0)) + 1
        if st["char_attempts"] < 2:
            st["char_feedback"] = (
                f"testes que falham no código atual: {', '.join(failing)}" if failing else "nenhum teste criado"
            )
            return None
        st["characterization_missing"] = [f.path for f in impacted]  # segue, mas o portão vira humano
        r.ctx.audit(
            "story.characterization_failed",
            {"story": story_id, "files": st["characterization_missing"]},
            ref=story_id,
        )
        st["phase"] = "tasks"
        return None
    for f in new_tests:
        if (worktree / f).is_file():
            st["locked"][f] = sha256_file(worktree / f)
    r.ctx.audit(
        "story.characterized",
        {"story": story_id, "files": [f.path for f in impacted], "tests": new_tests},
        ref=story_id,
    )
    r.ui("info", {"message": f"caracterização: {len(new_tests)} teste(s) do comportamento atual travados"})
    st["phase"] = "tasks"
    return None


def lint_new_tests(r: StoryRunner, run_id: str, worktree: Path, tests: list[str], label: str) -> list[str]:
    """Testes escritos por agente passam pelo lint (com correção automática) ANTES de serem travados."""
    from nexo.kernel import services

    lint_files = services.get("quality.runner", "lint_files")

    before = git.head(worktree)
    findings = lint_files(r._stack(), worktree, r._out(run_id, f"lint-{label}"), tests, r._brain())
    if git.head(worktree) == before:
        git.checkpoint(worktree, f"nexo: correções automáticas de lint nos testes ({label})")
    return [f"{f.file}:{f.line} {f.rule} {f.message}" for f in findings]


def characterization_failures(
    r: StoryRunner, story_id: str, run_id: str, st: dict[str, Any], worktree: Path, tests: list[str]
) -> list[str]:
    """Roda os testes de caracterização sobre a BASE (código atual, sem os testes de aceite da história):
    teste que falha ou nem executa não descreve o comportamento atual."""
    n = int(st.get("char_attempts", 0)) + 1
    # nome que não colide com a worktree da tarefa US-x-CHARn em sistemas de arquivos sem distinção de caixa
    probe = git.worktree_path(r.root, f"_probe-caracterizacao-{story_id}-{n}")
    branch = f"nexo/char/{story_id}-{n}"
    with GIT_LOCK:
        git.add_worktree(r.root, probe, branch, st["base"])
    try:
        for f in tests:
            dest = probe / f
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(worktree / f, dest)
        run = run_pipeline(r._stack(), probe, r._out(run_id, f"characterization-{n}"), r._brain(), only=["unit"])
    finally:
        with GIT_LOCK:
            git.remove_worktree(r.root, probe)
            git.git(["branch", "-q", "-D", branch], r.root, check=False)
    stems = {Path(f).stem for f in tests}

    def mine(t: TestCase) -> bool:
        # Flutter e Vitest (Angular/React) identificam o teste pelo caminho do arquivo; xUnit/JUnit/pytest, pela classe
        if any(t.classname.endswith(f) or t.file.endswith(f) for f in tests):
            return True
        return any(
            t.classname == s or t.classname.endswith("." + s) or t.name == s or t.name.endswith("." + s) for s in stems
        )

    ran = [t for t in run.tests if mine(t)]
    failing = [t.name for t in ran if t.status in (FAILED, ERROR)]
    if not ran:
        unit = run.stage("unit")
        why = f"{unit.status}: {unit.summary}" if unit else "etapa unit ausente"
        failing = [f"{f} (não executado — {why})" for f in tests]
    return failing
