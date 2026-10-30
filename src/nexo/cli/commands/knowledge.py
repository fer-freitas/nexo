"""Comandos de conhecimento: documentação viva, grafo, perguntas, extensões, cérebro, aprendizado e avaliação."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
from rich.table import Table

from nexo.cli import render
from nexo.cli.app import app, console, ctx, emit
from nexo.cli.shared import _ext_group
from nexo.kernel.errors import ExitCode, NexoError
from nexo.kernel.finops.money import brl

docs_app = typer.Typer(help="Agente writer: documentação viva e conhecimento no cérebro", no_args_is_help=True)


app.add_typer(docs_app, name="docs")


@docs_app.command("update")
def docs_update_cmd(
    target: Annotated[
        str | None, typer.Argument(help="História (US-n), feature (F-n) ou vazio para o repositório")
    ] = None,
) -> None:
    """O writer atualiza docs/ a partir do que mudou e do que está ligado; só escreve documentação."""
    from nexo.modules.knowledge.app import writer

    data = writer.update(ctx(), target)
    if ctx().options.json:
        emit(data)
        return
    console.print(f"{data['task']}: {data['state']} · branch {data.get('branch') or '—'}")
    if data.get("reason"):
        console.print(f"  {data['reason']}", markup=False, highlight=False)


@docs_app.command("knowledge")
def docs_knowledge_cmd(
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Só gera o arquivo local")] = False,
) -> None:
    """Resumo de docs/ vira conhecimento no cérebro (knowledge/<produto>/<serviço>.md) por PR revisado."""
    from nexo.modules.knowledge.app import writer

    data = writer.publish_knowledge(ctx(), dry_run)
    if ctx().options.json:
        emit(data)
        return
    where = data.get("pr") or data.get("issue") or "só local"
    console.print(f"Conhecimento {data['path']} ({data['chars']} caracteres) · {where}")


graph_app = typer.Typer(help="Grafo do código e do cérebro (impacto e contexto)", no_args_is_help=True)


app.add_typer(graph_app, name="graph")


@graph_app.command("build")
def graph_build_cmd() -> None:
    """Refaz o grafo a partir do Git e do cérebro (as consultas refazem sozinhas quando algo muda)."""
    from nexo.modules.knowledge.app import graph as knowledge_graph

    data = knowledge_graph.build(ctx())
    if ctx().options.json:
        emit(data)
        return
    parts = ", ".join(f"{v} {k}" for k, v in sorted(data["stats"].items()))
    console.print(f"Grafo montado: {parts}")


def _print_groups(data: dict[str, Any], titles: dict[str, str]) -> None:
    for key, title in titles.items():
        items = data.get(key) or []
        console.print(f"[nexo.title]{title}[/] ({len(items)})")
        for item in items[:40]:
            console.print(f"  {item}", markup=False, highlight=False)
        if len(items) > 40:
            console.print(f"  … mais {len(items) - 40}")


@graph_app.command("impact")
def graph_impact_cmd(
    paths: Annotated[list[str], typer.Argument(help="Arquivos que vão mudar")],
    depth: Annotated[int, typer.Option("--depth", min=1, max=10)] = 3,
) -> None:
    """Quem depende desses arquivos e o que verifica isso: testes, histórias, critérios, incidentes e decisões."""
    from nexo.modules.knowledge.app import graph as knowledge_graph

    data = knowledge_graph.impact(ctx(), paths, depth)
    if ctx().options.json:
        emit(data)
        return
    if data["unknown"]:
        console.print(f"[nexo.warn]Fora do grafo:[/] {', '.join(data['unknown'])}")
    _print_groups(
        data,
        {
            "dependents": "Código que depende",
            "tests": "Testes para rodar",
            "stories": "Histórias",
            "criteria": "Critérios de aceite",
            "incidents": "Incidentes",
            "decisions": "Decisões e lições",
        },
    )


@graph_app.command("related")
def graph_related_cmd(paths: Annotated[list[str], typer.Argument(help="Arquivos")]) -> None:
    """O que está ligado a esses arquivos (é o que vai para o agente junto com a tarefa)."""
    from nexo.modules.knowledge.app import graph as knowledge_graph

    data = knowledge_graph.related(ctx(), paths)
    if ctx().options.json:
        emit(data)
        return
    _print_groups(
        data,
        {
            "neighbors": "Código ligado",
            "tests": "Testes",
            "decisions": "Decisões (ADRs)",
            "lessons": "Lições",
            "incidents": "Incidentes",
        },
    )


@app.command("ask")
def ask_cmd(
    question: Annotated[str, typer.Argument(help="Pergunta sobre o código ou as decisões do repositório")],
    budget: Annotated[int, typer.Option("--budget", help="Caracteres de contexto enviados ao agente")] = 9000,
) -> None:
    """Responde perguntas sobre o código e o cérebro, com as fontes (fontes inexistentes são descartadas)."""
    from nexo.modules.knowledge.app import ask as knowledge_ask

    res = knowledge_ask.ask(ctx(), question, budget)
    if ctx().options.json:
        emit(res)
        return
    console.print(res["answer"], markup=False)
    console.print(f"\n[nexo.muted]confiança: {res['confidence']}[/]")
    for s in res["sources"]:
        where = f":{s['lines']}" if s.get("lines") else ""
        console.print(f"  · {s['path']}{where}" + (f" — {s['why']}" if s.get("why") else ""), markup=False)
    if res["dropped_sources"]:
        console.print(f"  [nexo.warn]fontes descartadas (não existem): {', '.join(res['dropped_sources'])}[/]")


skill_app = typer.Typer(help="Skills", no_args_is_help=True)


tool_app = typer.Typer(help="Tools (servidores MCP)", no_args_is_help=True)


app.add_typer(skill_app, name="skill", hidden=True)


app.add_typer(tool_app, name="tool", hidden=True)


_ext_group("skill", skill_app)


_ext_group("tool", tool_app)


brain_app = typer.Typer(help="Segundo cérebro (nexo-brain)", no_args_is_help=True)


app.add_typer(brain_app, name="brain")


@brain_app.command("pull")
def brain_pull_cmd() -> None:
    from nexo.modules.knowledge.app import brain as knowledge_brain

    data = knowledge_brain.pull(ctx())
    if ctx().options.json:
        emit(data)
    elif not ctx().options.quiet:
        mark = "[nexo.good]assinado e verificado[/]" if data["verified"] else f"[nexo.warn]{data['reason']}[/]"
        console.print(f"Cérebro atualizado: versão {data['version']} · {mark}")


@brain_app.command("status")
def brain_status_cmd() -> None:
    from nexo.modules.knowledge.app import brain as knowledge_brain

    data = knowledge_brain.status(ctx())
    if ctx().options.json:
        emit(data)
        return
    console.print(f"repositório: {data['repo'] or '—'} · versão {data['version'] or '—'}")
    console.print(
        f"assinatura: {'verificada' if data['verified'] else data['reason'] or 'não verificada'}"
        f" · exigida: {'sim' if data['require_signature'] else 'não'} · chaves confiáveis: {data['trusted_keys']}"
    )


@brain_app.command("search")
def brain_search_cmd(query: str, limit: Annotated[int, typer.Option("--limit")] = 5) -> None:
    from nexo.modules.knowledge.app import brain as knowledge_brain

    results = knowledge_brain.search(ctx(), query, limit)
    if ctx().options.json:
        emit(results)
        return
    for r in results:
        console.print(f"[nexo.title]{r['title']}[/] · {r['path']}\n  {r['excerpt']}")


@brain_app.command("propose")
def brain_propose_cmd(
    path: Path,
    title: Annotated[str | None, typer.Option("--title")] = None,
    clone: Annotated[Path | None, typer.Option("--clone", help="Clone de trabalho do cérebro: abre um branch")] = None,
    target: Annotated[str, typer.Option("--target", help="Caminho da nota no cérebro")] = "",
) -> None:
    from nexo.modules.knowledge.app import brain as knowledge_brain

    if clone is not None:
        emit(knowledge_brain.propose_clone(ctx(), clone, path, target or f"knowledge/{path.name}", title or path.stem))
        return
    url = knowledge_brain.propose(ctx(), path, title)
    console.print(f"Proposta: {url or 'na fila (repositório/credencial não configurados)'}")


@brain_app.command("lint")
def brain_lint_cmd(
    path: Annotated[Path | None, typer.Argument(help="Clone do cérebro (padrão: pasta atual)")] = None,
) -> None:
    """Confere ID, frontmatter e links. Com erro, o `brain sign` recusa assinar."""
    from nexo.modules.knowledge.app import notes

    data = notes.lint(path or ctx().cwd)
    if ctx().options.json:
        emit(data)
    else:
        for p in data["problems"]:
            console.print(f"[{p['level']}] {p['file']}: {p['message']}", markup=False)
        console.print(f"{data['notes']} nota(s) · {data['errors']} erro(s) · {data['warnings']} aviso(s)")
    if data["errors"]:
        raise typer.Exit(ExitCode.GATE)


@brain_app.command("review")
def brain_review_cmd(
    number: int,
    path: Annotated[Path | None, typer.Option("--path", help="Clone do cérebro (padrão: pasta atual)")] = None,
) -> None:
    """Confere um PR do cérebro sem depender de CI: mesmo lint que o `brain sign` exige."""
    from nexo.modules.knowledge.app import notes

    data = notes.review_pr(path or ctx().cwd, number)
    emit(data)
    if data["errors"]:
        raise typer.Exit(ExitCode.GATE)


@brain_app.command("index")
def brain_index_cmd(
    path: Annotated[Path | None, typer.Argument(help="Clone do cérebro (padrão: a cópia verificada)")] = None,
) -> None:
    """Gera nós e arestas no cache local: a base do grafo. Nunca entra no Git."""
    from nexo.modules.knowledge.app import notes

    emit(notes.index(ctx(), path))


@brain_app.command("migrate-notes")
def brain_migrate_notes_cmd(path: Path) -> None:
    """Dá frontmatter aos arquivos que ainda não têm."""
    from nexo.modules.knowledge.app import notes

    emit(notes.migrate(path))


@brain_app.command("promote")
def brain_promote_cmd(path: Path, note_id: str) -> None:
    """Abre o branch que leva uma nota de projeto para o global, para revisão humana."""
    from nexo.modules.knowledge.app import notes

    emit(notes.promote(path, note_id))


@brain_app.command("init")
def brain_init_cmd(path: Path) -> None:
    from nexo.modules.knowledge.app import brain as knowledge_brain

    created = knowledge_brain.init_repo(path)
    console.print(f"nexo-brain preparado em {path} ({len(created)} arquivo(s))")


@brain_app.command("keygen")
def brain_keygen_cmd(private_key: Path) -> None:
    from nexo.modules.knowledge.app import brain as knowledge_brain

    pub = knowledge_brain.keygen(private_key)
    console.print(f"Chave privada: {private_key} (guarde com segurança)\nChave pública (brain.trusted_keys): {pub}")


@brain_app.command("sign")
def brain_sign_cmd(
    path: Path,
    key: Annotated[str, typer.Option("--key", help="Arquivo PEM, awskms://<chave> ou azkv://<cofre>/<chave>")],
    version: Annotated[str, typer.Option("--version")],
) -> None:
    from nexo.modules.knowledge.app import brain as knowledge_brain

    manifest = knowledge_brain.sign(ctx(), path, key, version)
    console.print(f"Manifesto assinado: {manifest}")


@brain_app.command("pubkey")
def brain_pubkey_cmd(
    key: Annotated[str, typer.Option("--key", help="Arquivo PEM ou azkv://<cofre>/<chave>[/<versão>]")],
) -> None:
    """Chave pública para `brain.trusted_keys` e impressão digital (para revogar em revoked-signers.yaml)."""
    from nexo.modules.knowledge.app import brain as knowledge_brain

    data = knowledge_brain.public_key(key)
    if ctx().options.json:
        emit(data)
        return
    console.print(f"brain.trusted_keys: {data['public_key']}\nimpressão digital: {data['fingerprint']}")


@brain_app.command("migrate")
def brain_migrate_cmd(
    path: Path,
    owner_nexo: Annotated[str, typer.Option("--owner-nexo", help="Dono padrão (CODEOWNERS)")] = "@time-nexo",
    owner_security: Annotated[str, typer.Option("--owner-security")] = "@seguranca",
    stack_owner: Annotated[
        list[str] | None, typer.Option("--stack-owner", help="stack=@dono, ex.: python=@time-python")
    ] = None,
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
) -> None:
    """Escreve a baseline embutida (prompts, skills, regras) num clone do nexo-brain para virar PR."""
    from nexo.modules.knowledge.app import content

    stacks = tuple(tuple(x.split("=", 1)) for x in stack_owner or [] if "=" in x)
    owners = content.Owners(owner_nexo, owner_security, stacks)  # type: ignore[arg-type]
    written = content.migrate(path, owners, overwrite)
    ctx().audit("brain.migrated", {"path": str(path), "files": len(written)})
    if ctx().options.json:
        emit({"written": written})
        return
    console.print(f"{len(written)} arquivo(s) escritos em {path}. Revise, assine (nexo brain sign) e abra o PR.")


@brain_app.command("diff")
def brain_diff_cmd(
    show: Annotated[str | None, typer.Option("--show", help="Mostra o diff de um arquivo")] = None,
) -> None:
    """O que o cérebro em uso muda em relação à baseline embutida."""
    from nexo.modules.knowledge.app import content

    data = content.diff(ctx(), show)
    if ctx().options.json:
        emit(data)
        return
    console.print(f"cérebro {data['version']} × baseline embutida")
    for row in data["changes"]:
        console.print(f"  {row['status']:<22} {row['path']}")
    if not data["changes"]:
        console.print("  nenhuma diferença")
    if data.get("diff"):
        console.print(data["diff"], markup=False, highlight=False)


learn_app = typer.Typer(help="Aprendizado contínuo", no_args_is_help=True)


app.add_typer(learn_app, name="learn")


@learn_app.command("collect")
def learn_collect_cmd(source: Annotated[list[str] | None, typer.Option("--source")] = None) -> None:
    from nexo.modules.knowledge.app import learning

    data = learning.collect(ctx(), source)
    if ctx().options.json:
        emit(data)
        return
    for name, info in data.items():
        detail = info.get("error") or f"{info['found']} encontrado(s), {info['new']} novo(s)"
        console.print(f"  {name}: {detail}")


@learn_app.command("review")
def learn_review_cmd(dry_run: Annotated[bool, typer.Option("--preview")] = False) -> None:
    from nexo.modules.knowledge.app import learning

    report = learning.review(ctx(), dry_run or ctx().options.dry_run)
    if ctx().options.json:
        emit(report.__dict__)
        return
    console.print(
        f"{report.events} evento(s) analisado(s) · {len(report.proposed)} lição(ões) proposta(s) · "
        f"{report.duplicates} repetida(s)"
    )
    for lesson in report.proposed:
        console.print(f"  • [{lesson['type']}] {lesson['title']} → {lesson['issue'] or 'fila/sem repositório'}")


@learn_app.command("status")
def learn_status_cmd() -> None:
    from nexo.modules.knowledge.app import learning

    data = learning.status(ctx())
    if ctx().options.json:
        emit(data)
        return
    for source, info in data["sources"].items():
        console.print(f"  {source}: {info['events']} evento(s), {info['pending']} pendente(s)")
    console.print(f"  lições propostas: {data['lessons']}")


@learn_app.command("patterns")
def learn_patterns_cmd(min_commits: Annotated[int, typer.Option("--min-commits")] = 2) -> None:
    from nexo.modules.knowledge.app import learning

    found = learning.patterns(ctx(), min_commits)
    if ctx().options.json:
        emit(found)
        return
    console.print(f"{len(found)} mudança(s) de padrão detectada(s)")
    for f in found:
        console.print(f"  • {f['package']} ({f['ecosystem']}) em {len(f['commits'])} commits → {f['issue'] or 'fila'}")


eval_app = typer.Typer(help="Avaliação de skills e rotas", no_args_is_help=True)


app.add_typer(eval_app, name="eval")


@eval_app.command("tasks")
def eval_tasks_cmd(
    from_repo: Annotated[Path | None, typer.Option("--from-repo")] = None,
    commit: Annotated[str | None, typer.Option("--commit")] = None,
) -> None:
    from nexo.modules.knowledge.app import evaluation

    if from_repo is not None:
        if not commit:
            raise NexoError("--from-repo exige --commit")
        evaluation.build_from_commit(ctx(), from_repo, commit)
    else:
        evaluation.build_from_stories(ctx())
    items = evaluation.list_golden(ctx())
    if ctx().options.json:
        emit(items)
        return
    for g in items:
        console.print(f"  {g['id']}: {len(g['tests'])} teste(s) de referência · base {str(g['base_sha'])[:10]}")


def _print_eval(report: dict[str, Any]) -> None:
    if ctx().options.json:
        emit(report)
        return
    table = Table(title=f"Avaliação {report['batch']} · {report['task']}", title_justify="left")
    for col in ("variante", "execuções", "sucesso", "IC 90%", "custo médio", "tempo médio"):
        table.add_column(col)
    for v in report["variants"]:
        table.add_row(
            v["variant"],
            str(v["runs"]),
            f"{v['success_rate']:.0%}",
            f"{v['ci90'][0]:.2f}–{v['ci90'][1]:.2f}",
            f"{brl(v['avg_cost_brl'])}",
            f"{v['avg_seconds']} s",
        )
    console.print(table)
    if report.get("comparison"):
        console.print(f"Resultado: {report['comparison']['verdict']}")


@eval_app.command("model")
def eval_model_cmd(
    task: Annotated[str, typer.Option("--task")],
    routes: Annotated[str, typer.Option("--routes", help="ex.: claude/sonnet,copilot/gpt-5")],
    runs: Annotated[int, typer.Option("--runs")] = 5,
    kind: Annotated[str, typer.Option("--kind")] = "backend",
) -> None:
    from nexo.modules.knowledge.app import evaluation

    variants = [evaluation.Variant(r.strip(), r.strip()) for r in routes.split(",") if r.strip()]
    if len(variants) < 1:
        raise NexoError("informe ao menos uma rota")
    _print_eval(evaluation.run(ctx(), task, variants, runs, kind))


@eval_app.command("skill")
def eval_skill_cmd(
    a: str,
    b: str,
    task: Annotated[str, typer.Option("--task")],
    route: Annotated[str, typer.Option("--route")] = "claude/sonnet",
    runs: Annotated[int, typer.Option("--runs")] = 5,
) -> None:
    from nexo.modules.knowledge.app import evaluation
    from nexo.modules.packs.app import extensions

    variants = [
        evaluation.Variant(f"skill:{name}", route, extensions.show(ctx(), "skill", name)["content"]) for name in (a, b)
    ]
    _print_eval(evaluation.run(ctx(), task, variants, runs, "skill"))


@eval_app.command("gate")
def eval_gate_cmd(
    route: Annotated[str, typer.Option("--route")] = "claude/sonnet",
    runs: Annotated[int, typer.Option("--runs")] = 3,
    baseline: Annotated[Path | None, typer.Option("--baseline", help="Padrão: evals/baseline.json do cérebro")] = None,
    max_drop: Annotated[float, typer.Option("--max-drop", help="Queda máxima da taxa de sucesso (0–1)")] = 0.10,
    max_cost_increase: Annotated[float, typer.Option("--max-cost-increase")] = 0.25,
    write: Annotated[Path | None, typer.Option("--write", help="Grava o resultado (nova linha de base)")] = None,
) -> None:
    """Portão de regressão de prompts/skills (Q-07): tarefas de referência × linha de base; falha = código 1."""
    from nexo.modules.knowledge.app import evaluation

    base = baseline or (ctx().brain_dir / "evals" / "baseline.json")
    report = evaluation.gate(ctx(), route, runs, base, max_drop, max_cost_increase, write)
    if ctx().options.json:
        emit(report)
    else:
        console.print(
            f"sucesso {report['success_rate']:.0%} · custo médio {render.brl(report['avg_cost_brl'])} · "
            f"{len(report['tasks'])} tarefa(s) de referência"
            + ("" if report["baseline"] else " · sem linha de base (primeira execução)")
        )
        for problem in report["regressions"]:
            console.print(f"[nexo.bad]✖ regressão:[/] {problem}")
        if report["passed"]:
            console.print("[nexo.good]✔ sem regressão[/]")
    if not report["passed"]:
        raise typer.Exit(1)


@eval_app.command("report")
def eval_report_cmd(
    batch: Annotated[str | None, typer.Option("--batch")] = None,
    propose: Annotated[bool, typer.Option("--propose")] = False,
) -> None:
    from nexo.modules.knowledge.app import evaluation, learning

    report = evaluation.report_batch(ctx(), batch)
    if propose:
        report["issue"] = evaluation.propose(ctx(), report)
        report["routing"] = learning.propose_routing(ctx())
    _print_eval(report)
    if propose and not ctx().options.json:
        console.print(
            f"Proposta: {report['issue'] or 'sem vencedor claro'} · roteamento: "
            f"{len(report['routing']['changes'])} ajuste(s)"
        )
