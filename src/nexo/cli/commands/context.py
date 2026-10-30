"""`nexo context` — o produto de que o projeto faz parte, a parte que ele é e o que é só dele.

Sem subcomando mostra o contexto atual. `context init` é o assistente de uma pasta nova (perguntas curtas; com as
opções, roda sem perguntar). `context product ...` cria e edita produtos; `context propose` manda o rascunho para o
cérebro da org, para todos os squads usarem.
"""

from __future__ import annotations

import re
from typing import Annotated

import typer
from rich.markup import escape

from nexo.cli import look
from nexo.cli.app import app, console, ctx, emit
from nexo.kernel.errors import NexoError
from nexo.kernel.platform import config

context_app = typer.Typer(help="Contexto do produto e do projeto (o que a IA sabe antes de planejar)")
product_app = typer.Typer(help="Produtos: visões e componentes compartilhados entre repositórios", no_args_is_help=True)
context_app.add_typer(product_app, name="product")
app.add_typer(context_app, name="context")

YES = {"s", "sim", "y", "yes"}


def _slug(text: str) -> str:
    import unicodedata

    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")[:40] or "projeto"


def _ask(question: str, default: str = "") -> str:
    shown = f" [{default}]" if default else ""
    try:
        answer = input(f"  {question}{shown} › ").strip()
    except (EOFError, OSError):  # sem entrada (script, CI): fica o sugerido
        answer = ""
    return answer or default


def _choose(question: str, options: list[str]) -> int:
    """Número escolhido (1..n) ou 0 para nenhum."""
    for i, option in enumerate(options, start=1):
        console.print(f"    [nexo.key]{i}[/] {escape(option)}")
    answer = _ask(question)
    return int(answer) if answer.isdigit() and 1 <= int(answer) <= len(options) else 0


@context_app.callback(invoke_without_command=True)
def context_show(context: typer.Context) -> None:
    """Mostra o produto, a parte que este repositório é e os arquivos de contexto."""
    if context.invoked_subcommand is not None:
        return
    from nexo.kernel import product as model
    from nexo.modules.planning.app import product_context as pc

    cur = pc.current(ctx())
    root = ctx().project_root
    data = {
        "product": cur.product.id if cur.product else None,
        "official": cur.official,
        "part": cur.part_id or None,
        "project_context": bool(cur.project_text),
        "folder": str(cur.folder) if cur.folder else None,
    }
    if ctx().options.json:
        emit(data)
        return
    if cur.product is None and not cur.project_text:
        console.print("Este projeto ainda não tem contexto.")
        console.print(f"  Rode [nexo.accent]{look.prefix()}context init[/]: poucas perguntas e o Nexo planeja melhor.")
        return
    if cur.product is not None:
        state = "oficial (cérebro da org)" if cur.official else "rascunho local — publique com `context propose`"
        console.print(f"[nexo.title]{escape(cur.product.label)}[/]  [nexo.muted]{state}[/]")
        console.print(escape(model.describe(cur.product, cur.part_id)))
    else:
        console.print("[nexo.muted]Sem produto: este repositório não está ligado a outros.[/]")
    if root is not None:
        mark = "✔" if cur.project_text else "–"
        console.print(f"\n  {mark} .nexo/contexto.md  [nexo.muted]o que é só deste repositório[/]")
    if cur.folder is not None:
        for name in ("contexto.md", "glossario.md"):
            mark = "✔" if (cur.folder / name).is_file() else "–"
            console.print(f"  {mark} {escape(str(cur.folder / name))}")


