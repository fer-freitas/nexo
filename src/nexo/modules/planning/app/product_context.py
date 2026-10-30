"""Contexto do produto e do projeto: onde mora, como entra nos prompts e como chega ao cérebro.

- Produto oficial: `<cérebro>/produtos/<id>/` (produto.yaml, contexto.md, glossario.md), igual para todos os squads.
- Produto em rascunho: `<NX_HOME>/produtos/<id>/`, criado aqui e usado na hora por quem criou; vira oficial
  quando o time do cérebro aceita a proposta (`nexo context propose`).
- Projeto: `product.id` e `product.repo` na configuração do projeto e `.nexo/contexto.md` com o que é só dele.

O texto que vai para a IA é curto e tem orçamento (`product.context_chars`): posição no produto, glossário, o
essencial do produto e do repositório. O código das outras partes nunca entra.
"""

from __future__ import annotations

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

FOLDER = "produtos"
FILES = ("produto.yaml", "contexto.md", "glossario.md")
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
    """Pasta do produto e se é a oficial. O rascunho local vence: é onde a pessoa está editando."""
    draft = draft_dir(ctx, pid)
    if (draft / "produto.yaml").is_file():
        return draft, False
    official = official_dir(ctx, pid)
    if (official / "produto.yaml").is_file():
        return official, True
    return None, False


def load(ctx: AppContext, pid: str) -> Product:
    folder, _ = locate(ctx, pid)
    if folder is None:
        raise NexoError(f"produto {pid} não existe (crie com `nexo context product new {pid}`)")
    try:
        return Product.model_validate({"id": pid, **yamlfile.load(folder / "produto.yaml")})
    except ValueError as exc:
        raise NexoError(f"{folder / 'produto.yaml'}: {exc}") from exc


def list_products(ctx: AppContext) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for base, official in ((ctx.brain_dir / FOLDER, True), (ctx.home / FOLDER, False)):
        for path in sorted(base.glob("*/produto.yaml")) if base.is_dir() else []:
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
    section = values.get("product") or {}
    if not isinstance(section, dict):
        return "", ""
    return str(section.get("id") or ""), str(section.get("repo") or "")


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
    """Bloco de contexto para o prompt do papel (vazio se o projeto não tem contexto ou o papel não usa)."""
    if role not in FULL_ROLES | SHORT_ROLES or ctx.project_root is None:
        return ""
    try:
        cur = current(ctx)
    except NexoError:
        return ""  # produto inválido não derruba o planejamento; o `nexo context` mostra o erro
    if cur.product is None and not cur.project_text:
        return ""
    settings = config.resolved(ctx).values.get("product")
    budget = int(settings.get("context_chars", 6000)) if isinstance(settings, dict) else 6000
    if role in SHORT_ROLES:
        budget = min(budget, 2500)
    parts = ["\n\n## Contexto do produto e do projeto (use os termos do glossário)"]
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
                    parts.append(f"### Sobre o produto\n{about}")
                    left -= len(about)
    if cur.project_text and left > 200:
        parts.append(f"### Sobre este repositório\n{_read_text_budget(cur.project_text, left)}")
    return "\n\n".join(parts) + "\n"


def _read_text_budget(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def impact(ctx: AppContext, text: str) -> Impact | None:
    """Quem mais a feature afeta, pelo produto do projeto (None se o projeto não faz parte de um produto)."""
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
    if not (draft / "produto.yaml").is_file():
        official = official_dir(ctx, pid)
        if (official / "produto.yaml").is_file():
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
    yamlfile.dump(folder / "produto.yaml", data)
    ctx.audit("product.saved", {"product": product.id, "parts": len(product.parts)})
    return folder


def create(ctx: AppContext, pid: str, name: str, description: str, squads: list[str]) -> Path:
    folder, _ = locate(ctx, pid)
    if folder is not None:
        raise NexoError(f"produto {pid} já existe ({folder})")
    try:
        product = Product(id=pid, name=name, description=description, squads=squads)
    except ValueError as exc:
        raise NexoError(str(exc)) from exc
    folder = save(ctx, product)
    (folder / "contexto.md").write_text(
        f"# {product.label}\n\n{description or '(o que é o produto, para quem e por quê)'}\n\n"
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
    """a parte deste repositório passa a usar `target` (ligação que o código já tem e o produto não declarava).
    Vai para o rascunho do produto; os outros squads veem quando o cérebro aceitar a proposta."""
    cur = current(ctx)
    part = cur.product.part(cur.part_id) if cur.product else None
    if cur.product is None or part is None:
        raise NexoError("este repositório não está ligado a uma parte do produto (rode `nexo context init`)")
    if cur.product.part(target) is None:
        raise NexoError(f"{target} não é parte do produto {cur.product.id}")
    if target not in part.uses:
        part = part.model_copy(update={"uses": [*part.uses, target]})
        upsert_part(ctx, cur.product.id, part)
    return load(ctx, cur.product.id), part


def link_project(ctx: AppContext, pid: str, part_id: str) -> None:
    config.set_value(ctx, "product.id", pid, level="project")
    config.set_value(ctx, "product.repo", part_id, level="project")


def write_project_context(ctx: AppContext, text: str) -> Path:
    root = ctx.project_root
    if root is None:
        raise NexoError("nenhum projeto encontrado; rode `nexo init`")
    path = root / PROJECT_DIR / PROJECT_FILE
    path.write_text(text, encoding="utf-8")
    ctx.audit("product.project_context", {"chars": len(text)})
    return path


def draft_project_text(ctx: AppContext, what: str, users: str, product: Product | None, part_id: str) -> str:
    """Rascunho do `.nexo/contexto.md`: respostas da pessoa + o que dá para ler do repositório (sem IA)."""
    root = ctx.project_root
    if root is None:
        raise NexoError("nenhum projeto encontrado; rode `nexo init`")
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
        lines += [f"Parte do produto **{product.label}** como {kind} **{part.label if part else part_id}**.", ""]
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


def propose(ctx: AppContext, pid: str) -> str | None:
    """Envia o rascunho do produto ao time do cérebro (issue com os arquivos); devolve o link, se houver."""
    from nexo.kernel import services
    from nexo.kernel.security.redaction import find, redact

    create_issue = services.get("knowledge.brain", "create_issue")

    folder = draft_dir(ctx, pid)
    if not (folder / "produto.yaml").is_file():
        raise NexoError(f"não há rascunho do produto {pid} para propor")
    load(ctx, pid)  # valida antes de enviar
    blocks = []
    for name in FILES:
        if (folder / name).is_file():
            text = (folder / name).read_text(encoding="utf-8")
            if {f.kind for f in find(text)} - {"email"}:
                raise NexoError(f"{name} contém segredo ou dado pessoal; remova antes de propor")
            blocks.append(f"### produtos/{pid}/{name}\n\n```\n{redact(text)[:6000]}\n```")
    body = (
        f"Produto proposto por `{ctx.actor}` via `nexo context propose`. Copie os arquivos para "
        f"`produtos/{pid}/` no cérebro, revise e assine a versão.\n\n" + "\n\n".join(blocks)
    )
    url = create_issue(ctx, f"Produto: {pid}", body, ["nexo", "produto"])
    ctx.audit("product.proposed", {"product": pid, "issue": url})
    return cast("str | None", url)
