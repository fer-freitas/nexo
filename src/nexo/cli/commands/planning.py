"""Comandos de planejamento.

plano, sessão conjunta, DoR, performance, discovery, templates e estimativa (modernização e incidente em
`legacy.py`).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any

import typer
from rich.markup import escape
from rich.table import Table

from nexo.cli import look
from nexo.cli.app import app, console, ctx, emit, err_console
from nexo.cli.shared import _ui
from nexo.kernel.errors import NexoError
from nexo.kernel.finops.money import brl


@app.command("ready")
def ready_cmd(
    target: Annotated[str, typer.Argument(help="História (US-n) ou feature (F-n)")],
    init: Annotated[bool, typer.Option("--init", help="Cria o refinamento no modelo único")] = False,
) -> None:
    """Definition of Ready: o refinamento segue o modelo único, com critérios de teste e de performance."""
    from nexo.modules.planning.app import ready as planning_ready

    if init:
        path = planning_ready.init(ctx(), target)
        console.print(f"Refinamento criado: {path}. Preencha e rode `nexo ready {target}`.")
        return
    results = planning_ready.check_many(ctx(), target)
    if ctx().options.json:
        emit(results)
    else:
        for r in results:
            mark = "[nexo.good]pronta[/]" if r["ready"] else "[nexo.bad]não pronta[/]"
            console.print(f"{r['story']}: {mark} · {r['refinement']}")
            for item in r["problems"]:
                console.print(f"  ✗ {item}", markup=False, highlight=False)
            for item in r["warnings"]:
                console.print(f"  ! {item}", markup=False, highlight=False)
    if not all(r["ready"] for r in results):
        raise typer.Exit(1)


perf_app = typer.Typer(help="Teste de performance pelos limites do refinamento", no_args_is_help=True)


app.add_typer(perf_app, name="perf", hidden=True)


@perf_app.command("script")
def perf_script_cmd(
    story: Annotated[str, typer.Argument(help="História (US-n)")],
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Gera tests/load/<história>.js (k6) com os thresholds iguais aos limites do refinamento."""
    from nexo.modules.planning.app import ready as planning_ready

    path = planning_ready.write_script(ctx(), story, force)
    console.print(f"Script de carga: {path} (complete a requisição do cenário; não mude os thresholds)")


@perf_app.command("check")
def perf_check_cmd(
    story: Annotated[str, typer.Argument(help="História (US-n)")],
    summary: Annotated[Path, typer.Option("--summary", help="Resultado do k6 (k6 run --summary-export)")],
) -> None:
    """Compara o resultado do teste de carga com os limites do refinamento; reprova como qualquer teste."""
    from nexo.modules.planning.app import ready as planning_ready

    result = planning_ready.check_result(ctx(), story, summary)
    if ctx().options.json:
        emit(result)
    else:
        m = result["measured"]
        mark = "[nexo.good]aprovado[/]" if result["passed"] else "[nexo.bad]reprovado[/]"
        console.print(
            f"{story}: teste de performance {mark} · p95 {m['p95_ms']} ms · p99 {m['p99_ms']} ms"
            f" · erro {m['erro_max_pct']}%"
        )
        for item in result["problems"]:
            console.print(f"  ✗ {item}", markup=False, highlight=False)
    if not result["passed"]:
        raise typer.Exit(1)


class DefaultGroup(typer.core.TyperGroup):
    """`nexo plan <entradas>` cai no subcomando oculto `new` quando o 1º argumento não é subcomando."""

    def parse_args(self, ctx: Any, args: list[str]) -> list[str]:
        if args and args[0] not in self.commands and args[0] not in ("--help", "-h"):
            args = ["new", *args]
        return list(super().parse_args(ctx, args))


plan_app = typer.Typer(help="Planejamento", no_args_is_help=True, cls=DefaultGroup)


app.add_typer(plan_app, name="plan")


WorkspaceOpt = Annotated[str | None, typer.Option("--workspace", help="Workspace quando fora de um projeto")]


def _planner(workspace: str | None) -> Any:
    from nexo.modules.planning.app.plan import Planner

    return Planner(ctx(), workspace, _ui)