@context_app.command("init")
def context_init(
    product: Annotated[str | None, typer.Option("--product", help="Id do produto (novo ou existente)")] = None,
    part: Annotated[str | None, typer.Option("--part", help="Id desta parte no produto")] = None,
    kind: Annotated[str | None, typer.Option("--kind", help="visao ou componente")] = None,
    uses: Annotated[
        str | None, typer.Option("--uses", help="Componentes que esta parte usa, separados por vírgula")
    ] = None,
    what: Annotated[str | None, typer.Option("--what", help="O que este repositório faz, em uma frase")] = None,
    users: Annotated[str | None, typer.Option("--users", help="Quem usa este repositório")] = None,
    standalone: Annotated[bool, typer.Option("--standalone", help="Não faz parte de um produto maior")] = False,
) -> None:
    """Assistente de contexto: produto, parte do produto e o que este repositório faz."""
    from nexo.kernel.product import Part
    from nexo.modules.planning.app import product_context as pc

    c = ctx()
    if c.project_root is None:
        raise NexoError("nenhum projeto encontrado; rode `nexo init` antes")
    values = config.resolved(c).values
    values_squad = str(values.get("squad") or "")
    folder_id = _slug(c.project_root.name)
    chosen = None
    part_id = ""
    if not standalone and product is None:
        console.print("[nexo.title]Contexto do projeto[/]  [nexo.muted]Enter aceita o sugerido[/]")
        belongs = _ask("Este projeto faz parte de um produto maior (outros repositórios ou visões ligadas)? s/N")
        standalone = belongs.lower() not in YES
        if not standalone:
            existing = pc.list_products(c)
            labels = [f"{p['name']} ({p['id']}, {p['parts']} partes)" for p in existing] + ["um produto novo"]
            pick = _choose("Qual produto?", labels)
            if 1 <= pick <= len(existing):
                product = str(existing[pick - 1]["id"])
            else:
                name = _ask("Nome do produto (ex.: Custódia)")
                product = _ask("Id do produto", _slug(name or folder_id))
                description = _ask("Em uma frase, o que é o produto?")
                pc.create(c, product, name or product, description, [values_squad] if values_squad else [])
                console.print(f"  [nexo.good]✔[/] produto {escape(product)} criado (rascunho local)")
    if product and not standalone:
        try:
            chosen = pc.load(c, product)
        except NexoError:
            pc.create(c, product, product, "", [values_squad] if values_squad else [])
            chosen = pc.load(c, product)
        part_id = part or ""
        if not part_id:
            labels = [f"{p.label} ({p.kind}, {p.id})" for p in chosen.parts] + ["esta é uma parte nova"]
            pick = _choose("Este repositório é qual parte do produto?", labels)
            part_id = chosen.parts[pick - 1].id if 1 <= pick <= len(chosen.parts) else ""
        existing_part = chosen.part(part_id) if part_id else None
        if existing_part is None:
            part_id = part_id or _ask("Id desta parte", folder_id)
            if kind is None:
                options = ["uma visão (sistema que o usuário usa)", "um componente compartilhado"]
                kind = "componente" if _choose("Ela é:", options) == 2 else "visao"
            components = [p for p in chosen.parts if p.kind == "componente" and p.id != part_id]
            used = [u.strip() for u in (uses or "").split(",") if u.strip()]
            if uses is None and components:
                console.print("  Quais componentes compartilhados ela usa? (números com vírgula; Enter: nenhum)")
                for i, comp in enumerate(components, start=1):
                    console.print(f"    [nexo.key]{i}[/] {escape(comp.label)}")
                picks = _ask("Componentes")
                used = [components[int(x) - 1].id for x in re.findall(r"\d+", picks) if 1 <= int(x) <= len(components)]
            scm = values.get("scm") if isinstance(values.get("scm"), dict) else {}
            repo = str((scm or {}).get("repo") or "")
            new_part = Part(
                id=part_id,
                kind="componente" if kind == "componente" else "visao",
                name=c.project_root.name,
                description=what or "",
                repo=repo,
                squads=[values_squad] if values_squad else [],
                uses=used,
            )
            chosen = pc.upsert_part(c, product, new_part)
        pc.link_project(c, product, part_id)
    path = c.project_root / ".nexo" / "contexto.md"
    if not path.is_file():
        what = what if what is not None else _ask("Em uma frase, o que este repositório faz?")
        users = users if users is not None else _ask("Quem usa (pessoas, áreas ou sistemas)?")
        pc.write_project_context(c, pc.draft_project_text(c, what, users, chosen, part_id))
    if c.options.json:
        emit({"product": product if not standalone else None, "part": part_id or None, "context": str(path)})
        return
    console.print(f"\n[nexo.good]✔[/] contexto salvo em {escape(str(path))}  [nexo.muted]complete quando quiser[/]")
    if chosen is not None:
        _, official = pc.locate(c, chosen.id)
        if not official:
            console.print(
                f"  O produto {escape(chosen.label)} está em rascunho na sua máquina. Para os outros squads usarem: "
                f"[nexo.accent]{look.prefix()}context propose {escape(chosen.id)}[/]"
            )


@context_app.command("uses")
def context_uses(target: str) -> None:
    """Declara que este repositório usa outra parte do produto (resolve o desvio do desenho apontado pelo portal)."""
    from nexo.modules.planning.app import product_context as pc

    product, part = pc.declare_use(ctx(), target)
    if ctx().options.json:
        emit({"product": product.id, "part": part.id, "uses": part.uses})
        return
    console.print(f"[nexo.good]✔[/] {escape(part.label)} usa {escape(target)} (rascunho do produto)")
    console.print(
        f"  para os outros squads verem: [nexo.accent]{look.prefix()}context propose {escape(product.id)}[/]"
        " · se a ligação não deveria existir, corrija o código"
    )


@context_app.command("propose")
def context_propose(product: str) -> None:
    """Envia o rascunho do produto ao cérebro da org (todos os squads passam a usar quando aceito)."""
    from nexo.modules.planning.app import product_context as pc

    url = pc.propose(ctx(), product)
    if ctx().options.json:
        emit({"product": product, "issue": url})
    elif url:
        console.print(f"[nexo.good]✔[/] proposta enviada: {url}")
    else:
        folder = pc.draft_dir(ctx(), product)
        console.print(
            "[nexo.warn]cérebro sem repositório configurado (brain.github_repo)[/]: entregue ao time do cérebro "
            f"os arquivos de {escape(str(folder))}"
        )


