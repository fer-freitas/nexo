"""Comandos de execução.

run, resume, status, aprovação do portão, qualidade, autonomia, runner, portões, servidor MCP e hooks.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.table import Table

from nexo.cli import look, render
from nexo.cli.app import app, console, ctx, emit
from nexo.cli.shared import _ext_group, _ui
from nexo.kernel.errors import ExitCode, NexoError

run_app = typer.Typer(help="Execução", no_args_is_help=True)


app.add_typer(run_app, name="run")


def _run_options(simulate: list[str] | None = None, wait: bool = False) -> Any:
    from nexo.modules.execution.app.run import RunOptions

    o = ctx().options
    return RunOptions(
        profile=o.profile,
        tool=o.tool,
        model=o.model,
        dry_run=o.dry_run,
        simulate_rate_limit=frozenset(simulate or []),
        wait=wait,
    )


def _print_report(report: Any) -> None:
    if ctx().options.json:
        emit(report.__dict__)
        return
    if report.decision is not None:
        d = report.decision
        console.print(f"[nexo.title]Tarefa {report.task_id}[/] · simulação (--dry-run)")
        cls = report.classification or {}
        console.print(
            f"  classificação: {cls.get('kind')} · tamanho {cls.get('size')} · riscos "
            f"{', '.join(cls.get('risk_tags', [])) or '—'} · origem {cls.get('source')}"
        )
        console.print(f"  rota escolhida: [nexo.title]{d['route'] or 'nenhuma'}[/]  [nexo.muted]{d['reason']}[/]")
        if ctx().options.verbose:
            table = Table()
            for col in ("rota", "p(sucesso)", "custo esperado", "score"):
                table.add_column(col)
            for c in d["candidates"]:
                table.add_row(c["route"], str(c["p_success"]), str(c["expected_cost"]), str(c["score"]))
            console.print(table)
            for route, why in d["rejected"].items():
                console.print(f"  [nexo.muted]✖ {route}: {why}[/]")
        console.print(f"  prompt: {d['prompt_chars']} caracteres · {d['redactions']} trecho(s) redigido(s)")
        return
    color = {"Concluido": "nexo.good", "Bloqueado": "nexo.warn", "Escalado": "nexo.bad"}.get(report.state, "white")
    console.print(
        f"\n[nexo.title]Tarefa {report.task_id}[/] → [{color}]{report.state}[/]"
        + (f"  [nexo.muted]{report.reason}[/]" if report.reason else "")
    )
    if report.commit:
        console.print(f"  commit {report.commit[:10]} na branch {report.branch}")
    if report.handoffs:
        console.print(f"  handoffs: {report.handoffs}")
    if ctx().options.verbose and report.routes:
        console.print(f"  rotas: {', '.join(dict.fromkeys(report.routes))}")
    if report.state in ("Bloqueado", "Escalado"):
        console.print(f"  retome com: [nexo.accent]{look.prefix()}resume {report.task_id}[/]")


@run_app.command("task")
def run_task_cmd(
    task_id: str,
    simulate_rate_limit: Annotated[
        list[str] | None, typer.Option("--simulate-rate-limit", help="Simula cota esgotada na ferramenta")
    ] = None,
) -> None:
    from nexo.modules.execution.app.run import Orchestrator

    report = Orchestrator(ctx(), _run_options(simulate_rate_limit), _ui).run_task(task_id)
    _print_report(report)
    if report.state == "Escalado":
        raise typer.Exit(ExitCode.ERROR)


@app.command("resume")
def resume_cmd(
    target: Annotated[str | None, typer.Argument()] = None,
    ready: Annotated[bool, typer.Option("--ready", help="Retoma tudo que já pode continuar")] = False,
    wait: Annotated[bool, typer.Option("--wait", help="Aguarda a renovação da cota e continua")] = False,
    simulate_rate_limit: Annotated[list[str] | None, typer.Option("--simulate-rate-limit")] = None,
) -> None:
    from nexo.modules.execution.app.run import Orchestrator
    from nexo.modules.execution.app.story import is_story

    if ready or target is None:
        from nexo.modules.execution.app import watcher

        if not ready:
            raise NexoError("informe o id ou use --ready")
        resumed = watcher.resume_ready(ctx())
        console.print(f"{len(resumed)} item(ns) retomado(s): {', '.join(resumed) or '—'}")
        return
    if is_story(ctx(), target):
        story = _story_runner().resume(target)
        _print_story(story)
        if story.state == "Escalado":
            raise typer.Exit(ExitCode.ERROR)
        return
    report = Orchestrator(ctx(), _run_options(simulate_rate_limit, wait), _ui).resume(target)
    _print_report(report)
    if report.state == "Escalado":
        raise typer.Exit(ExitCode.ERROR)


@app.command("status")
def status_cmd(target: Annotated[str | None, typer.Argument()] = None) -> None:
    from nexo.modules.execution.app import status as execution_status

    if target is None:
        rows = execution_status.overview(ctx())
        if ctx().options.json:
            emit(rows)
            return
        table = Table()
        for col in ("item", "título", "status", "custo", "detalhe"):
            table.add_column(col)
        for r in rows:
            cost = ""
            if r.get("cost_brl") is not None:
                cost = render.brl(r["cost_brl"])
                if r.get("cap_brl"):
                    cost += f" de {render.brl(r['cap_brl'])} ({r['cost_brl'] / r['cap_brl'] * 100:.0f}%)"
            table.add_row(r["id"], r["title"], r["state"], cost, r["reason"] or "")
        console.print(table)
        return
    import re

    if re.fullmatch(r"F-\d+", target, re.IGNORECASE):  # feature mostra o plano (o antigo `plan status`)
        from nexo.cli.commands.planning import plan_status_cmd

        plan_status_cmd(target.upper(), None)
        return
    data = execution_status.detail(ctx(), target)
    if ctx().options.json:
        emit(data)
        return
    console.print(f"[nexo.title]{data['id']}[/] · {data['state']}")
    for r in data["runs"]:
        console.print(f"  execução {r['id']} · {r['state']}" + (f" · {r['reason']}" if r["reason"] else ""))
        for st in r["steps"]:
            route = f" {st['route']}" if ctx().options.verbose else ""
            console.print(f"    passo {st['seq']}{route}: {st['outcome']} · checkpoint {st['checkpoint']}")
        for h in r["handoffs"]:
            console.print(f"    handoff {h['seq']}: {h['reason']}")


def _story_ui(kind: str, data: dict[str, Any]) -> None:
    """Andamento da história em fases."""
    o = ctx().options
    if o.json or o.quiet:
        return
    _progress()(kind, data)


def _progress() -> look.StoryProgress:
    if "p" not in look.ACTIVE:
        look.ACTIVE["p"] = look.StoryProgress(console, verbose=ctx().options.verbose)
    return look.ACTIVE["p"]


def _print_story(report: Any) -> None:
    progress = look.ACTIVE.pop("p", None)
    if progress is not None:
        progress.close(report)
    if ctx().options.json:
        emit(report.__dict__)
        return
    look.story_report(console, report)


def _story_runner() -> Any:
    from nexo.modules.execution.app.story import StoryRunner

    return StoryRunner(ctx(), _run_options(), _story_ui)


@run_app.command("story")
def run_story_cmd(
    story_id: str,
    base: Annotated[
        str | None, typer.Option("--base", hidden=True, help="Branch base (dependência não mergeada)")
    ] = None,
) -> None:
    if base:
        import dataclasses

        from nexo.modules.execution.app.story import StoryRunner

        report = StoryRunner(ctx(), dataclasses.replace(_run_options(), base_ref=base), _story_ui).run(story_id)
    else:
        report = _story_runner().run(story_id)
    _print_story(report)
    if report.state == "Escalado":
        raise typer.Exit(ExitCode.ERROR)


@run_app.command("feature")
def run_feature_cmd(
    feature_id: str,
    parallel: Annotated[int, typer.Option("--parallel", help="Histórias em paralelo por onda")] = 1,
) -> None:
    """Roda a feature inteira: histórias em ondas por dependência, com paralelismo e orçamento."""
    from nexo.modules.execution.app import feature as execution_feature

    report = execution_feature.run_feature(ctx(), feature_id, max(1, parallel))
    if ctx().options.json:
        emit(report)
    else:
        for s in report["stories"]:
            console.print(
                f"{s['story']}: {s['state']}" + (f" ({s['reason']})" if s.get("reason") else ""), highlight=False
            )
        console.print(
            f"{report['done']}/{len(report['stories'])} concluída(s)"
            + (f" · parou: {report['stopped']}" if report["stopped"] else "")
        )
    if report["done"] < len(report["stories"]):
        raise typer.Exit(ExitCode.ERROR)


@app.command("approve")
def approve_cmd(
    story_id: str,
    gate: Annotated[str, typer.Option("--gate")] = "code",
    raise_pct: Annotated[float | None, typer.Option("--raise", help="Portão de custo: eleva o teto em N%")] = None,
    economic: Annotated[
        bool, typer.Option("--economic", help="Portão de custo: segue no perfil econômico (+25% de teto)")
    ] = False,
) -> None:
    _print_story(_story_runner().approve(story_id, gate, raise_pct, economic))


@app.command("reject")
def reject_cmd(story_id: str, reason: Annotated[str, typer.Option("--reason")] = "") -> None:
    _print_story(_story_runner().reject(story_id, reason))


quality_app = typer.Typer(help="Qualidade", no_args_is_help=True)


app.add_typer(quality_app, name="quality")


@quality_app.command("run")
def quality_run_cmd(
    stack: Annotated[str | None, typer.Option("--stack")] = None,
    stage: Annotated[list[str] | None, typer.Option("--stage")] = None,
    path: Annotated[Path | None, typer.Option("--path")] = None,
) -> None:
    from nexo.modules.execution.app import quality as execution_quality

    report = execution_quality.run(ctx(), stack, stage, path)
    failed = report.failed_required
    if ctx().options.json:
        emit(report.as_dict())
    else:
        table = Table(title=f"Qualidade · {report.stack}", title_justify="left")
        for col in ("etapa", "tipo", "status", "resumo", "s"):
            table.add_column(col)
        colors = {"passed": "nexo.good", "failed": "nexo.bad", "error": "nexo.bad", "skipped": "nexo.muted"}
        for s in report.stages:
            table.add_row(
                s.id, s.kind, f"[{colors.get(s.status, 'white')}]{s.status}[/]", s.summary, f"{s.seconds:.1f}"
            )
        console.print(table)
        if report.coverage is not None:
            console.print(f"cobertura: {report.coverage:.1f}%")
    if failed:
        raise typer.Exit(ExitCode.ERROR)


standards_app = typer.Typer(help="Padrão de código garantido (regras das skills)", no_args_is_help=True)


app.add_typer(standards_app, name="standards", hidden=True)


@standards_app.command("check")
def standards_check_cmd(
    stack: Annotated[str | None, typer.Option("--stack")] = None,
    changed_from: Annotated[
        str | None, typer.Option("--changed-from", help="só o que mudou desde esta referência (ex.: origin/main)")
    ] = None,
) -> None:
    """Verifica o padrão de código; sai com erro se alguma regra `error` for violada."""
    from nexo.modules.execution.app import quality as execution_quality

    data = execution_quality.standards_check(ctx(), stack, changed_from)
    if ctx().options.json:
        emit(data)
    else:
        std = data["standards"] or {}
        files = int(std.get("files") or 0)
        adherence = f"{100 * int(std.get('files_ok') or 0) / files:.0f}%" if files else "—"
        console.print(f"Padrão de código · {data['stack']}: {data['summary']} · aderência {adherence}")
        for v in data["violations"][:200]:
            mark = "[nexo.bad]erro[/]" if v["severity"] in ("high", "critical") else "[nexo.warn]aviso[/]"
            console.print(f"  {mark} {v['file']}:{v['line']} {v['rule']} — {v['message']}", highlight=False)
        console.print(f"[nexo.muted]SARIF: {data['sarif']}[/]")
    if data["status"] == "failed":
        raise typer.Exit(ExitCode.ERROR)


@standards_app.command("rules")
def standards_rules_cmd(stack: Annotated[str | None, typer.Option("--stack")] = None) -> None:
    """Regras que valem para o projeto e a skill de cada uma."""
    from nexo.modules.execution.app import quality as execution_quality

    rules = execution_quality.standards_rules(ctx(), stack)
    if ctx().options.json:
        emit(rules)
        return
    table = Table()
    for col in ("regra", "o que verifica", "severidade", "skill"):
        table.add_column(col)
    for r in rules:
        table.add_row(r["id"], r["title"], "erro" if r["severity"] == "error" else "aviso", r["skill"] or "—")
    console.print(table)


gates_app = typer.Typer(help="Portões", no_args_is_help=True)


app.add_typer(gates_app, name="gates", hidden=True)


@gates_app.command("calibrate")
def gates_calibrate_cmd() -> None:
    from nexo.modules.finops.app import metrics as finops_metrics

    data = finops_metrics.calibrate(ctx())
    if ctx().options.json:
        emit(data)
        return
    console.print(
        f"Limiar atual {data['current']} → proposto {data['proposal']} · {data['samples']} decisão(ões) "
        f"humana(s)\n  {data['note']}\n  A mudança entra por PR no nexo-brain (gates.thresholds.code)."
    )


autonomy_app = typer.Typer(help="Autonomia conquistada do squad", no_args_is_help=True)


app.add_typer(autonomy_app, name="autonomy", hidden=True)


@autonomy_app.command("status")
def autonomy_status_cmd() -> None:
    """Nível de autonomia (L0–L3) do squad × área deste projeto e o que falta para subir."""
    from nexo.modules.execution.app import autonomy

    data = autonomy.status(ctx())
    if ctx().options.json:
        emit(data)
        return
    console.print(
        f"squad {data['squad'] or '—'} · área {data['area'] or '—'} · nível [nexo.title]{data['level']}[/]",
        highlight=False,
    )
    el = data.get("eligibility")
    if el:
        nxt = el.get("next_level")
        verdict = "elegível" if el["eligible"] else "ainda não: " + "; ".join(el["reasons"])
        console.print(f"próximo nível {nxt or '—'}: {verdict}", highlight=False)


@autonomy_app.command("set")
def autonomy_set_cmd(
    level: Annotated[str, typer.Argument(help="L0 | L1 | L2 | L3")],
    reason: Annotated[str, typer.Option("--reason")] = "",
    squad: Annotated[str | None, typer.Option("--squad")] = None,
    area: Annotated[str | None, typer.Option("--area")] = None,
) -> None:
    """Pede a mudança de nível ao central: subir exige elegibilidade e o gestor do squad; descer, motivo."""
    from nexo.modules.execution.app import autonomy

    data = autonomy.set_level(ctx(), level, reason, squad, area)
    if ctx().options.json:
        emit(data)
        return
    console.print(f"{data['squad']} · {data['area']}: {data['from']} → {data['to']}")


runner_app = typer.Typer(help="Runner remoto", no_args_is_help=True)


app.add_typer(runner_app, name="runner", hidden=True)


@runner_app.command("serve")
def runner_serve_cmd(
    queue: Annotated[Path, typer.Option("--queue", help="Pasta-fila com jobs *.json")],
    once: Annotated[bool, typer.Option("--once", help="Processa o que houver e sai")] = False,
) -> None:
    """Executa features/histórias de uma fila, desassistido, com as políticas e a identidade de serviço."""
    from nexo.modules.execution.app import feature as execution_feature

    processed = execution_feature.serve(ctx(), queue, once)
    if ctx().options.json:
        emit(processed)
    else:
        console.print(f"{len(processed)} job(s) processado(s)")


mcp_app = typer.Typer(help="Servidor MCP do Nexo e catálogo de MCPs oficiais", no_args_is_help=True)


app.add_typer(mcp_app, name="mcp")


@mcp_app.command("catalog")
def mcp_catalog_cmd() -> None:
    """MCPs oficiais dos produtos: situação, se está ligado, aprovado pela Segurança e o que falta."""
    from nexo.modules.mcp.app import catalog as mcp_catalog

    rows = mcp_catalog.status(ctx())
    if ctx().options.json:
        emit(rows)
        return
    table = Table()
    for col in ("id", "produto", "situação", "ligado", "aprovado", "acesso", "pendências"):
        table.add_column(col)
    for r in rows:
        table.add_row(
            r["id"],
            str(r["product"]),
            str(r["status"]) + ("" if r["verified"] else " · não verificado"),
            "sim" if r["enabled"] else "—",
            "sim" if r["approved"] else "—",
            "leitura e escrita" if r["write"] else "leitura",
            "; ".join(r["problems"]) if r["enabled"] else "",
        )
    console.print(table)
    console.print(
        "[nexo.muted]Ligar: mcp.servers.<id>.enabled · aprovar: org/tools.yaml › external_mcp · "
        "credencial: nexo login mcp_<id>[/]"
    )


@mcp_app.command("check")
def mcp_check_cmd() -> None:
    """Sai com erro quando algum MCP ligado não vai entrar nas execuções (aprovação, URL, credencial)."""
    from nexo.modules.mcp.app import catalog as mcp_catalog

    issues = mcp_catalog.check(ctx())
    if ctx().options.json:
        emit({"ok": not issues, "issues": issues})
    elif not issues:
        console.print("MCPs ligados prontos para as execuções.")
    for issue in [] if ctx().options.json else issues:
        console.print(f"  [nexo.warn]·[/] {issue['id']}: {issue['reason']}", highlight=False)
    if issues:
        raise typer.Exit(ExitCode.ERROR)


@mcp_app.command("serve")
def mcp_serve_cmd(
    run: Annotated[str, typer.Option("--run")], role: Annotated[str | None, typer.Option("--role")] = None
) -> None:
    from nexo.modules.execution.app.mcp_server import build_server

    build_server(ctx(), run, role).serve()


hook_app = typer.Typer(help="Hooks", no_args_is_help=True)


app.add_typer(hook_app, name="hook", hidden=True)


_ext_group("hook", hook_app)


@hook_app.command("exec")
def hook_exec_cmd(key: str, claude: Annotated[bool, typer.Option("--claude")] = False) -> None:
    """Executa um hook de extensão; com --claude fala o protocolo de hooks do Claude Code."""
    import json as _json

    from nexo.modules.packs.app import extensions

    name = key.split(":", 1)[-1]
    ext = next((e for e in extensions.effective(ctx()) if e.key == f"hook:{name}"), None)
    if ext is None or ext.meta.hook is None:
        raise NexoError(f"hook {name} não encontrado")
    raw = _json.loads(sys.stdin.read() or "{}")
    payload = (
        {
            "event": ext.meta.hook.event,
            "tool": raw.get("tool_name"),
            "input": raw.get("tool_input") or {},
            "worktree": raw.get("cwd") or str(ctx().cwd),
        }
        if claude
        else raw
    )
    result = extensions.run_hook(ext, payload)
    ctx().audit("extension.hook", {"hook": name, "event": ext.meta.hook.event, **result})
    if result["decision"] != "deny":
        return
    if claude and ext.meta.hook.event == "pre_tool":
        console.print_json(
            _json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": result["reason"],
                    }
                }
            )
        )
    elif claude:
        console.print_json(_json.dumps({"decision": "block", "reason": result["reason"]}))
    else:
        emit(result)


@hook_app.command("run")
def hook_run_cmd(
    name: str,
    run: Annotated[str, typer.Option("--run")] = "",
    exit_code: Annotated[bool, typer.Option("--exit-code", help="Nega saindo com 2, sem JSON")] = False,
) -> None:
    from nexo.modules.execution.app import hooks as hooks_app

    if name == "guard":  # sessão do Claude Code aberta fora do Nexo
        from nexo.modules.setup.app import guard

        raise typer.Exit(guard.run_hook(ctx()))
    if name != "pre-tool-policy" or not run:
        raise NexoError(f"hook desconhecido: {name}")
    raise typer.Exit(hooks_app.run_pre_tool_policy(ctx(), run, exit_code=exit_code))
