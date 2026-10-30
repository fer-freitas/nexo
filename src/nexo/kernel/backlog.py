"""Backlog gerado na aprovação: histórias, tarefas, validação e grafo de paralelismo."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from nexo.kernel.models.work import TASK_KINDS, Task

HUMAN_GATE_KINDS = ("migration", "infra")
MAX_FILES_PER_TASK = 8


class Story(BaseModel):
    id: str
    title: str
    ac_refs: list[str] = Field(default_factory=list)
    points: int = 0
    # narrativa da história (template story.md)
    as_a: str = ""
    i_want: str = ""
    so_that: str = ""
    context: str = ""
    repo: str = ""  # repositório da história numa feature de vários repositórios


class BacklogTask(BaseModel):
    id: str
    story: str
    kind: str
    title: str
    description: str = ""
    acceptance: list[str] = Field(default_factory=list)
    files_hint: list[str] = Field(default_factory=list)
    section_refs: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    size: str = "M"
    human_gate: bool = False
    script: dict[str, str] | None = None  # F25

    def to_task(self, stack: str | None, data_class: str | None) -> Task:
        return Task(
            id=self.id,
            title=self.title,
            kind=self.kind,
            stack=stack,
            size=self.size,
            description=self.description,
            acceptance=self.acceptance,
            files_hint=self.files_hint,
            section_refs=self.section_refs,
            depends_on=self.depends_on,
            data_class=data_class,
            story=self.story,
            human_gate=self.human_gate,
            script=self.script,
        )


class Backlog(BaseModel):
    feature: str
    stories: list[Story] = Field(default_factory=list)
    tasks: list[BacklogTask] = Field(default_factory=list)


@dataclass
class BacklogCheck:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def overlaps(a: list[str], b: list[str]) -> bool:
    for x in a:
        for y in b:
            if x == y or fnmatch.fnmatch(x, y) or fnmatch.fnmatch(y, x):
                return True
    return False


def _cycle(tasks: dict[str, BacklogTask]) -> list[str] | None:
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for dep in tasks[node].depends_on:
            if dep not in tasks:
                continue
            if state.get(dep) == 1:
                return [*stack[stack.index(dep) :], dep]
            if state.get(dep) is None:
                found = visit(dep)
                if found:
                    return found
        stack.pop()
        state[node] = 2
        return None

    for node in tasks:
        if state.get(node) is None:
            found = visit(node)
            if found:
                return found
    return None


def ancestors(tasks: dict[str, BacklogTask], node: str) -> set[str]:
    seen: set[str] = set()
    todo = list(tasks[node].depends_on)
    while todo:
        cur = todo.pop()
        if cur in seen or cur not in tasks:
            continue
        seen.add(cur)
        todo.extend(tasks[cur].depends_on)
    return seen


def normalize(backlog: Backlog) -> Backlog:
    """Ajustes determinísticos: portão humano obrigatório por tipo."""
    tasks = [t.model_copy(update={"human_gate": t.human_gate or t.kind in HUMAN_GATE_KINDS}) for t in backlog.tasks]
    return backlog.model_copy(update={"tasks": tasks})


def validate(backlog: Backlog, ac_ids: list[str]) -> BacklogCheck:
    check = BacklogCheck()
    story_ids = [s.id for s in backlog.stories]
    task_ids = [t.id for t in backlog.tasks]
    for label, ids in (("história", story_ids), ("tarefa", task_ids)):
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            check.errors.append(f"{label} com id duplicado: {', '.join(dup)}")
    tasks = {t.id: t for t in backlog.tasks}
    for t in backlog.tasks:
        if t.story not in story_ids:
            check.errors.append(f"{t.id}: história {t.story} não existe")
        if t.kind not in TASK_KINDS:
            check.errors.append(f"{t.id}: tipo inválido {t.kind}")
        missing = [d for d in t.depends_on if d not in tasks]
        if missing:
            check.errors.append(f"{t.id}: depende de tarefa inexistente {', '.join(missing)}")
        if t.size == "L":
            check.errors.append(f"{t.id}: tarefa grande demais (L) — quebre em tarefas menores")
        if len(t.files_hint) > MAX_FILES_PER_TASK:
            check.errors.append(f"{t.id}: {len(t.files_hint)} arquivos previstos (máx. {MAX_FILES_PER_TASK})")
        if not t.files_hint:
            check.warnings.append(f"{t.id}: sem arquivos previstos (paralelismo conservador)")
    cycle = _cycle(tasks)
    if cycle:
        check.errors.append("ciclo de dependências: " + " → ".join(cycle))
    covered = {ac for s in backlog.stories for ac in s.ac_refs}
    uncovered = [ac for ac in ac_ids if ac not in covered]
    if uncovered:
        check.errors.append(f"critérios sem história: {', '.join(uncovered)}")
    for s in backlog.stories:
        if not [t for t in backlog.tasks if t.story == s.id]:
            check.errors.append(f"{s.id}: história sem tarefas")
        # F49 (achado no e2e Angular): história sem critério passava na aprovação e só parava na DoR
        if not s.ac_refs:
            check.errors.append(f"{s.id}: história sem critérios de aceite (ac_refs)")
        unknown = [ac for ac in s.ac_refs if ac_ids and ac not in ac_ids]
        if unknown:
            check.errors.append(f"{s.id}: critérios inexistentes na spec: {', '.join(unknown)}")
    if not cycle:
        for story in story_ids:
            contracts = [t.id for t in backlog.tasks if t.story == story and t.kind == "contract"]
            if not contracts:
                continue
            for t in backlog.tasks:
                if t.story == story and t.kind != "contract" and not set(contracts) & ancestors(tasks, t.id):
                    check.errors.append(f"{t.id}: deve depender do contrato {contracts[0]}")
    return check


def waves(backlog: Backlog) -> list[list[str]]:
    """Ondas de execução: dependências resolvidas e sem sobreposição de arquivos na mesma onda."""
    tasks = {t.id: t for t in backlog.tasks}
    done: set[str] = set()
    pending = [t.id for t in backlog.tasks]
    out: list[list[str]] = []
    while pending:
        ready = [i for i in pending if all(d in done or d not in tasks for d in tasks[i].depends_on)]
        if not ready:
            raise ValueError("grafo com ciclo")
        wave: list[str] = []
        for i in ready:
            if any(overlaps(tasks[i].files_hint, tasks[j].files_hint) for j in wave):
                continue
            wave.append(i)
        out.append(wave)
        done.update(wave)
        pending = [i for i in pending if i not in done]
    return out