@product_app.command("list")
def product_list() -> None:
    """Produtos conhecidos: oficiais (cérebro da org) e rascunhos desta máquina."""
    from nexo.modules.planning.app import product_context as pc

    items = pc.list_products(ctx())
    if ctx().options.json:
        emit(items)
        return
    if not items:
        console.print(f"Nenhum produto ainda. Crie com [nexo.accent]{look.prefix()}context product new <id>[/].")
        return
    for p in items:
        state = "oficial" if p["official"] else "rascunho"
        console.print(
            f"  {escape(str(p['id'])):<20} {escape(str(p['name']))}  [nexo.muted]{p['parts']} partes · {state}[/]"
        )


@product_app.command("show")
def product_show(product: str) -> None:
    """Visões, componentes, quem usa cada componente e os squads."""
    from nexo.kernel import product as model
    from nexo.modules.planning.app import product_context as pc

    p = pc.load(ctx(), product)
    if ctx().options.json:
        emit(p.model_dump(mode="json"))
        return
    console.print(escape(model.describe(p, "")))
    for part in p.parts:
        where = part.repo or part.path or "—"
        squads = ", ".join(part.squads) or "—"
        console.print(
            f"  {model.KIND_LABEL[part.kind]:<24} {escape(part.label):<24} "
            f"[nexo.muted]{escape(where)} · squads {escape(squads)}[/]"
        )


@product_app.command("new")
def product_new(
    product: str,
    name: Annotated[str, typer.Option("--name", help="Nome do produto")] = "",
    description: Annotated[str, typer.Option("--description", help="O que é o produto, em uma frase")] = "",
    squad: Annotated[list[str] | None, typer.Option("--squad", help="Squad que cuida do produto (repita)")] = None,
) -> None:
    """Cria um produto (rascunho local até ser proposto ao cérebro)."""
    from nexo.modules.planning.app import product_context as pc

    folder = pc.create(ctx(), product, name or product, description, list(squad or []))
    if ctx().options.json:
        emit({"product": product, "folder": str(folder)})
    else:
        console.print(f"[nexo.good]✔[/] produto {escape(product)} criado em {escape(str(folder))}")


@product_app.command("part")
def product_part(
    product: str,
    part: str,
    kind: Annotated[str, typer.Option("--kind", help="visao, componente ou externo")] = "visao",
    name: Annotated[str, typer.Option("--name")] = "",
    description: Annotated[str, typer.Option("--description")] = "",
    repo: Annotated[str, typer.Option("--repo", help="Repositório (ex.: org/custodia-cliente)")] = "",
    path: Annotated[str, typer.Option("--path", help="Pasta da parte, se o produto for um monorepo")] = "",
    uses: Annotated[
        str, typer.Option("--uses", help="Componentes e sistemas externos usados, separados por vírgula")
    ] = "",
    squad: Annotated[list[str] | None, typer.Option("--squad", help="Squad responsável (repita)")] = None,
    alias: Annotated[list[str] | None, typer.Option("--alias", help="Outro nome usado pelo time (repita)")] = None,
) -> None:
    """Adiciona ou atualiza uma parte do produto (visão, componente ou sistema externo)."""
    from nexo.kernel.product import KINDS, Part
    from nexo.modules.planning.app import product_context as pc

    if kind not in KINDS:
        raise NexoError("--kind deve ser visao, componente ou externo")
    try:
        new = Part(
            id=part,
            kind=kind,  # type: ignore[arg-type]
            name=name,
            description=description,
            repo=repo,
            path=path,
            uses=[u.strip() for u in uses.split(",") if u.strip()],
            squads=list(squad or []),
            aliases=list(alias or []),
        )
    except ValueError as exc:
        raise NexoError(str(exc)) from exc
    updated = pc.upsert_part(ctx(), product, new)
    if ctx().options.json:
        emit(updated.model_dump(mode="json"))
    else:
        console.print(f"[nexo.good]✔[/] {escape(new.label)} em {escape(updated.label)} ({len(updated.parts)} partes)")


def print_impact(fid: str) -> None:
    """a feature mexe em componente compartilhado? Avisa quais partes e squads do produto são afetados."""
    from nexo.modules.planning.app import product_context

    found = product_context.impact_for_feature(ctx(), fid)
    if found is None or not found.any:
        return
    squads = f" · squads {', '.join(found.squads)}" if found.squads else ""
    console.print(
        f"  [nexo.warn]impacto no produto:[/] {escape(', '.join(found.components))} também é usado por "
        f"{escape(', '.join(found.affected))}{escape(squads)} — combine com eles ou planeje em vários repositórios"
    )
