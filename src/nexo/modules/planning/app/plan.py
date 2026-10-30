"""Planejamento: `plan`, `plan feature`, `plan answer`, `plan status`, `plan approve`, `plan graph`."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from nexo.kernel.backlog import Backlog, normalize, validate, waves
from nexo.kernel.context import PROJECT_DIR, AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.platform import config
from nexo.kernel.runtime import yamlfile
from nexo.kernel.runtime.resources import load_yaml
from nexo.kernel.secrets import credentials
from nexo.kernel.spec import questions as q
from nexo.kernel.spec.gherkin import lint_feature, render_feature
from nexo.kernel.spec.model import Readiness, Spec, apply_patch, readiness
from nexo.kernel.workflow.engine import Workflow
from nexo.kernel.workspace import specs
from nexo.kernel.workspace import tasks as workspace_tasks
from nexo.modules.execution.app import llm
from nexo.modules.knowledge.app import content
from nexo.modules.planning.adapters import ingest
from nexo.modules.planning.app import backlog_review, classify, templates
from nexo.modules.planning.app.schemas import planner_schema
from nexo.modules.setup.app.project import list_files
from nexo.store.runs import RunStore, project_key

PLACEHOLDER_TITLE = "Nova feature"
DEFAULT_WORKSPACE = "rascunho"


def breakdown_schema() -> dict[str, Any]:
    full = Backlog.model_json_schema()
    defs = full.get("$defs", {})
    return {
        "type": "object",
        "$defs": defs,
        "properties": {"stories": full["properties"]["stories"], "tasks": full["properties"]["tasks"]},
        "required": ["stories", "tasks"],
    }


@dataclass
class PlanReport:
    feature: str
    folder: str
    title: str
    score: int
    ready: bool
    missing: list[dict[str, Any]] = field(default_factory=list)
    questions: list[dict[str, Any]] = field(default_factory=list)
    ingested: list[dict[str, Any]] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    run_id: str | None = None
    backlog: dict[str, Any] | None = None
    waves: list[list[str]] | None = None
    workspace: str | None = None
    state: str | None = None


def story_workflow(ctx: AppContext) -> Workflow:
    brain = ctx.brain_dir if ctx.brain_dir.is_dir() else None
    return Workflow.from_mapping(load_yaml("workflows/story.yaml", brain))


def record_suspicion(folder: Path, suspicion: dict[str, Any]) -> None:
    """Registra a suspeita em `specs/F-n/security.yaml` (as histórias da feature passam a exigir portão humano)."""
    from nexo.kernel.runtime import yamlfile

    path = folder / "security.yaml"
    data = yamlfile.load(path)
    items = list(data.get("injection_suspects") or [])
    if suspicion not in items:
        items.append(suspicion)
    yamlfile.dump(path, {**data, "injection_suspects": items})


def injection_suspects(folder: Path) -> list[dict[str, Any]]:
    from nexo.kernel.runtime import yamlfile

    return list(yamlfile.load(folder / "security.yaml").get("injection_suspects") or [])


class Planner:
    def __init__(self, ctx: AppContext, workspace: str | None = None, ui: llm.UI | None = None) -> None:
        self.ctx = ctx
        self.ui = ui
        self.workspace: str | None = None
        if ctx.project_root is not None and workspace is None:
            self.root = ctx.project_root
        else:
            self.workspace = workspace or DEFAULT_WORKSPACE
            self.root = workspace_root(ctx, self.workspace)
        cfg_path = self.root / PROJECT_DIR / "nexo.yaml"
        self.project_cfg = yamlfile.load(cfg_path)
        self.cfg = config.resolved(ctx).values
        self.store = RunStore(ctx.conn, project_key(self.root))
        self.workflow = story_workflow(ctx)
        self.project = str(self.project_cfg.get("project") or self.root.name)
        from nexo.modules.demands.app.tracker import Sync

        self.sync = Sync(ctx, self.root)
        self._docs = templates.FeatureDocs()

    # ------------------------------------------------------------ comandos

    def new(self, inputs: list[str], title: str | None = None, fid: str | None = None) -> PlanReport:
        if not inputs:
            raise NexoError("informe ao menos um insumo: arquivos, link Figma ou texto entre aspas")
        fid = fid or specs.next_feature_id(self.root)
        folder = specs.create_feature_dir(self.root, fid, title or PLACEHOLDER_TITLE)
        spec = Spec(id=fid, title=title or PLACEHOLDER_TITLE)
        specs.save_spec(folder, spec)
        specs.save_questions(folder, [])
        self.store.upsert_item(fid, "feature", spec.title, self.project, "local", self.workflow.initial)
        self._transition(fid, "plan")
        self.ctx.audit("plan.created", {"feature": fid, "folder": str(folder)}, ref=fid)
        ingested = self._ingest(folder, inputs)
        return self._refine(folder, ingested, [])

    def feature(self, fid: str, inputs: list[str], answers: dict[str, str] | None = None) -> PlanReport:
        folder = self._folder(fid)
        self._ensure_refinable(fid)
        answered = self._record_answers(folder, answers or {})
        ingested = self._ingest(folder, inputs) if inputs else []
        return self._refine(folder, ingested, answered)

    def answer(self, fid: str, answers: dict[str, str], refine: bool = True) -> PlanReport:
        folder = self._folder(fid)
        answered = self._record_answers(folder, answers)
        if refine and any(x.source not in ("agent", "dev", "review") for x in answered):  # não reabrem a spec
            self._ensure_refinable(fid)
            return self._refine(folder, [], answered)
        return self.status(fid)

    def status(self, fid: str) -> PlanReport:
        folder = self._folder(fid)
        spec = specs.load_spec(folder)
        qs = specs.load_questions(folder)
        report = self._report(folder, spec, qs, self._readiness(folder, spec, qs))
        backlog = specs.load_backlog(folder)
        if backlog is not None:
            report.backlog = backlog.model_dump(mode="json")
        return report

    def approve(self, fid: str, force: bool = False, reason: str = "") -> PlanReport:
        from nexo.modules.planning.app import roleflow

        if roleflow.active(self.ctx):
            raise NexoError(
                f"fluxo por papéis ativo: cada papel aprova a sua parte (nexo plan approve {fid} --part "
                "requisitos|design|plano)"
            )
        folder = self._folder(fid)
        state = self.store.item_state(fid)
        if state not in (None, "Ideia", "Refinamento") and specs.load_backlog(folder) is not None:
            # já aprovada (achado no teste real: aprovar de novo dava "transição inválida"): devolve o backlog
            if self.ui is not None:
                self.ui("info", {"message": f"{fid} já aprovada ({state}); backlog mantido"})
            return self.status(fid)
        spec = specs.load_spec(folder)
        qs = specs.load_questions(folder)
        ready = self._readiness(folder, spec, qs)
        if not ready.ready:
            if not force:
                missing = (
                    ", ".join(i.label for i in ready.missing) or f"{ready.blocking_questions} dúvida(s) bloqueante(s)"
                )
                raise NexoError(f"prontidão {ready.score}% — pendências: {missing}. Use --force --reason para aprovar")
            if len(reason.strip()) < 10:
                raise NexoError("--force exige --reason com a justificativa (auditada no portão 'spec')")
            self.ctx.audit(
                "gate.decision",
                {
                    "gate": "spec",
                    "feature": fid,
                    "decision": "approved_with_exception",
                    "score": ready.score,
                    "reason": reason,
                },
                ref=fid,
            )
        else:
            self.ctx.audit(
                "gate.decision", {"gate": "spec", "feature": fid, "decision": "approved", "score": 100}, ref=fid
            )
        self._warn_suspects(folder)
        backlog, run_id = self.draft_backlog(folder, spec)
        report = self.materialize(folder, spec, backlog)
        report.run_id = run_id
        from nexo.modules.planning.app import ready as story_ready

        created = story_ready.init_all(self.ctx, [s.id for s in backlog.stories])
        if created and self.ui is not None:
            self.ui("info", {"message": f"refinamento (DoR) criado para {', '.join(created)}: o tech lead completa"})
        return report

    def _warn_suspects(self, folder: Path) -> None:
        suspects = injection_suspects(folder)
        if suspects:  # S-03: aprovação segue humana, mas o aprovador é avisado e as histórias terão portão humano
            sources = ", ".join(str(x["source"]) for x in suspects)
            self._warn(
                f"insumos com possível injeção de prompt: {sources} — histórias desta "
                "feature exigirão aprovação humana no portão de código"
            )

    def draft_backlog(self, folder: Path, spec: Spec, feedback: str = "") -> tuple[Backlog, str | None]:
        """Quebra a spec em histórias e tarefas (agente `breakdown`), sem gravar nada no projeto nem no tracker."""
        fid = spec.id
        number = fid.split("-")[1]
        ac_ids = [s.id for s in spec.acceptance]

        def check(data: dict[str, Any]) -> list[str]:
            backlog = normalize(Backlog.model_validate({"feature": fid, **data}))
            result = validate(backlog, ac_ids)
            return result.errors + templates.backlog_problems(self.ctx, backlog, spec)

        extra = f"\n## Ajustes pedidos pelo dev\n{feedback}\n" if feedback else ""
        program_repos = dict((self.project_cfg.get("program") or {}).get("repos") or {})
        if program_repos:  # feature em vários repositórios — cada história num repositório
            extra += (
                "\n## Repositórios da feature\nCada história acontece em um só repositório: preencha `repo` com um"
                " destes nomes: "
                + ", ".join(sorted(program_repos))
                + ". Quem fornece contrato vem antes (depends_on).\n"
            )
        prompt = content.prompt(self.ctx, "breakdown").format(
            n=number,
            feedback=extra,
            spec=yamlfile_dump(spec.model_dump(mode="json")),
            repo_map=self._repo_overview(spec),
        )
        result = llm.call_structured(
            self.ctx,
            "breakdown",
            fid,
            prompt,
            breakdown_schema(),
            folder,
            facts={"kind": "planning"},
            data_class=spec.data_class or "confidential",
            validate=check,
            ui=self.ui,
            **self._route_options(),
        )
        drafted = normalize(Backlog.model_validate({"feature": fid, **result.data}))
        return backlog_review.review(self, folder, spec, drafted), result.run_id

    def materialize(self, folder: Path, spec: Spec, backlog: Backlog) -> PlanReport:
        """Backlog aprovado vira tarefas, Gherkin, rastreabilidade, documentos e itens no tracker."""
        fid = spec.id
        specs.save_backlog(folder, backlog)
        self._write_tasks(backlog, spec)
        self._write_gherkin(folder, spec, backlog)
        self._write_traceability(folder, spec, backlog)
        by_ac = {a.id: a for a in spec.acceptance}
        docs_dir = folder / "backlog"
        docs_dir.mkdir(exist_ok=True)
        for story in backlog.stories:
            self.store.upsert_item(story.id, "story", story.title, self.project, f"local:{fid}", "Pronto")
            gherkin = render_feature(story.id, story.title, [by_ac[a] for a in story.ac_refs if a in by_ac])
            doc = templates.render(self.ctx, "story", templates.story_context(story, spec, backlog), self.root)
            (docs_dir / f"{story.id}.md").write_text(doc.text, encoding="utf-8")
            self.sync.ensure(
                story.id,
                "story",
                story.title,
                "Pronto",
                parent=fid,
                description=doc.tracker_description(),
                fields={"story_points": story.points, "acceptance": doc.tracker_acceptance() or gherkin},
            )
        for t in backlog.tasks:
            doc = templates.render(self.ctx, "task", templates.task_context(t), self.root)
            (docs_dir / f"{t.id}.md").write_text(doc.text, encoding="utf-8")
            self.sync.ensure(t.id, "task", t.title, "Pronto", parent=t.story, description=doc.tracker_description())
        self._readiness(folder, spec, specs.load_questions(folder))  # documentos com backlog e rastreabilidade
        from nexo.modules.knowledge.app import docs as knowledge_docs

        knowledge_docs.publish(self.ctx, folder, spec, self.root)
        self._transition(fid, "approve_spec")
        self.ctx.audit(
            "plan.approved", {"feature": fid, "stories": len(backlog.stories), "tasks": len(backlog.tasks)}, ref=fid
        )
        report = self.status(fid)
        report.backlog = backlog.model_dump(mode="json")
        report.waves = waves(backlog)
        return report

    def graph(self, fid: str) -> PlanReport:
        folder = self._folder(fid)
        backlog = specs.load_backlog(folder)
        if backlog is None:
            raise NexoError(f"{fid} ainda não foi aprovada (nexo plan approve {fid})")
        report = self.status(fid)
        report.backlog = backlog.model_dump(mode="json")
        report.waves = waves(backlog)
        return report

    # ------------------------------------------------------------ internos

    def _folder(self, fid: str) -> Path:
        """Pasta da feature; se o banco local não conhece o item (ex.: repo clonado), reconstrói pelo arquivo."""
        folder = specs.feature_dir(self.root, fid)
        if self.store.item_state(fid) is None:
            inferred = "Pronto" if specs.load_backlog(folder) is not None else "Refinamento"
            spec = specs.load_spec(folder)
            self.store.upsert_item(fid, "feature", spec.title, self.project, "local", inferred)
            self.ctx.audit("state.reconciled", {"item": fid, "state": inferred, "source": "spec files"}, ref=fid)
        return folder

    def _route_options(self) -> dict[str, Any]:
        o = self.ctx.options
        return {"forced_tool": o.tool, "forced_model": o.model, "profile": o.profile, "scope": project_key(self.root)}

    def _ensure_refinable(self, fid: str) -> None:
        state = self.store.item_state(fid)
        if state not in (None, "Refinamento", "Ideia"):
            if state == "Pronto" and self.workflow.can(state, "spec_ambiguity"):
                self._transition(fid, "spec_ambiguity")
                return
            raise NexoError(f"{fid} está em {state}; refinamento só em Refinamento")

    def _transition(self, fid: str, event: str) -> None:
        current = self.store.item_state(fid) or self.workflow.initial
        target = self.workflow.next_state(current, event)
        self.store.set_item_state(fid, target)
        self.ctx.audit("state.transition", {"item": fid, "from": current, "to": target, "event": event}, ref=fid)
        if self.sync.ref(fid) is not None:  # a criação no tracker ocorre após o 1º refinamento (título definido)
            self.sync.transition(fid, target)

    def _ingest(self, folder: Path, inputs: list[str]) -> list[ingest.Ingested]:
        token = credentials.get_secret("figma") if any(ingest.FIGMA_URL.match(i) for i in inputs) else None
        model = str((self.cfg.get("planning") or {}).get("whisper_model", "base"))
        items = ingest.ingest(inputs, folder / "inputs", figma_token=token, whisper_model=model)
        for item in items:
            self.ctx.audit(
                "plan.ingested",
                {"kind": item.kind, "path": item.path, "chars": item.chars, "redactions": item.redactions},
                ref=folder.name,
            )
        return items

    def _record_answers(self, folder: Path, answers: dict[str, str]) -> list[q.Question]:
        qs = specs.load_questions(folder)
        answered: list[q.Question] = []
        for qid, value in answers.items():
            text = value
            path = Path(value).expanduser()
            if path.is_file():  # resposta em arquivo, áudio ou imagem
                item = self._ingest(folder, [str(path)])[0]
                text = Path(item.path).read_text(encoding="utf-8") if item.kind != "image" else f"(imagem) {item.path}"
            try:
                qs = q.answer(qs, qid, text)
            except ValueError as exc:
                raise NexoError(str(exc)) from exc
            answered.append(next(x for x in qs if x.id == qid))
            self.ctx.audit("plan.answered", {"question": qid, "chars": len(text)}, ref=folder.name)
        specs.save_questions(folder, qs)
        classify.apply_answers(self.ctx, folder, answered)  # classificação de dados: decisão humana
        return answered

    def _refine(self, folder: Path, ingested: list[ingest.Ingested], answered: list[q.Question]) -> PlanReport:
        spec = specs.load_spec(folder)
        qs = specs.load_questions(folder)
        before = readiness(spec, q.blocking_open(qs))
        inputs_text = self._inputs_text(folder, ingested)
        prompt = content.prompt(self.ctx, "planner").format(
            spec=yamlfile_dump(spec.model_dump(mode="json")),
            missing="\n".join(f"- {i.label}: {i.hint}" for i in before.missing) or "- nenhuma",
            asked="\n".join(f"- {x.id} [{x.status}] {x.text}" for x in qs) or "- nenhuma",
            answers="\n".join(
                f"- {x.id}: {x.text}\n  Resposta: {self._untrusted(folder, f'resposta:{x.id}', str(x.answer))}"
                for x in answered
            )
            or "- nenhuma",
            inputs=inputs_text or "- nenhum",
        )

        def check(data: dict[str, Any]) -> list[str]:
            problems: list[str] = []
            try:
                apply_patch(spec, dict(data.get("spec_patch") or {}))
            except ValueError as exc:
                problems.append(str(exc))
            problems += contract_problems(data.get("contract_files") or [])
            return problems

        result = llm.call_structured(
            self.ctx,
            "planner",
            spec.id,
            prompt,
            planner_schema(),
            folder,
            facts={"kind": "planning"},
            data_class=spec.data_class or str(self.project_cfg.get("data_class", "confidential")),
            read_files=True,
            validate=check,
            ui=self.ui,
            **self._route_options(),
        )
        new_spec, changed = apply_patch(spec, dict(result.data.get("spec_patch") or {}))
        extra = [a for a in result.data.get("assumptions") or [] if a not in new_spec.assumptions]
        if extra:
            new_spec = new_spec.model_copy(update={"assumptions": [*new_spec.assumptions, *extra]})
        for cf in result.data.get("contract_files") or []:
            target = folder / str(cf["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(cf["content"]), encoding="utf-8")
        qs = q.merge_new(qs, list(result.data.get("questions") or []))
        new_spec, qs = classify.hold(self.ctx, spec, new_spec, qs)  # classe de dados: a IA sugere, a pessoa decide
        specs.save_spec(folder, new_spec)
        specs.save_questions(folder, qs)
        folder = self._maybe_rename(folder, new_spec)
        self.store.upsert_item(
            new_spec.id,
            "feature",
            new_spec.title,
            self.project,
            "local",
            self.store.item_state(new_spec.id) or "Refinamento",
        )
        after = self._readiness(folder, new_spec, qs)
        self.sync.ensure(
            new_spec.id,
            "feature",
            new_spec.title,
            self.store.item_state(new_spec.id) or "Refinamento",
            description=(self._docs.sdd.tracker_description() if self._docs.sdd else "")[:8000],
        )
        self.ctx.audit(
            "plan.refined",
            {
                "feature": new_spec.id,
                "before": before.score,
                "after": after.score,
                "changed": changed,
                "open_questions": len(q.open_questions(qs)),
            },
            ref=new_spec.id,
        )
        report = self._report(folder, new_spec, qs, after)
        report.ingested = [i.__dict__ for i in ingested]
        report.changed = changed
        report.run_id = result.run_id
        return report

    def _maybe_rename(self, folder: Path, spec: Spec) -> Path:
        desired = folder.parent / f"{spec.id}-{specs.slug(spec.title)}"
        if desired != folder and not desired.exists():
            folder.rename(desired)
            return desired
        return folder

    def _inputs_text(self, folder: Path, ingested: list[ingest.Ingested]) -> str:
        blocks: list[str] = []
        for item in ingested:
            rel = Path(item.path).relative_to(folder).as_posix()
            if item.kind == "image":
                blocks.append(f"### Imagem: {rel}\n(abra com a ferramenta Read)")
            else:
                text = self._untrusted(folder, f"{item.kind}:{rel}", Path(item.path).read_text(encoding="utf-8"))
                blocks.append(f"### {item.kind}: {rel}\n{text}")
        return "\n\n".join(blocks)

    def _warn(self, message: str) -> None:
        if self.ui is not None:
            self.ui("warning", {"message": message})

    def _untrusted(self, folder: Path, source: str, text: str) -> str:
        """S-03: insumo externo entra cercado como dado; suspeita de injeção fica registrada na feature."""
        from nexo.kernel.security.injection import check

        fenced, suspicion = check(source, text)
        if suspicion is not None:
            record_suspicion(folder, suspicion.as_dict())
            self.ctx.audit(
                "security.injection_suspect",
                {
                    "feature": "-".join(folder.name.split("-")[:2]),
                    "source": source.split(":")[0],
                    "rules": list(suspicion.rules),
                },
                ref=folder.name,
            )
            self._warn(f"possível injeção de prompt em {source}: {', '.join(suspicion.rules)}")
        return fenced

    def _readiness(self, folder: Path, spec: Spec, qs: list[q.Question], write: bool = True) -> Readiness:
        """Prontidão = regras do planejamento + DoR dos templates; também regrava os documentos."""
        base = readiness(spec, q.blocking_open(qs))
        states = {
            s.id: self.store.item_state(s.id) or "Pronto"
            for s in (specs.load_backlog(folder) or Backlog(feature=spec.id)).stories
        }
        states[spec.id] = self.store.item_state(spec.id) or "Refinamento"
        docs = (
            templates.write_feature_docs(
                self.ctx, folder, spec, qs, base.score, specs.load_backlog(folder), states, root=self.root
            )
            if write
            else templates.FeatureDocs()
        )
        self._docs = docs
        return templates.with_template_items(self.ctx, base, spec, docs)

    def _report(self, folder: Path, spec: Spec, qs: list[q.Question], r: Readiness) -> PlanReport:
        return PlanReport(
            feature=spec.id,
            folder=str(folder),
            title=spec.title,
            score=r.score,
            ready=r.ready,
            missing=[{"key": i.key, "label": i.label, "weight": i.weight, "hint": i.hint} for i in r.missing],
            questions=[x.model_dump() for x in q.open_questions(qs)],
            workspace=self.workspace,
            state=self.store.item_state(spec.id),
        )

    def _repo_overview(self, spec: Spec) -> str:
        """Com mapa (`nexo discover`): arquivos relevantes com símbolos, dentro do orçamento; sem mapa: lista."""
        from nexo.modules.planning.app import discover

        budget = int((self.project_cfg.get("brownfield") or {}).get("repo_map_chars", 6000))
        discover.ensure_map(self.ctx, self.root)
        query = " ".join([spec.title, spec.objective, *spec.scope_in, *(a.title for a in spec.acceptance)])
        ranked = discover.repo_overview(self.root, query, budget)
        if ranked:
            return ranked
        return "\n".join(f"- {p}" for p in self._repo_map()) or "- (repositório vazio)"

    def _repo_map(self, limit: int = 300) -> list[str]:
        files = [f for f in list_files(self.root) if not f.startswith(".nexo/")]
        return files[:limit]

    def _write_tasks(self, backlog: Backlog, spec: Spec) -> None:
        stacks = self.project_cfg.get("stacks") or []
        folder = workspace_tasks.tasks_dir(self.root)
        for t in backlog.tasks:
            state = self.store.item_state(t.id)
            if state not in (None, "Pronto"):
                continue  # não sobrescreve tarefa já iniciada
            task = t.to_task(stacks[0] if len(stacks) == 1 else None, spec.data_class or None)
            data = task.model_dump(exclude={"id"}, exclude_none=True)
            yamlfile.dump(folder / f"{t.id}.yaml", data)
            self.store.upsert_item(t.id, "task", t.title, self.project, f"local:{backlog.feature}", "Pronto")

    def _write_gherkin(self, folder: Path, spec: Spec, backlog: Backlog) -> None:
        out = folder / "features"
        out.mkdir(exist_ok=True)
        by_id = {s.id: s for s in spec.acceptance}
        for story in backlog.stories:
            scenarios = [by_id[a] for a in story.ac_refs if a in by_id]
            text = render_feature(story.id, story.title, scenarios)
            problems = lint_feature(text)
            if problems:
                raise NexoError(f"Gherkin inválido em {story.id}: {'; '.join(problems)}")
            (out / f"{story.id}.feature").write_text(text, encoding="utf-8")

    def _write_traceability(self, folder: Path, spec: Spec, backlog: Backlog) -> None:
        matrix = []
        for sc in spec.acceptance:
            stories = [s.id for s in backlog.stories if sc.id in s.ac_refs]
            tasks = [t.id for t in backlog.tasks if t.story in stories]
            matrix.append(
                {"ac": sc.id, "title": sc.title, "stories": stories, "tasks": tasks, "tests": [], "evidence": []}
            )
        yamlfile.dump(folder / "traceability.yaml", {"feature": spec.id, "matrix": matrix})


def yamlfile_dump(data: dict[str, Any]) -> str:
    import io

    buf = io.StringIO()
    YAML().dump(data, buf)
    return buf.getvalue()


def contract_problems(files: list[dict[str, Any]]) -> list[str]:
    """Contratos gerados precisam ser válidos antes de entrar na spec (2.7)."""
    from openapi_spec_validator import validate as validate_openapi

    problems: list[str] = []
    for cf in files:
        path = str(cf.get("path", ""))
        if not path.startswith("contracts/") or ".." in path:
            problems.append(f"contrato fora de contracts/: {path}")
            continue
        try:
            doc = YAML(typ="safe").load(str(cf.get("content", "")))
        except YAMLError as exc:
            problems.append(f"{path}: YAML/JSON inválido ({exc})")
            continue
        if not isinstance(doc, dict):
            problems.append(f"{path}: documento vazio ou inválido")
            continue
        from jsonschema import Draft202012Validator  # import caro, só na checagem de contratos
        from jsonschema.exceptions import SchemaError

        try:
            if "openapi" in doc:
                validate_openapi(json.loads(json.dumps(doc, default=str)))
            elif "asyncapi" in doc:
                if not doc.get("channels"):
                    problems.append(f"{path}: AsyncAPI sem channels")
            else:
                Draft202012Validator.check_schema(doc)
        except SchemaError as exc:
            problems.append(f"{path}: JSON Schema inválido ({exc.message})")
        except Exception as exc:  # validadores lançam tipos diversos
            problems.append(f"{path}: OpenAPI inválido ({str(exc).splitlines()[0][:200]})")
    return problems


# ------------------------------------------------------------ workspace sem repositório (2.8)


def workspace_root(ctx: AppContext, name: str) -> Path:
    root = ctx.home / "workspace" / specs.slug(name)
    cfg = root / PROJECT_DIR / "nexo.yaml"
    if not cfg.is_file():
        yamlfile.dump(cfg, {"project": name, "workspace": True, "stacks": [], "data_class": "confidential"})
    return root


def adopt_workspace(ctx: AppContext, name: str, target_root: Path) -> list[str]:
    source = ctx.home / "workspace" / specs.slug(name)
    if not (source / PROJECT_DIR).is_dir():
        raise NexoError(f"workspace '{name}' não encontrado")
    moved: list[str] = []
    for sub in ("specs", "tasks"):
        src = source / PROJECT_DIR / sub
        if not src.is_dir():
            continue
        dst = target_root / PROJECT_DIR / sub
        dst.mkdir(parents=True, exist_ok=True)
        for item in src.iterdir():
            dest = dst / item.name
            if dest.exists():
                raise NexoError(f"conflito ao migrar: {dest} já existe")
            shutil.move(str(item), str(dest))
            moved.append(f"{sub}/{item.name}")
    shutil.rmtree(source)
    ctx.audit("plan.workspace_adopted", {"workspace": name, "target": str(target_root), "moved": moved})
    return moved


def load_answers_file(path: Path) -> dict[str, str]:
    return {str(k): str(v) for k, v in yamlfile.load(path).items()}


def store_credential(ctx: AppContext, provider: str, token: str) -> None:
    credentials.set_secret(provider, token)
    ctx.audit("credential.set", {"provider": provider})
