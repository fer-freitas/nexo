"""Contexto do projeto e do repositório: onde mora, como entra nos prompts e como chega ao cérebro.

- Projeto oficial: `<cérebro>/projetos/<id>/` (projeto.yaml, contexto.md, glossario.md), igual para todos os squads.
- Projeto em rascunho: `<NX_HOME>/projetos/<id>/`, criado aqui e usado na hora por quem criou; vira oficial
  quando o time do cérebro aceita a proposta (`nexo context propose`).
- Repositório: `context.project` e `context.part` na configuração e `.nexo/contexto.md` com o que é só dele.

O texto que vai para a IA é curto e tem orçamento (`context.chars`): posição no projeto, glossário, o
essencial do projeto e do repositório. O código das outras partes nunca entra.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from nexo.kernel import product as model
from nexo.kernel.context import PROJECT_DIR, AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.platform import config
from nexo.kernel.product import Impact, Part, Product
from nexo.kernel.runtime import yamlfile

FOLDER = "projetos"
FILES = ("projeto.yaml", "contexto.md", "glossario.md")
PROJECT_FILE = "contexto.md"
# papéis que planejam ou explicam recebem o contexto inteiro; quem escreve código recebe o resumo
FULL_ROLES = {
    "planner",
    "breakdown",
    "discovery",
    "explainer",
    "assistant",
    "writer",
    "analyst",
    "architect",
    "briefing",
    "breakdown-reviewer",
}
SHORT_ROLES = {"developer", "qa-author", "characterization", "reviewer", "security-reviewer", "integrator"}


@dataclass
class Current:
    product: Product | None
    part_id: str
    official: bool
    folder: Path | None
    project_text: str


def official_dir(ctx: AppContext, pid: str) -> Path:
    return ctx.brain_dir / FOLDER / pid


def draft_dir(ctx: AppContext, pid: str) -> Path:
    return ctx.home / FOLDER / pid


def locate(ctx: AppContext, pid: str) -> tuple[Path | None, bool]:
    """Pasta do projeto e se é a oficial. O rascunho local vence: é onde a pessoa está editando."""
    draft = draft_dir(ctx, pid)
    if (draft / "projeto.yaml").is_file():
        return draft, False
    official = official_dir(ctx, pid)
    if (official / "projeto.yaml").is_file():
        return official, True
    return None, False


def load(ctx: AppContext, pid: str) -> Product:
    folder, _ = locate(ctx, pid)
    if folder is None:
        raise NexoError(f"projeto {pid} não existe (crie com `nexo context project new {pid}`)")
    try:
        return Product.model_validate({"id": pid, **yamlfile.load(folder / "projeto.yaml")})
    except ValueError as exc:
        raise NexoError(f"{folder / 'projeto.yaml'}: {exc}") from exc


def list_products(ctx: AppContext) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for base, official in ((ctx.brain_dir / FOLDER, True), (ctx.home / FOLDER, False)):
        for path in sorted(base.glob("*/projeto.yaml")) if base.is_dir() else []:
            pid = path.parent.name
            try:
                p = load(ctx, pid)
            except NexoError:
                continue
            seen[pid] = {"id": pid, "name": p.label, "parts": len(p.parts), "official": official and pid not in seen}
    return sorted(seen.values(), key=lambda x: str(x["id"]))


def _read(path: Path, limit: int) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def project_settings(ctx: AppContext) -> tuple[str, str]:
    values = config.resolved(ctx).values if ctx.project_root else {}
    section = values.get("context") or {}
    if not isinstance(section, dict):
        return "", ""
    return str(section.get("project") or ""), str(section.get("part") or "")


def current(ctx: AppContext) -> Current:
    root = ctx.project_root
    project_text = _read(root / PROJECT_DIR / PROJECT_FILE, 20000) if root else ""
    pid, part_id = project_settings(ctx)
    if not pid:
        return Current(None, "", False, None, project_text)
    folder, official = locate(ctx, pid)
    product = load(ctx, pid) if folder else None
    return Current(product, part_id, official, folder, project_text)


def section(ctx: AppContext, role: str) -> str:
    """Bloco de contexto para o prompt do papel (vazio se o repositório não tem contexto ou o papel não usa)."""
    if role not in FULL_ROLES | SHORT_ROLES or ctx.project_root is None:
        return ""
    try:
        cur = current(ctx)
    except NexoError:
        return ""  # projeto inválido não derruba o planejamento; o `nexo context` mostra o erro
    if cur.product is None and not cur.project_text:
        return ""
    settings = config.resolved(ctx).values.get("context")
    budget = int(settings.get("chars", 6000)) if isinstance(settings, dict) else 6000
    if role in SHORT_ROLES:
        budget = min(budget, 2500)
    parts = ["\n\n## Contexto do projeto e do repositório (use os termos do glossário)"]
    left = budget
    if cur.product is not None:
        summary = model.describe(cur.product, cur.part_id)
        parts.append(summary)
        left -= len(summary)
        if cur.folder is not None and left > 200:
            glossary = _read(cur.folder / "glossario.md", max(200, left // 3))
            if glossary:
                parts.append(f"### Glossário\n{glossary}")
                left -= len(glossary)
            if role in FULL_ROLES and left > 200:
                about = _read(cur.folder / "contexto.md", max(200, left // 2))
                if about:
                    parts.append(f"### Sobre o projeto\n{about}")
                    left -= len(about)
    if cur.project_text and left > 200:
        parts.append(f"### Sobre este repositório\n{_read_text_budget(cur.project_text, left)}")
    return "\n\n".join(parts) + "\n"


def _read_text_budget(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def impact(ctx: AppContext, text: str) -> Impact | None:
    """Quem mais a feature afeta, pelo projeto do repositório (None se o repositório não faz parte de um projeto)."""
    try:
        cur = current(ctx)
    except NexoError:
        return None
    if cur.product is None or not cur.part_id:
        return None
    return model.impact(cur.product, cur.part_id, text)


def impact_for_feature(ctx: AppContext, fid: str) -> Impact | None:
    """Impacto cruzado da feature pelo texto da spec (título, objetivo, escopo e critérios)."""
    from nexo.kernel.workspace import specs

    root = ctx.project_root
    if root is None:
        return None
    try:
        spec = specs.load_spec(specs.feature_dir(root, fid))
    except (NexoError, OSError, ValueError):
        return None
    text = " ".join([spec.title, spec.objective, *spec.scope_in, *(f"{a.title} {a.then}" for a in spec.acceptance)])
    return impact(ctx, text)


# ---------------------------------------------------------------------- edição


def _draft_for_edit(ctx: AppContext, pid: str) -> Path:
    """Pasta de rascunho para editar: copia a oficial na primeira edição."""
    draft = draft_dir(ctx, pid)
    if not (draft / "projeto.yaml").is_file():
        official = official_dir(ctx, pid)
        if (official / "projeto.yaml").is_file():
            draft.mkdir(parents=True, exist_ok=True)
            for name in FILES:
                if (official / name).is_file():
                    shutil.copyfile(official / name, draft / name)
    return draft


def save(ctx: AppContext, product: Product) -> Path:
    folder = _draft_for_edit(ctx, product.id)
    folder.mkdir(parents=True, exist_ok=True)
    data = product.model_dump(mode="json", exclude={"id"}, exclude_defaults=True)
    data["parts"] = [p.model_dump(mode="json", exclude_defaults=True) for p in product.parts]
    yamlfile.dump(folder / "projeto.yaml", data)
    ctx.audit("product.saved", {"project": product.id, "parts": len(product.parts)})
    return folder


def create(ctx: AppContext, pid: str, name: str, description: str, squads: list[str]) -> Path:
    folder, _ = locate(ctx, pid)
    if folder is not None:
        raise NexoError(f"projeto {pid} já existe ({folder})")
    try:
        product = Product(id=pid, name=name, description=description, squads=squads)
    except ValueError as exc:
        raise NexoError(str(exc)) from exc
    folder = save(ctx, product)
    (folder / "contexto.md").write_text(
        f"# {product.label}\n\n{description or '(o que é o projeto, para quem e por quê)'}\n\n"
        "## Regras de negócio comuns\n\n- (regras que valem para todas as visões)\n\n"
        "## Integrações e restrições\n\n- (sistemas externos, regulação, limites)\n",
        encoding="utf-8",
    )
    (folder / "glossario.md").write_text(
        "# Glossário\n\n| Termo | Significado |\n|---|---|\n| (termo) | (o que significa no negócio) |\n",
        encoding="utf-8",
    )
    return folder


def upsert_part(ctx: AppContext, pid: str, part: Part) -> Product:
    product = load(ctx, pid)
    parts = [p for p in product.parts if p.id != part.id] + [part]
    try:
        updated = Product.model_validate({**product.model_dump(), "parts": [p.model_dump() for p in parts]})
    except ValueError as exc:
        raise NexoError(str(exc)) from exc
    save(ctx, updated)
    return updated


def declare_use(ctx: AppContext, target: str) -> tuple[Product, Part]:
    """a parte deste repositório passa a usar `target` (ligação que o código já tem e o projeto não declarava).
    Vai para o rascunho do projeto; os outros squads veem quando o cérebro aceitar a proposta."""
    cur = current(ctx)
    part = cur.product.part(cur.part_id) if cur.product else None
    if cur.product is None or part is None:
        raise NexoError("este repositório não está ligado a uma parte do projeto (rode `nexo context init`)")
    if cur.product.part(target) is None:
        raise NexoError(f"{target} não é parte do projeto {cur.product.id}")
    if target not in part.uses:
        part = part.model_copy(update={"uses": [*part.uses, target]})
        upsert_part(ctx, cur.product.id, part)
    return load(ctx, cur.product.id), part


def link_project(ctx: AppContext, pid: str, part_id: str) -> None:
    config.set_value(ctx, "context.project", pid, level="project")
    config.set_value(ctx, "context.part", part_id, level="project")


def write_project_context(ctx: AppContext, text: str) -> Path:
    root = ctx.project_root
    if root is None:
        raise NexoError("nenhum repositório preparado; rode `nexo init`")
    path = root / PROJECT_DIR / PROJECT_FILE
    path.write_text(text, encoding="utf-8")
    ctx.audit("product.project_context", {"chars": len(text)})
    return path


def draft_project_text(ctx: AppContext, what: str, users: str, product: Product | None, part_id: str) -> str:
    """Rascunho do `.nexo/contexto.md`: respostas da pessoa + o que dá para ler do repositório (sem IA)."""
    root = ctx.project_root
    if root is None:
        raise NexoError("nenhum repositório preparado; rode `nexo init`")
    values = config.resolved(ctx).values
    stacks = ", ".join(str(s) for s in values.get("stacks") or []) or "não detectada"
    folders = sorted(
        p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith(".") and p.name not in ("node_modules",)
    )[:12]
    readme = ""
    for name in ("README.md", "readme.md", "README"):
        if (root / name).is_file():
            paragraphs = [b.strip() for b in (root / name).read_text(encoding="utf-8").split("\n\n") if b.strip()]
            readme = next((b for b in paragraphs if not b.startswith("#")), "")[:600]
            break
    lines = [f"# {values.get('project') or root.name}", ""]
    if product is not None:
        part = product.part(part_id)
        kind = model.KIND_LABEL[part.kind] if part else "parte"
        lines += [f"Parte do projeto **{product.label}** como {kind} **{part.label if part else part_id}**.", ""]
    lines += ["## O que é", "", what or readme or "(o que este repositório faz)", ""]
    lines += ["## Quem usa", "", users or "(pessoas, áreas ou sistemas que dependem dele)", ""]
    lines += ["## Estrutura", "", f"- Stack: {stacks}"] + ([f"- Pastas: {', '.join(folders)}"] if folders else [])
    lines += [
        "",
        "## O que é específico daqui",
        "",
        "- (regras, cuidados e decisões que só valem neste repositório)",
        "",
    ]
    return "\n".join(lines)


SPECIFIC = "## O que é específico daqui"
TERMS = "## Glossário"
ENRICH_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {"type": "array", "items": {"type": "string"}},
        "terms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"term": {"type": "string"}, "meaning": {"type": "string"}},
                "required": ["term", "meaning"],
            },
        },
        "questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["facts", "terms", "questions"],
}
PLACEHOLDER = re.compile(r"^\s*(?:- )?\(.+\)\s*$")


def _repo_context(ctx: AppContext) -> Path:
    root = ctx.project_root
    if root is None:
        raise NexoError("nenhum repositório preparado; rode `nexo init`")
    path = root / PROJECT_DIR / PROJECT_FILE
    if not path.is_file():
        write_project_context(ctx, draft_project_text(ctx, "", "", None, ""))
    return path


def _under(text: str, heading: str, line: str) -> str:
    """Acrescenta a linha ao fim da seção e tira o texto de exemplo dela."""
    lines = text.rstrip("\n").split("\n")
    if heading not in lines:
        lines += ["", heading, ""]
    start = lines.index(heading) + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
    body = [x for x in lines[start:end] if x.strip() and not PLACEHOLDER.match(x)]
    if line not in body:
        body.append(line)
    return "\n".join([*lines[:start], "", *body, "", *lines[end:]]).rstrip("\n") + "\n"


def _refuse_sensitive(text: str) -> None:
    from nexo.kernel.security.redaction import find

    if {f.kind for f in find(text)} - {"email"}:
        raise NexoError("o texto parece ter segredo ou dado pessoal; o contexto vai para a IA e para o Git")


def add_fact(ctx: AppContext, fact: str) -> Path:
    _refuse_sensitive(fact)
    path = _repo_context(ctx)
    write_project_context(ctx, _under(path.read_text(encoding="utf-8"), SPECIFIC, f"- {fact.strip()}"))
    return path


def add_term(ctx: AppContext, term: str, meaning: str) -> Path:
    _refuse_sensitive(f"{term} {meaning}")
    path = _repo_context(ctx)
    line = f"- **{term.strip()}:** {meaning.strip()}"
    write_project_context(ctx, _under(path.read_text(encoding="utf-8"), TERMS, line))
    return path


def gaps(ctx: AppContext) -> list[str]:
    """O que ainda é texto de exemplo no contexto do repositório."""
    path = _repo_context(ctx)
    return [x.strip(" -()") for x in path.read_text(encoding="utf-8").splitlines() if PLACEHOLDER.match(x)]


def suggest(ctx: AppContext, sources: list[Path]) -> dict[str, Any]:
    """A IA lê os documentos ou o repositório e sugere. Nada entra sem a pessoa aceitar."""
    from nexo.kernel import services
    from nexo.kernel.security.injection import fence
    from nexo.kernel.security.redaction import redact

    root = ctx.project_root
    if root is None:
        raise NexoError("nenhum repositório preparado; rode `nexo init`")
    blocks = []
    for source in sources:
        if not source.is_file():
            raise NexoError(f"{source} não existe")
        blocks.append(fence(source.name, redact(source.read_text(encoding="utf-8", errors="replace"))[:12000]))
    material = "\n\n".join(blocks) or "Leia o repositório: README, configuração e estrutura de pastas."
    prompt = (
        "Sugira o que falta no contexto deste repositório, para quem vai planejar e construir nele. "
        "Só fatos que estejam no material, sem inventar. Regras, cuidados e decisões vão em `facts`; siglas e nomes "
        "do negócio, em `terms`; o que só uma pessoa sabe responder, em `questions`. Não repita o que já está lá.\n\n"
        f"## Contexto atual\n\n{_repo_context(ctx).read_text(encoding='utf-8')}\n\n## Material\n\n{material}"
    )
    llm = services.load("execution.llm")
    result = llm.call_structured(
        ctx,
        "explainer",
        "context-enrich",
        prompt,
        ENRICH_SCHEMA,
        root,
        facts={"kind": "planning"},
        data_class=str(config.resolved(ctx).values.get("data_class") or "confidential"),
        read_files=not sources,
    )
    return dict(result.data)


def propose(ctx: AppContext, pid: str) -> str | None:
    """Envia o rascunho do projeto ao time do cérebro (issue com os arquivos); devolve o link, se houver."""
    from nexo.kernel import services
    from nexo.kernel.security.redaction import find, redact

    create_issue = services.get("knowledge.brain", "create_issue")

    folder = draft_dir(ctx, pid)
    if not (folder / "projeto.yaml").is_file():
        raise NexoError(f"não há rascunho do projeto {pid} para propor")
    load(ctx, pid)  # valida antes de enviar
    blocks = []
    for name in FILES:
        if (folder / name).is_file():
            text = (folder / name).read_text(encoding="utf-8")
            if {f.kind for f in find(text)} - {"email"}:
                raise NexoError(f"{name} contém segredo ou dado pessoal; remova antes de propor")
            blocks.append(f"### projetos/{pid}/{name}\n\n```\n{redact(text)[:6000]}\n```")
    body = (
        f"Projeto proposto por `{ctx.actor}` via `nexo context propose`. Copie os arquivos para "
        f"`projetos/{pid}/` no cérebro, revise e assine a versão.\n\n" + "\n\n".join(blocks)
    )
    url = create_issue(ctx, f"Projeto: {pid}", body, ["nexo", "projeto"])
    ctx.audit("product.proposed", {"project": pid, "issue": url})
    return cast("str | None", url)