def _parse_answers(items: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items or []:
        qid, sep, value = item.partition("=")
        if not sep or not qid.strip():
            raise NexoError(f"resposta inválida '{item}': use Q-1=texto ou Q-1=caminho/arquivo")
        out[qid.strip()] = value.strip().removeprefix("@")
    return out


def _print_plan(report: Any) -> None:
    if ctx().options.json:
        emit(report.__dict__)
        return
    where = f" · workspace {report.workspace}" if report.workspace else ""
    color = "nexo.good" if report.ready else ("nexo.warn" if report.score >= 60 else "nexo.bad")
    bar = "█" * (report.score // 5) + "░" * (20 - report.score // 5)
    state = f" · {report.state}" if report.state else ""
    console.print(f"\n[nexo.title]{report.feature}[/] — {report.title}{where}{state}")
    console.print(
        f"  prontidão [{color}]{bar} {report.score}%[/]"
        + ("  [nexo.good]pronta para aprovar[/]" if report.ready and not report.backlog else "")
    )
    if report.ingested:
        console.print(
            "  insumos: "
            + ", ".join(
                f"{i['kind']}" + (f" ({i['redactions']} redação)" if i["redactions"] else "") for i in report.ingested
            )
        )
        if any(i["redactions"] for i in report.ingested):
            console.print(look.REDACTION_HINT)
    if report.changed:
        console.print(f"  seções atualizadas: {', '.join(report.changed)}")
    for item in report.missing:
        console.print(f"  [nexo.muted]○ {item['label']} ({item['weight']}) — {item['hint']}[/]")
    if report.questions:
        table = Table(title=f"{len(report.questions)} dúvida(s) em aberto", title_justify="left")
        for col in ("id", "gravidade", "seção", "pergunta"):
            table.add_column(col)
        sev = {"blocking": "[nexo.bad]bloqueante[/]", "important": "[nexo.warn]importante[/]", "optional": "opcional"}
        for qd in report.questions:
            table.add_row(qd["id"], sev.get(qd["severity"], qd["severity"]), qd["section"], qd["text"])
        console.print(table)
    if report.backlog:
        b = report.backlog
        console.print(f"  backlog: {len(b['stories'])} história(s), {len(b['tasks'])} tarefa(s)")
    if report.waves:
        for i, wave in enumerate(report.waves, 1):
            console.print(f"  onda {i}: {', '.join(wave)}" + ("  [nexo.muted](paralelo)[/]" if len(wave) > 1 else ""))
    if report.backlog:
        nxt = "run story <US-id>"
    elif report.ready:
        nxt = f"plan approve {report.feature}"
    elif report.questions:
        nxt = f"plan answer {report.feature}"
    else:
        nxt = f"plan feature {report.feature} <insumos>"
    console.print(f"  próximo passo: [nexo.accent]{escape(look.cmd(nxt))}[/]")
    from nexo.cli.commands.context import print_impact

    print_impact(report.feature)


@plan_app.command("new", hidden=True)
def plan_new_cmd(
    inputs: Annotated[list[str], typer.Argument(help="Arquivos, link Figma ou texto")],
    title: Annotated[str | None, typer.Option("--title")] = None,
    workspace: WorkspaceOpt = None,
) -> None:
    _print_plan(_planner(workspace).new(inputs, title))


@plan_app.command("feature")
def plan_feature_cmd(
    feature: str,
    inputs: Annotated[list[str] | None, typer.Argument()] = None,
    answer: Annotated[list[str] | None, typer.Option("--answer", "-a", help="Q-1=texto (repetível)")] = None,
    workspace: WorkspaceOpt = None,
) -> None:
    _print_plan(_planner(workspace).feature(feature, inputs or [], _parse_answers(answer)))


@plan_app.command("answer")
def plan_answer_cmd(
    feature: str,
    answers: Annotated[list[str] | None, typer.Argument(help="Q-1=texto ou Q-2=arquivo")] = None,
    file: Annotated[Path | None, typer.Option("--file", help="YAML {Q-1: resposta}")] = None,
    no_refine: Annotated[bool, typer.Option("--no-refine")] = False,
    workspace: WorkspaceOpt = None,
) -> None:
    from nexo.modules.planning.app.plan import load_answers_file

    planner = _planner(workspace)
    parsed = _parse_answers(answers)
    if file is not None:
        parsed.update(load_answers_file(file))
    if not parsed:
        if ctx().options.json:
            raise NexoError("informe respostas (Q-1=texto) ou --file no modo --json")
        for qd in planner.status(feature).questions:
            value = typer.prompt(
                f"{qd['id']} ({qd['severity']}) {qd['text']}\n  resposta (vazio = pular)",
                default="",
                show_default=False,
            )
            if value.strip():
                parsed[qd["id"]] = value.strip()
    _print_plan(planner.answer(feature, parsed, refine=not no_refine))


@plan_app.command("status")
def plan_status_cmd(feature: str, workspace: WorkspaceOpt = None) -> None:
    _print_plan(_planner(workspace).status(feature))


@plan_app.command("approve")
def plan_approve_cmd(
    feature: str,
    part: Annotated[
        str | None, typer.Option("--part", help="Fluxo por papéis: requisitos | design | plano (a parte do seu papel)")
    ] = None,
    force: Annotated[bool, typer.Option("--force")] = False,
    reason: Annotated[str, typer.Option("--reason")] = "",
    workspace: WorkspaceOpt = None,
) -> None:
    if part is not None:
        from nexo.modules.planning.app import roleflow

        _print_roles(roleflow.approve_part(_planner(workspace), feature, part))
        return
    _print_plan(_planner(workspace).approve(feature, force, reason))


ROLE_NAMES = {"pm": "PM", "tech_lead": "tech lead", "dev": "dev"}


PART_STYLE = {
    "aprovada": "nexo.good",
    "pendente": "nexo.warn",
    "desatualizada": "nexo.warn",
    "rejeitada": "nexo.bad",
}


def _print_roles(view: dict[str, Any]) -> None:
    if ctx().options.json:
        emit(view)
        return
    if view.get("plan"):
        _print_plan(SimpleNamespace(**view["plan"]))
    console.print(f"\n[nexo.title]{view['feature']}[/] · fluxo por papéis · {view.get('state') or ''}")
    if view.get("joint_reasons"):
        done = "[nexo.good]feita[/]" if not view["joint_pending"] else "[nexo.bad]pendente[/]"
        console.print(f"  sessão conjunta {done}: {'; '.join(view['joint_reasons'])}")
    if view.get("open_session"):
        console.print(f"  sessão aberta: {view['open_session']}")
    if not view.get("packaged"):
        console.print("  pacote: [nexo.warn]ainda não gerado[/] (encerre a sessão ou rode nexo plan package)")
    for p in view.get("parts", []):
        style = PART_STYLE.get(p["status"], "white")
        who = f" por {p['by']}" if p["by"] else ""
        extra = f" — {p['detail']}" if p["detail"] else ""
        can = f"  [nexo.muted]({', '.join(p['who_can'])})[/]" if p["status"] != "aprovada" and p["who_can"] else ""
        console.print(f"  [{style}]{p['status']:<13}[/] {p['part']:<11} {ROLE_NAMES[p['role']]}{who}{extra}{can}")


@plan_app.command("discovery")
def plan_discovery_cmd(
    inputs: Annotated[list[str] | None, typer.Argument(help="Pesquisas, dados, entrevistas ou texto")] = None,
    feature: Annotated[str | None, typer.Option("--feature", help="Enriquecer o discovery de uma feature")] = None,
    title: Annotated[str | None, typer.Option("--title")] = None,
    workspace: WorkspaceOpt = None,
) -> None:
    """Discovery do PM: problema, evidências, hipóteses e experimentos até a decisão de construir."""
    from nexo.modules.planning.app import roleflow

    planner = _planner(workspace)
    if not inputs and feature:
        view = roleflow.discovery_status(planner, feature)
    else:
        view = roleflow.discovery(planner, inputs or [], feature, title)
    if ctx().options.json:
        emit(view)
        return
    console.print(f"\n[nexo.title]{view['feature']}[/] — {view['title']} · discovery")
    for h in view["hypotheses"]:
        console.print(
            f"  {h['id']}: {h['statement']}  [nexo.muted]métrica {h['metric'] or '?'} · meta {h['target'] or '?'}[/]"
        )
    for q in view["open_questions"]:
        console.print(f"  [nexo.warn]?[/] {q}")
    if view["missing"]:
        console.print(f"  falta: {', '.join(view['missing'])}")
    console.print(f"  recomendação do agente: [nexo.title]{view['recommendation'] or '—'}[/]")
    console.print(
        f"  próximo passo: [nexo.accent]{look.prefix()}plan decide {view['feature']} "
        "build|drop|investigate --reason ...[/]"
    )


@plan_app.command("decide")
def plan_decide_cmd(
    feature: str,
    decision: Annotated[str, typer.Argument(help="build | drop | investigate")],
    reason: Annotated[str, typer.Option("--reason")] = "",
    force: Annotated[bool, typer.Option("--force", help="Construir mesmo com lacunas no discovery")] = False,
    workspace: WorkspaceOpt = None,
) -> None:
    """Decisão do PM sobre o discovery; `build` inicia o refinamento com a IA usando o discovery como insumo."""
    from nexo.modules.planning.app import roleflow

    out = roleflow.decide(_planner(workspace), feature, decision, reason, force)
    if ctx().options.json:
        emit(out)
        return
    console.print(f"{feature}: decisão [nexo.title]{decision}[/] · estado {out['state']}")
    if out.get("plan"):
        _print_plan(SimpleNamespace(**out["plan"]))


session_app = typer.Typer(help="Sessão conjunta PM + tech lead + dev com a IA", no_args_is_help=True)


plan_app.add_typer(session_app, name="session")


@session_app.command("start")
def plan_session_start_cmd(
    feature: str,
    pm: Annotated[list[str] | None, typer.Option("--pm")] = None,
    lead: Annotated[list[str] | None, typer.Option("--lead", help="Tech lead")] = None,
    dev: Annotated[list[str] | None, typer.Option("--dev")] = None,
    workspace: WorkspaceOpt = None,
) -> None:
    from nexo.modules.planning.app import roleflow

    participants = {"pm": pm or [], "tech_lead": lead or [], "dev": dev or []}
    out = roleflow.session_start(_planner(workspace), feature, participants)
    if ctx().options.json:
        emit(out)
        return
    console.print(f"sessão {out['session']['id']} aberta em {feature}")
    if out["missing_roles"]:
        console.print(f"  [nexo.warn]sem {', '.join(out['missing_roles'])}: a sessão não conta como conjunta[/]")
    console.print(
        f"  refine com [nexo.accent]{look.prefix()}plan feature {feature}[/] / "
        f"[nexo.accent]{look.prefix()}plan answer {feature}[/];"
    )
    console.print(f"  ao encerrar ([nexo.accent]{look.prefix()}plan session close {feature}[/]) o pacote é gerado")


@session_app.command("close")
def plan_session_close_cmd(
    feature: str,
    notes: Annotated[str, typer.Option("--notes", help="Decisões e combinados da sessão")] = "",
    workspace: WorkspaceOpt = None,
) -> None:
    from nexo.modules.planning.app import roleflow

    out = roleflow.session_close(_planner(workspace), feature, notes)
    if "parts" in out:
        _print_roles(out)
    elif ctx().options.json:
        emit(out)
    else:
        console.print(f"sessão encerrada; prontidão {out['score']}% — o pacote sai quando a spec estiver pronta")


@plan_app.command("package")
def plan_package_cmd(feature: str, workspace: WorkspaceOpt = None) -> None:
    """Gera o pacote (requisitos, design/SDD e plano) para as aprovações sem sessão conjunta."""
    from nexo.modules.planning.app import roleflow

    _print_roles(roleflow.package(_planner(workspace), feature))


@plan_app.command("reject")
def plan_reject_cmd(
    feature: str,
    part: Annotated[str, typer.Option("--part", help="requisitos | design | plano")],
    reason: Annotated[str, typer.Option("--reason")] = "",
    workspace: WorkspaceOpt = None,
) -> None:
    """Rejeita a parte do seu papel; a IA refaz usando o motivo e o pacote volta para aprovação."""
    from nexo.modules.planning.app import roleflow

    _print_roles(roleflow.reject_part(_planner(workspace), feature, part, reason))


@plan_app.command("roles")
def plan_roles_cmd(feature: str, workspace: WorkspaceOpt = None) -> None:
    """Quem aprova o quê, o que está pendente e se a sessão conjunta é exigida."""
    from nexo.modules.planning.app import roleflow

    _print_roles(roleflow.roles_status(_planner(workspace), feature))


@plan_app.command("inbox", hidden=True)
def plan_inbox_cmd(workspace: WorkspaceOpt = None) -> None:
    """O que está esperando por você: decisões de discovery e aprovações do seu papel."""
    from nexo.modules.planning.app import roleflow

    out = roleflow.inbox(_planner(workspace))
    if ctx().options.json:
        emit(out)
        return
    roles = ", ".join(ROLE_NAMES[x] for x in out["roles"]) or "nenhum papel"
    console.print(f"{out['who']} · {roles}")
    if not out["items"]:
        console.print("  nada esperando por você")
    for item in out["items"]:
        console.print(f"  {item['feature']}: {item['action']}  [nexo.accent]{item['command']}[/]")


@plan_app.command("graph")
def plan_graph_cmd(
    feature: str,
    mermaid: Annotated[bool, typer.Option("--mermaid")] = False,
    workspace: WorkspaceOpt = None,
) -> None:
    report = _planner(workspace).graph(feature)
    if mermaid and not ctx().options.json:
        lines = ["flowchart LR"]
        for t in report.backlog["tasks"]:
            lines.append(f'    {t["id"].replace("-", "_")}["{t["id"]}: {t["title"]}"]')
            for dep in t["depends_on"]:
                lines.append(f"    {dep.replace('-', '_')} --> {t['id'].replace('-', '_')}")
        console.print("\n".join(lines))
        return
    _print_plan(report)


@app.command("baseline", hidden=True)
def baseline_cmd(
    since: Annotated[
        str | None, typer.Option("--since", help="AAAA-MM-DD (padrão: início das últimas sprints)")
    ] = None,
    until: Annotated[str | None, typer.Option("--until", help="AAAA-MM-DD (padrão: hoje)")] = None,
    sprints: Annotated[int, typer.Option("--sprints")] = 2,
    sprint_days: Annotated[int, typer.Option("--sprint-days")] = 14,
) -> None:
    """Linha de base antes do piloto: lead/cycle time, vazão e bugs das histórias fechadas no tracker."""
    from datetime import date, timedelta

    from nexo.modules.finops.app import metrics as finops_metrics

    end = until or date.today().isoformat()
    try:
        start = since or (date.fromisoformat(end) - timedelta(days=sprints * sprint_days)).isoformat()
    except ValueError as exc:
        raise NexoError("datas no formato AAAA-MM-DD") from exc
    data = finops_metrics.baseline(ctx(), start, end)
    if ctx().options.json:
        emit(data)
        return
    lead = data["lead_time_hours"] or {}
    cycle = data["cycle_time_hours"] or {}
    console.print(
        f"linha de base {start} a {end} ({data['tracker']}): {data['stories']} história(s), {data['bugs']} bug(s),"
        f" vazão {data['throughput_per_week']}/semana · lead time p50 {lead.get('p50', '—')} h"
        f" (p85 {lead.get('p85', '—')}) · cycle time p50 {cycle.get('p50', '—')} h"
        + (" · enviada ao central" if data.get("sent") else "")
    )
    if data["excluded_nexo"]:
        console.print(f"[nexo.muted]{data['excluded_nexo']} item(ns) já feitos pelo Nexo ficaram fora[/]")


@app.command("estimate", hidden=True)
def estimate_cmd(
    target: Annotated[str | None, typer.Argument()] = None,
    points: Annotated[int | None, typer.Option("--points")] = None,
) -> None:
    from nexo.modules.finops.app import metrics as finops_metrics

    data = finops_metrics.estimate_feature(ctx(), target, points)
    if ctx().options.json:
        emit(data)
        return
    hours = f" · ~{data['hours']} h de ciclo" if data["hours"] is not None else ""
    console.print(
        f"{data['points']} ponto(s): {brl(data['brl'])} (80%: {brl(data['brl_low'])} – "
        f"{brl(data['brl_high'])}){hours} · confiança {data['confidence']} ({data['samples']} amostras)"
    )


@app.command("discover")
def discover_cmd(
    no_baseline: Annotated[
        bool, typer.Option("--no-baseline", help="Não roda o pipeline (sem baseline/cobertura)")
    ] = False,
    as_is: Annotated[bool, typer.Option("--as-is", help="Gera o design técnico as-is pelo template")] = False,
) -> None:
    """Mapa do código existente, baseline de achados, convenções locais e (opcional) design as-is."""
    from nexo.modules.planning.app import discover as planning_discover

    data = planning_discover.discover(ctx(), with_baseline=not no_baseline, as_is=as_is)
    if ctx().options.json:
        emit(data)
        return
    langs = ", ".join(f"{k} {v}" for k, v in data["languages"].items())
    console.print(
        f"[nexo.title]Mapa do código[/] · {data['files']} arquivos ({langs}) · {data['tests']} de teste "
        f"· {data['seconds']} s"
    )
    if data["entrypoints"]:
        console.print(f"  entradas: {', '.join(data['entrypoints'])}")
    if data["hotspots"]:
        console.print(f"  hotspots (alteração × complexidade): {', '.join(data['hotspots'])}")
    naming = ", ".join(f"{k}: {v}" for k, v in data["conventions"]["naming"].items())
    console.print(f"  convenções: {naming or '—'} · testes: {', '.join(data['conventions']['test_frameworks']) or '—'}")
    for stage, cmds in data["commands"].items():
        console.print(f"  comando do projeto ({stage}): {cmds[0]['command']}  [nexo.muted]({cmds[0]['source']})[/]")
    if data["baseline"]:
        b = data["baseline"]
        console.print(
            f"  baseline: {b['findings']} achado(s) e {b['failing_tests']} teste(s) falhando hoje — não bloqueiam; "
            "o portão avalia só o que mudar"
        )
    console.print(
        f"  proposta de convenções: {data['conventions_skill']} (revise e adote com `nexo extensions skill add`)"
    )
    if data.get("as_is"):
        console.print(f"  design as-is: {data['as_is']}")


template_app = typer.Typer(help="Templates de artefatos", no_args_is_help=True)


app.add_typer(template_app, name="template")


@template_app.command("list")
def template_list_cmd() -> None:
    from nexo.modules.planning.app import templates

    rows = templates.listing(ctx())
    if ctx().options.json:
        emit(rows)
        return
    table = Table(title="Templates efetivos", title_justify="left")
    for col in ("tipo", "origem", "versão", "obrigatórias", "travadas"):
        table.add_column(col)
    for r in rows:
        table.add_row(r["type"], r["origin"], str(r["version"] or ""), ", ".join(r["required"]), ", ".join(r["locked"]))
    console.print(table)


@template_app.command("show")
def template_show_cmd(kind: str) -> None:
    from nexo.modules.planning.app import templates

    tpl = templates.resolve(ctx(), kind)
    if ctx().options.json:
        emit({"type": tpl.type, "origin": tpl.origin, "header": tpl.header, "body": tpl.body})
        return
    console.print(f"[nexo.title]{tpl.type}[/] · origem {tpl.origin}")
    console.print(tpl.body, markup=False, highlight=False)


@template_app.command("validate")
def template_validate_cmd(path: Path, kind: Annotated[str | None, typer.Option("--type")] = None) -> None:
    from nexo.modules.planning.app import templates

    problems = templates.validate_file(ctx(), path, kind)
    if ctx().options.json:
        emit({"path": str(path), "problems": problems})
    elif problems:
        for p in problems:
            err_console.print(f"[nexo.bad]✖[/] {p}")
    else:
        console.print(f"[nexo.good]✔[/] {path} válido")
    if problems:
        raise typer.Exit(1)


@template_app.command("preview")
def template_preview_cmd(
    kind: str,
    feature: Annotated[str, typer.Option("--feature")],
    item: Annotated[str | None, typer.Option("--item", help="História ou tarefa (story/task)")] = None,
) -> None:
    """Renderiza um template com os dados reais de uma feature (sem gravar nada)."""
    from nexo.modules.planning.app import templates

    rendered = templates.preview(ctx(), kind, feature, item)
    if ctx().options.json:
        emit({"type": kind, "missing": rendered.missing, "text": rendered.text})
        return
    console.print(rendered.text, markup=False, highlight=False)
    if rendered.missing:
        err_console.print(f"[nexo.warn]seções obrigatórias vazias:[/] {', '.join(rendered.missing)}")
