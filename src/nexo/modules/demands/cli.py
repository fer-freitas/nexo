"""Comandos do fluxo por papel: ideia, responsáveis, propostas, reuniões, board e resumo da passagem."""

from __future__ import annotations

from typing import Annotated, Any

import typer
from rich.table import Table

from nexo.cli.surface import role_app
from nexo.kernel.errors import ExitCode, NexoError, PolicyViolation
from nexo.modules.demands.app import core, flow, meetings, proposals, views
from nexo.modules.demands.domain import model as m

Reason = Annotated[str, typer.Option("--reason", help="Motivo")]


def _pairs(values: list[str] | None) -> dict[str, str]:
    out = {}
    for item in values or []:
        key, sep, value = item.partition("=")
        if not sep:
            raise NexoError(f"use AC-1=texto (recebi {item})")
        out[key.strip()] = value.strip()
    return out


def register(root: typer.Typer, tools: Any) -> None:  # noqa: C901 — tabela de comandos
    ctx, emit, console, internal = tools.ctx, tools.emit, tools.console, tools.internal

    def show(data: Any, text: str | None = None) -> None:
        if ctx().options.json or text is None:
            emit(data)
        else:
            console.print(text, markup=False)

    def inbox(role: str) -> None:
        rows = core.holder_inbox(ctx(), role)
        if ctx().options.json:
            emit(rows)
            return
        if not rows:
            console.print(f"Nada com você como {m.ROLE_LABEL[role]} agora.")
            return
        console.print(f"Com você ({m.ROLE_LABEL[role]}), do mais antigo para o mais novo:")
        for i, r in enumerate(rows, 1):
            console.print(f"  {i}. {r['demand']}  {r['title'][:50]} — {r['action']}", markup=False)
        console.print(f"  ver: nexo brief {rows[0]['demand']}", markup=False)

    pm = role_app("pm", inbox)
    qa = role_app("qa", inbox)
    tl = role_app("tl", inbox)
    dev = role_app("dev", inbox)
    root.add_typer(pm, name="pm")
    root.add_typer(qa, name="qa")
    root.add_typer(tl, name="tl")
    root.add_typer(dev, name="dev")

    # ---------------------------------------------------------------- PM
    @pm.command("idea")
    def pm_idea(text: str, file: Annotated[str, typer.Option("--file", help="Documento de origem")] = "") -> None:
        """Registra a ideia e gera IDEA-n, sem IA."""
        data = flow.idea(ctx(), text, file)
        show(data, f"{data['id']}: {data['title']}\n  próximo: nexo pm decide {data['id']} build --reason \"...\"")

    @pm.command("decide")
    def pm_decide(did: str, decision: str, reason: Reason = "") -> None:
        """Decide a ideia: build, drop ou investigate."""
        data = flow.decide(ctx(), did, decision, reason)
        show(data, f"{did}: {decision}" + (f"\n  próximo: nexo pm split {did} --feature \"...\"" if decision == "build" else ""))

    @pm.command("split")
    def pm_split(
        did: str,
        feature: Annotated[list[str] | None, typer.Option("--feature", help="Título de uma feature")] = None,
        single: Annotated[bool, typer.Option("--single", help="A ideia vira uma só feature")] = False,
    ) -> None:
        """Divide a ideia em features."""
        data = flow.split(ctx(), did, feature or [], single)
        show(data, f"{did} → {', '.join(data['features'])}\n  próximo: nexo pm refine {data['features'][0]}")

    @pm.command("discover")
    def pm_discover(inputs: list[str]) -> None:
        """Problema, evidências, hipóteses e recomendação. O primeiro argumento pode ser IDEA-n."""
        args = [a for a in inputs if not a.startswith("IDEA-")]
        raise typer.Exit(internal(["plan", "discovery", *args]))

    @pm.command("refine")
    def pm_refine(target: str, from_: Annotated[list[str] | None, typer.Option("--from", help="Insumos")] = None) -> None:
        """Enriquece a spec e gera dúvidas. Com a prontidão em 100%, gera as histórias em rascunho."""
        from nexo.kernel.workspace import specs

        context = ctx()
        demands = core.load(context)
        inputs = list(from_ or [])
        if not target.startswith("F-"):
            raise typer.Exit(internal(["plan", target, *inputs]))
        d = demands.get(target)
        folder = specs.feature_dir(core.root_of(context), target)
        if not (folder / "spec.yaml").is_file():
            from nexo.modules.planning.app.plan import Planner

            title = d.title if d else target
            Planner(context).new([*inputs, title] if inputs else [title], title=title, fid=target)
            code = 0
        else:
            code = internal(["plan", "feature", target, *inputs])
        raise typer.Exit(code)

    @pm.command("approve")
    def pm_approve(fid: str) -> None:
        """Aprova os requisitos. Gera o rascunho das histórias se ainda não existir."""
        from nexo.kernel.workspace import specs

        folder = specs.feature_dir(core.root_of(ctx()), fid)
        if not (folder / "plan.draft.yaml").is_file():
            code = internal(["plan", "package", fid])
            if code:
                raise typer.Exit(code)
        raise typer.Exit(internal(["plan", "approve", fid, "--part", "requisitos"]))

    @pm.command("accept")
    def pm_accept(story: str) -> None:
        """Aceite de negócio depois da entrega."""
        show(flow.accept(ctx(), story), f"{story}: aceita")

    @pm.command("confirm")
    def pm_confirm(pid: str) -> None:
        """Confirma uma premissa provisória."""
        show(proposals.confirm(ctx(), pid, True), f"{pid}: confirmada")

    @pm.command("decline")
    def pm_decline(pid: str, reason: Reason = "") -> None:
        """Recusa uma premissa provisória: o que dependia dela é reaberto."""
        data = proposals.confirm(ctx(), pid, False, reason)
        show(data, f"{pid}: recusada · reabre: {', '.join(data['reopened']) or 'nada'}")

    # ---------------------------------------------------------------- QA
    @qa.command("plan")
    def qa_plan(
        story: str, scenario: Annotated[list[str] | None, typer.Option("--scenario", help="AC-1=texto")] = None
    ) -> None:
        """Matriz critério → cenário → tipo de teste."""
        data = flow.qa_plan(ctx(), story, _pairs(scenario))
        rows = "\n".join(f"  {r['ac']}: {r.get('cenario', '')[:80]}" for r in data["rows"])
        show(data, f"{story}: plano em {data['file']}\n{rows}\n  próximo: nexo qa approve {story}")

    @qa.command("approve")
    def qa_approve(story: str) -> None:
        """Aprova o plano de teste da história."""
        data = flow.approve_story(ctx(), story, "testes")
        show(data, _approved_text(data))

    @qa.command("reject")
    def qa_reject(story: str, reason: Reason = "") -> None:
        """Reprova o plano de teste."""
        show(flow.reject_story(ctx(), story, "testes", reason), f"{story}: plano de teste reprovado")

    @qa.command("trace")
    def qa_trace(story: str) -> None:
        """Critério → teste → resultado, com o que falta."""
        raise typer.Exit(internal(["check", story]))

    @qa.command("bug")
    def qa_bug(
        story: str, text: str, evidence: Annotated[str, typer.Option("--evidence", help="Arquivo ou texto")] = ""
    ) -> None:
        """Registra bug ligado à história e devolve ao dev."""
        core.require_role(ctx(), "qa", "Registrar um bug")
        data = flow.new_bug(ctx(), story, text, evidence)
        show(data, f"{data['id']}: ligado a {story}\n  o dev corrige com: nexo dev run {data['id']}")

    @qa.command("pass")
    def qa_pass(story: str) -> None:
        """Valida a entrega e libera o portão do tech lead."""
        core.require_role(ctx(), "qa", "Validar a entrega")
        d, _ = core.get(ctx(), story)
        core.record(ctx(), "demand.validated", d.id, {"passed": True})
        show({"story": d.id, "validated": True}, f"{d.id}: validada; segue para o tech lead")

    @qa.command("verify")
    def qa_verify(bug: str) -> None:
        """Roda de novo e verifica a correção antes de fechar o bug."""
        core.require_role(ctx(), "qa", "Verificar a correção")
        code = internal(["quality", "run"])
        if code:
            raise typer.Exit(code)
        d, _ = core.get(ctx(), bug)
        core.record(ctx(), "demand.state", d.id, {"state": "Concluido"}, push=False)
        core.record(ctx(), "demand.verified", d.id, {"passed": True})
        show({"bug": d.id, "verified": True}, f"{d.id}: verificado e fechado")

    @qa.command("flaky")
    def qa_flaky() -> None:
        """Testes instáveis registrados no pipeline."""
        rows = ctx().events.query("quality.flaky") if hasattr(ctx().events, "query") else []
        emit([dict(r.payload) for r in rows])

    # ---------------------------------------------------------------- tech lead
    @tl.command("setup")
    def tl_setup() -> None:
        """Prepara o repositório: mapa do código e grafo. Não cria workflow: lê o pipeline existente."""
        code = internal(["init"]) if ctx().project_root is None else 0
        code = code or internal(["discover"])
        raise typer.Exit(code or internal(["graph", "build"]))

    @tl.command("plan")
    def tl_plan(fid: str, feedback: Annotated[str, typer.Option("--feedback", help="O que ajustar")] = "") -> None:
        """Primeira quebra em tarefas e o que roda em paralelo."""
        args = ["plan", "package", fid]
        if feedback:
            args += ["--feedback", feedback]
        code = internal(args)
        raise typer.Exit(code or internal(["plan", "graph", fid]))

    @tl.command("assign")
    def tl_assign(
        target: str,
        dev_: Annotated[str, typer.Option("--dev", help="Pessoa, ex.: gh:dani")] = "",
        qa_: Annotated[str, typer.Option("--qa", help="Pessoa, ex.: gh:caio")] = "",
        queue: Annotated[bool, typer.Option("--queue", help="Deixa na fila do papel")] = False,
        suggest_: Annotated[bool, typer.Option("--suggest", help="Sugere pelo dono do código e pela carga")] = False,
    ) -> None:
        """Define dev e QA de cada história."""
        if suggest_:
            rows = flow.suggest_assignees(ctx(), target)
            text = "\n".join(f"{r['story']}  dev: {r['dev'] or '—'}   qa: {r['qa'] or '—'}" for r in rows)
            show(rows, text or "sem histórias")
            return
        if not (dev_ or qa_ or queue):
            raise NexoError("informe --dev e --qa, --queue ou --suggest")
        data = flow.assign(ctx(), target, dev_, qa_, queue)
        show(data, f"{', '.join(data['stories'])}: dev {data['dev'] or '—'} · qa {data['qa'] or '—'}")

    @tl.command("reassign")
    def tl_reassign(
        story: str,
        reason: Reason = "",
        dev_: Annotated[str, typer.Option("--dev")] = "",
        qa_: Annotated[str, typer.Option("--qa")] = "",
    ) -> None:
        """Troca o responsável, com motivo na trilha."""
        role, person = ("dev", dev_) if dev_ else ("qa", qa_)
        if not person:
            raise NexoError("informe --dev ou --qa")
        data = flow.reassign(ctx(), story, role, person, reason)
        show(data, f"{story}: {role} agora é {person} (antes {data['from'] or '—'})")

    @tl.command("assume")
    def tl_assume(pid: str, reason: Reason = "") -> None:
        """Libera continuar com premissa provisória; o merge fica retido até o dono decidir."""
        show(proposals.assume(ctx(), pid, reason), f"{pid}: premissa aberta; o PR não recebe merge até o dono decidir")

    @tl.command("idea")
    def tl_idea(text: str, tech: Annotated[bool, typer.Option("--tech", help="Sem impacto funcional")] = True) -> None:
        """Melhoria técnica sem impacto funcional; o aceite é do tech lead."""
        data = flow.tech_demand(ctx(), text)
        show(data, f"{data['id']}: melhoria técnica registrada\n  próximo: nexo tl analyze {data['id']}")

    @tl.command("pass")
    def tl_pass(story: str) -> None:
        """Aprova o portão: exige validação do QA, spec congelada atual e nenhuma premissa aberta."""
        core.require_role(ctx(), "tl", "Decidir o portão")
        demands = core.load(ctx())
        d = demands.get(story)
        if d is not None and d.type == "story":
            team = core.team(ctx())
            if team.declared and not d.qa_validated:
                raise PolicyViolation(f"{story} ainda não foi validada pelo QA (`nexo qa pass {story}`)")
            if d.lock:
                problems = proposals.lock_current(ctx(), story)
                if problems:
                    raise PolicyViolation("; ".join(problems))
        raise typer.Exit(internal(["approve", story]))

    # ---------------------------------------------------------------- dev
    @dev.command("take")
    def dev_take(story: str, force: Annotated[bool, typer.Option("--force")] = False) -> None:
        """Assume uma história da fila."""
        show(flow.take(ctx(), story, "dev", force), f"{story}: agora é sua")

    @dev.command("refine")
    def dev_refine(
        story: str,
        add_task: Annotated[list[str] | None, typer.Option("--add-task", help="Título da nova tarefa")] = None,
        propose: Annotated[list[str] | None, typer.Option("--propose", help="seção=texto, ex.: tecnica=trocar a fila")] = None,
        blocking: Annotated[bool, typer.Option("--blocking", help="As propostas travam a história")] = False,
    ) -> None:
        """Melhora o refinamento: tarefas são suas; o resto vira proposta para o dono."""
        core.require_role(ctx(), "dev", "Refinar a história")
        d, demands = core.get(ctx(), story)
        lines = []
        for title in add_task or []:
            n = len([x for x in demands.values() if x.parent == d.id and x.type == "task"]) + len(lines) + 1
            tid = f"T-{d.id.removeprefix('US-')}-{n}"
            core.record(
                ctx(),
                "demand.created",
                tid,
                {"type": "task", "title": core.clean(title)[:200], "parent": d.id, "project": d.project},
                push=False,
            )
            lines.append(f"tarefa {tid}: {title}")
        for section, text in _pairs(propose).items():
            opened = proposals.suggest(ctx(), d.id, section, text, blocking or section in ("funcional", "tecnica"))
            lines.append(f"proposta {opened['id']} para {m.ROLE_LABEL[opened['owner']]}: {text}")
        core.store(ctx()).push(f"nexo: refinamento de {d.id}")
        show({"story": d.id, "changes": lines}, "\n".join(lines) or f"{d.id}: sem mudanças\n  próximo: nexo dev approve {d.id}")

    @dev.command("approve")
    def dev_approve(story: str) -> None:
        """Aprova o plano da história."""
        data = flow.approve_story(ctx(), story, "plano")
        show(data, _approved_text(data))

    @dev.command("reject")
    def dev_reject(story: str, reason: Reason = "") -> None:
        """Reprova o plano da história."""
        show(flow.reject_story(ctx(), story, "plano", reason), f"{story}: plano reprovado")

    @dev.command("run")
    def dev_run(
        target: str,
        parallel: Annotated[int, typer.Option("--parallel", help="Histórias em paralelo")] = 1,
        force: Annotated[bool, typer.Option("--force", help="Rodar história de outro dev (avisa o TL)")] = False,
    ) -> None:
        """Executa com o agente até o portão."""
        if target.startswith("US-"):
            prepared = proposals.prepare_run(ctx(), target, force)
            if prepared.get("changed"):
                console.print(f"{target}: spec congelada v{prepared['version']}", markup=False)
            raise typer.Exit(internal(["run", "story", target]))
        if target.startswith("F-"):
            raise typer.Exit(internal(["run", "feature", target, "--parallel", str(parallel)]))
        raise typer.Exit(internal(["run", "task", target]))

    @dev.command("resume")
    def dev_resume(target: str) -> None:
        """Retoma depois de cota, dúvida, orçamento ou mudança na spec. Com `--dry-run`, só mostra o que seria refeito."""
        dry_run = ctx().options.dry_run
        known = target in core.load(ctx())
        if known:
            plan = proposals.resume_plan(ctx(), target)
            if dry_run:
                lines = [f"refazer: {', '.join(plan['redo']) or 'nada'}", f"manter: {', '.join(plan['done_kept']) or 'nada'}"]
                lines += [f"mudou: [{c['section']}] {c['text']}" for c in plan["what_changed"]]
                show(plan, "\n".join(lines))
                return
            if plan["blocked_by"]:
                raise PolicyViolation(f"{target} aguarda decisão: {', '.join(plan['blocked_by'])}")
            proposals.freeze(ctx(), target)
        raise typer.Exit(internal(["resume", target]))

    def problem_cmd(role: str) -> Any:
        def run(
            story: str,
            kind: Annotated[str, typer.Option("--kind", help="task, technical, functional, test ou other")] = "",
            text: Annotated[str, typer.Option("--text", help="O que foi encontrado")] = "",
            evidence: Annotated[str, typer.Option("--evidence")] = "",
            ac: Annotated[list[str] | None, typer.Option("--ac", help="Critério afetado")] = None,
            file: Annotated[list[str] | None, typer.Option("--file", help="Arquivo afetado")] = None,
            ref: Annotated[list[str] | None, typer.Option("--ref", help="Trecho técnico afetado")] = None,
        ) -> None:
            """Triagem guiada: o que está errado define quem decide."""
            _problem(story, kind, text, evidence, ac, file, ref)

        run.__name__ = f"{role}_problem"
        return run

    def _problem(
        story: str, kind: str, text: str, evidence: str, ac: list[str] | None, file: list[str] | None, ref: list[str] | None
    ) -> None:
        if not kind:
            options = [
                ("task", "A tarefa está errada ou faltando"),
                ("technical", "A solução técnica não funciona"),
                ("functional", "A regra de negócio está errada ou incompleta"),
                ("test", "Um teste está errado"),
                ("other", "Outro problema, fora desta história"),
            ]
            console.print("O que você encontrou?")
            for i, (_, label) in enumerate(options, 1):
                console.print(f"  {i}. {label}", markup=False)
            kind = options[int(typer.prompt("›")) - 1][0]
            text = text or typer.prompt("Descreva")
            evidence = evidence or typer.prompt("Evidência (opcional, Enter para pular)", default="", show_default=False)
        data = proposals.problem(ctx(), story, kind, text, evidence, ac, ref, file)
        owner = m.ROLE_LABEL.get(str(data.get("owner")), "")
        lines = [f"Vai para {owner}." if owner else "Registrado."]
        if data.get("affected"):
            lines.append("Afetadas: " + ", ".join(data["affected"]))
        if data.get("continue"):
            lines.append("Continuam: " + ", ".join(data["continue"]))
        if data.get("kept"):
            lines.append("Prontas e mantidas: " + ", ".join(data["kept"]))
        if data.get("uncertain"):
            lines.append("Impacto incerto: a história inteira para. " + "; ".join(data.get("reasons") or []))
        if data.get("proposal"):
            lines.append(f"Proposta: {data['proposal']}")
        show(data, "\n".join(lines))

    dev.command("problem")(problem_cmd("dev"))
    qa.command("problem")(problem_cmd("qa"))
    tl.command("problem")(problem_cmd("tl"))

    # ---------------------------------------------------------------- todos
    @root.command("board")
    def board_cmd(
        mine: Annotated[bool, typer.Option("--mine")] = False,
        role: Annotated[str, typer.Option("--role")] = "",
        state: Annotated[str, typer.Option("--state")] = "",
        blocked: Annotated[bool, typer.Option("--blocked")] = False,
        origin: Annotated[str, typer.Option("--origin")] = "",
        triage: Annotated[bool, typer.Option("--triage")] = False,
        all_: Annotated[bool, typer.Option("--all", help="Inclui as concluídas")] = False,
    ) -> None:
        """Demandas de todos os projetos e com quem estão."""
        rows = views.board(ctx(), mine, role, state, blocked, origin, triage, all_)
        if ctx().options.json:
            emit(rows)
            return
        table = Table(title_justify="left")
        for col in ("PROJETO", "DEMANDA", "TIPO", "ETAPA", "COM", "IDADE", "ORIGEM"):
            table.add_column(col)
        for r in rows:
            table.add_row(r["project"], r["id"], r["type"], r["stage"], ", ".join(r["with"]), r["age"], r["origin"])
        console.print(table)
        console.print(f"{len(rows)} demanda(s) · {sum(1 for r in rows if r['blocked_by'])} bloqueada(s)")

    @root.command("brief")
    def brief_cmd(did: str) -> None:
        """O que mudou e o que esperam de você nesta demanda."""
        data = views.brief(ctx(), did)
        if ctx().options.json:
            emit(data)
            return
        lines = [
            f"{data['id']}: {data['title']}",
            f"Etapa {data['stage']}, há {data['age']}" + (f", enviada por {data['from']}." if data["from"] else "."),
            "",
            "O que esperam de você" if data["for_you"] else "Quem precisa agir",
        ]
        lines += [f"  {x}" for x in data["expected"]] or ["  nada"]
        if data["decided"]:
            lines += ["", "O que foi decidido", *[f"  • {x}" for x in data["decided"]]]
        if data["open"]:
            lines += ["", "Em aberto", *[f"  {x}" for x in data["open"]]]
        if data["next"]:
            lines += ["", f"Próximo comando: {data['next']}"]
        console.print("\n".join(lines), markup=False)

    demand = typer.Typer(help="Detalhes das demandas", no_args_is_help=True)
    root.add_typer(demand, name="demand")

    @demand.command("list")
    def demand_list() -> None:
        """Todas as demandas abertas."""
        emit(views.board(ctx()))

    @demand.command("show")
    def demand_show(did: str) -> None:
        """Etapa, responsáveis e links."""
        d, demands = core.get(ctx(), did)
        emit(
            {
                "id": d.id,
                "type": d.type,
                "title": d.title,
                "stage": m.stage_of(d, demands),
                "state": d.state,
                "holders": m.holders_of(d, demands),
                "responsible": d.responsible,
                "origin": d.origin,
                "parent": d.parent,
                "spawned_by": d.spawned_by,
                "links": d.links,
                "lock": d.lock,
                "created_at": d.created_at,
            }
        )

    @demand.command("tree")
    def demand_tree(did: str) -> None:
        """Ideia → features → histórias → tarefas e bugs."""
        data = views.tree(ctx(), did)
        if ctx().options.json:
            emit(data)
            return

        def walk(node: dict[str, Any], depth: int) -> None:
            people = f" · {', '.join(node['with'])}" if node["with"] else ""
            console.print(f"{'  ' * depth}{node['id']}  {node['title'][:50]}  [{node['stage']}]{people}", markup=False)
            for kid in node["children"]:
                walk(kid, depth + 1)

        walk(data, 0)

    @demand.command("why")
    def demand_why(did: str) -> None:
        """Com quem está, por quê e o que falta."""
        data = views.why(ctx(), did)
        show(data, data["text"])

    @demand.command("verify")
    def demand_verify() -> None:
        """Refaz a projeção a partir dos eventos e confere que nenhuma demanda ficou sem dono."""
        data = views.verify(ctx())
        emit(data)
        if not data["ok"]:
            raise typer.Exit(ExitCode.ERROR)

    @demand.command("cancel")
    def demand_cancel(did: str, reason: Reason = "") -> None:
        """Cancela a demanda."""
        if not reason.strip():
            raise NexoError("cancelar exige --reason")
        core.record(ctx(), "demand.cancelled", did, {"reason": core.clean(reason)})
        console.print(f"{did}: cancelada", markup=False)

    @demand.command("link")
    def demand_link(did: str, kind: str, value: str) -> None:
        """Liga a demanda a um PR, commit ou link externo."""
        core.record(ctx(), "demand.linked", did, {"links": {kind: value}})
        console.print(f"{did}: {kind} = {value}", markup=False)

    @demand.command("triage")
    def demand_triage(
        did: str, accept: Annotated[bool, typer.Option("--accept/--decline")] = True, reason: Reason = ""
    ) -> None:
        """Aceita ou descarta um achado gerado por IA."""
        emit(flow.triage(ctx(), did, accept, reason))

    props = typer.Typer(help="Propostas abertas", no_args_is_help=False)
    root.add_typer(props, name="proposals")

    @props.callback(invoke_without_command=True)
    def props_list(context: typer.Context, mine: Annotated[bool, typer.Option("--mine")] = False) -> None:
        if context.invoked_subcommand is not None:
            return
        rows = proposals.listing(ctx(), mine)
        text = "\n".join(
            f"{r['id']}  {r['demand']}  {r['section']}  {'bloqueante ' if r['blocking'] else ''}{r['status']}: {r['text'][:70]}"
            for r in rows
        )
        show(rows, text or "nenhuma proposta aberta")

    @props.command("accept")
    def props_accept(pid: str) -> None:
        """Aceita: a seção muda e só a aprovação dela é refeita."""
        data = proposals.decide(ctx(), pid, True)
        show(data, f"{pid}: aceita · reaprovar: {', '.join(data['invalidated']) or 'nada'}")

    @props.command("decline")
    def props_decline(pid: str, reason: Reason = "") -> None:
        """Recusa: o motivo volta para quem propôs."""
        show(proposals.decide(ctx(), pid, False, reason), f"{pid}: recusada")

    @root.command("suggest")
    def suggest_cmd(
        did: str,
        text: str,
        section: Annotated[str, typer.Option("--section", help="funcional, tecnica, testes ou implementacao")],
        blocking: Annotated[bool, typer.Option("--blocking")] = False,
    ) -> None:
        """Propõe mudança na seção de outro papel."""
        data = proposals.suggest(ctx(), did, section, text, blocking)
        show(data, f"{data['id']}: proposta para {m.ROLE_LABEL[data['owner']]}")

    @root.command("return")
    def return_cmd(did: str, to: Annotated[str, typer.Option("--to")], reason: Reason = "") -> None:
        """Devolve a demanda com a pergunta."""
        show(flow.return_to(ctx(), did, to, reason), f"{did}: devolvida para {m.ROLE_LABEL.get(to, to)}")

    @root.command("problem")
    def problem_root(
        words: Annotated[list[str] | None, typer.Argument(help="<US-n>, ou: reclassify <P-n> --kind <tipo>")] = None,
        kind: Annotated[str, typer.Option("--kind")] = "",
        text: Annotated[str, typer.Option("--text")] = "",
    ) -> None:
        """Registra um problema e indica o dono."""
        args = list(words or [])
        if args[:1] == ["reclassify"] and len(args) > 1:
            data = proposals.reclassify(ctx(), args[1], kind)
            show(data, f"{args[1]} → {data['to']}, com {m.ROLE_LABEL[data['owner']]}")
            return
        if not args:
            raise NexoError("use `nexo problem <US-n>`")
        _problem(args[0], kind, text, "", None, None, None)

    meeting = typer.Typer(help="Reunião de refinamento", no_args_is_help=True)
    root.add_typer(meeting, name="meeting")

    @meeting.command("start")
    def meeting_start(
        did: str,
        type_: Annotated[str, typer.Option("--type", help="funcional ou tecnico")],
        with_: Annotated[str, typer.Option("--with", help="Pessoas, separadas por vírgula")] = "",
    ) -> None:
        """Abre a reunião e registra quem está presente."""
        data = meetings.start(ctx(), did, type_, [p.strip() for p in with_.split(",") if p.strip()])
        show(data, f"{data['id']} aberta para {data['demand']}: {', '.join(data['participants'])}")

    @meeting.command("note")
    def meeting_note(text: str) -> None:
        """Registra uma nota. Use prefixos: `critério:`, `técnico:`, `teste:`, `tarefa:`."""
        show(meetings.note(ctx(), text), "nota registrada")

    @meeting.command("import")
    def meeting_import(source: str) -> None:
        """Transcrição, ata, PDF, áudio ou vídeo vira texto redigido; o original não vai para o Git."""
        emit(meetings.import_file(ctx(), source))

    @meeting.command("close")
    def meeting_close(ai: Annotated[bool, typer.Option("--ai", help="A IA separa também as transcrições")] = False) -> None:
        """Separa o que foi dito por seção. Nada entra na spec e nada é aprovado."""
        data = meetings.close(ctx(), use_ai=ai)
        text = "\n".join(f"  {i['id']} [{i['section']}] {i['text']}" for i in data["items"])
        show

    @meeting.command("review")
    def meeting_review(
        mid: str,
        accept: Annotated[list[str] | None, typer.Option("--accept")] = None,
        decline: Annotated[list[str] | None, typer.Option("--decline")] = None,
    ) -> None:
        """Cada dono aceita ou recusa o que é da própria seção."""
        data = meetings.review(ctx(), mid, accept or [], decline or [])
        show(data, f"{mid}: {len(data['decided'])} decidido(s), {len(data['pending'])} pendente(s)")

    @root.command("role")
    def role_cmd() -> None:
        """Mostra seus papéis no time."""
        me = core.who(ctx())
        t = core.team(ctx())
        data = {"who": me, "mode": t.mode, "roles": t.roles_of(me) if t.declared else list(m.ROLE_LABEL)}
        show(data, f"{me}: {', '.join(data['roles']) or 'sem papel'} (modo {t.mode})")

    @root.command("join")
    def join_cmd(
        team: str,
        repo: Annotated[str, typer.Option("--repo", help="Endereço do repositório do time")] = "",
        project: Annotated[str, typer.Option("--project", help="Projeto dentro do time")] = "",
    ) -> None:
        """Entra no time sem precisar saber Git."""
        from nexo.modules.demands.app import membership

        data = membership.join(ctx(), team, repo, project)
        show(data, f"Você entrou no time {team}. Projetos: {', '.join(data['projects']) or 'nenhum'}")

    def _approved_text(data: dict[str, Any]) -> str:
        text = f"{data['story']}: {data['part']} aprovado por {data['by']}"
        if data.get("ready"):
            return text + "\nDefinition of Ready passou: a história está pronta."
        if data.get("problems"):
            return text + "\nFalta para ficar pronta: " + "; ".join(str(p) for p in data["problems"])
        return text
