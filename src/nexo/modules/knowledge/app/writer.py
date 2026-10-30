"""Agente writer: documentação viva do projeto e base de conhecimento no cérebro.

- O contexto é montado sem IA: o que a entrega mudou, o que está ligado a isso no grafo, contratos, ADRs,
  recursos de infraestrutura e o refinamento da história.
- O agente escreve só documentação (perfil de política `doc-writer`); qualquer outra alteração é bloqueada.
- Ao fim da feature, um resumo determinístico dos documentos vira conhecimento no cérebro, por PR revisado.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.platform import config, flags
from nexo.kernel.runtime import yamlfile
from nexo.kernel.security.redaction import find
from nexo.kernel.workspace import codemap, git, specs
from nexo.modules.planning.app.clarify import feature_for_story

STANDARD_DOCS = (
    "docs/arquitetura.md",
    "docs/servicos.md",
    "docs/eventos.md",
    "docs/apis.md",
    "docs/relacionamentos.md",
    "docs/dominio.md",
    "docs/adr/README.md",
    "docs/runbook.md",
    "docs/historico.md",
)
KNOWLEDGE_SECTIONS = (
    ("docs/servicos.md", "Serviços"),
    ("docs/eventos.md", "Eventos"),
    ("docs/apis.md", "APIs"),
    ("docs/relacionamentos.md", "Relacionamentos"),
    ("docs/adr/README.md", "Decisões"),
    ("docs/historico.md", "Histórico"),
)
TF_RESOURCE = re.compile(r'^\s*resource\s+"(aws_[a-z0-9_]+)"\s+"([\w-]+)"', re.M)
MAX_CONTEXT = 12_000
MAX_SECTION = 4_000


def mode(ctx: AppContext) -> str:
    """`docs.writer`: auto (documenta ao fim de cada história, no mesmo PR), manual (só pelo comando) ou off."""
    value = str((config.resolved(ctx).values.get("docs") or {}).get("writer") or "manual")
    return value if value in ("auto", "manual", "off") else "manual"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "servico"


def infra_resources(root: Path) -> list[str]:
    out = []
    for rel in codemap.working_files(root):
        if rel.endswith(".tf") and (root / rel).is_file():
            for kind, name in TF_RESOURCE.findall((root / rel).read_text(encoding="utf-8", errors="replace")):
                out.append(f"{kind}.{name} ({rel})")
    return sorted(set(out))


def context(ctx: AppContext, root: Path, changed: list[str], story_id: str | None = None) -> str:
    """Contexto do writer: o que mudou e o que está ligado, com caminhos (o agente lê o conteúdo)."""
    from nexo.modules.knowledge.app import graph

    parts: list[str] = []
    if story_id:
        folder = feature_for_story(ctx.project_root or root, story_id)
        backlog = specs.load_backlog(folder) if folder else None
        story = next((s for s in backlog.stories if s.id == story_id), None) if backlog else None
        if story is not None:
            parts.append(
                f"## História {story.id}: {story.title}\nComo {story.as_a or '—'}, quero {story.i_want or '—'}, para"
                f" {story.so_that or '—'}.\nCritérios: {', '.join(story.ac_refs) or '—'}"
            )
        ref = folder / "refinamento" / f"{story_id}.yaml" if folder else None
        if ref is not None and ref.is_file():
            data = dict(yamlfile.load(ref))
            keep = {k: data.get(k) for k in ("observabilidade", "deploy", "dados", "dependencias") if data.get(k)}
            if keep:
                parts.append("## Refinamento (trechos)\n" + json.dumps(keep, ensure_ascii=False, indent=1))
    if changed:
        parts.append("## Arquivos alterados nesta entrega\n" + "\n".join(f"- {f}" for f in changed[:80]))
        try:
            rel = graph.related(ctx, changed)
        except (NexoError, OSError, ValueError):
            rel = {}
        linked = [f"- {k}: {', '.join(v[:15])}" for k, v in rel.items() if v]
        if linked:
            parts.append("## Ligado aos arquivos alterados (grafo)\n" + "\n".join(linked))
    files = codemap.working_files(root)
    contracts = [f for f in files if f.startswith("contracts/")]
    adrs = [f for f in files if re.match(r"(docs|spec)/adr/\d", f)]
    docs = [f for f in files if f.startswith("docs/") or f.endswith("README.md")]
    for title, items in (
        ("Contratos", contracts),
        ("ADRs", adrs),
        ("Recursos de infraestrutura (Terraform)", infra_resources(root)),
        ("Documentação existente", docs),
    ):
        if items:
            parts.append(f"## {title}\n" + "\n".join(f"- {i}" for i in items[:60]))
    text = "\n\n".join(parts) or "Sem contexto adicional: documente o repositório a partir do código."
    return text[:MAX_CONTEXT]


def doc_targets(root: Path) -> list[str]:
    existing = [f for f in codemap.working_files(root) if f.startswith("docs/") and f.endswith(".md")]
    return sorted({*STANDARD_DOCS, *existing, "README.md"})[:40]


def allowed_change(path: str) -> bool:
    from nexo.kernel.security.policy import DOC_GLOBS, _matches

    return _matches(path, DOC_GLOBS) is not None


def update(ctx: AppContext, target: str | None = None) -> dict[str, Any]:
    """`nexo docs update [US-n|F-n]`: roda o writer sobre o repositório atual; o resultado fica numa branch."""
    flags.require(ctx, "writer")
    if mode(ctx) == "off":
        raise NexoError("agente writer desligado (docs.writer: off)")
    root = ctx.project_root
    if root is None:
        raise NexoError("rode dentro de um repositório com o Nexo (nexo init)")
    from nexo.kernel.workspace import tasks
    from nexo.modules.execution.app.run import Orchestrator, RunOptions

    stories: list[str] = []
    if target and target.upper().startswith("F-"):
        backlog = specs.load_backlog(specs.feature_dir(root, target.upper()))
        stories = [s.id for s in backlog.stories] if backlog else []
    elif target:
        stories = [target]
    changed = _story_changes(ctx, root, stories)
    desc = "\n\n".join(context(ctx, root, changed, s) for s in stories) if stories else context(ctx, root, changed)
    task_id = f"DOC-{(target or 'REPO').upper()}-{_stamp()}"
    yamlfile.dump(
        tasks.tasks_dir(root) / f"{task_id}.yaml",
        {
            "title": f"Atualizar a documentação ({target or 'repositório'})",
            "kind": "docs",
            "description": desc[:MAX_CONTEXT],
            "acceptance": [
                "documentos afetados atualizados com as fontes citadas",
                "docs/relacionamentos.md com o diagrama de quem chama quem",
                "docs/historico.md com a entrega",
            ],
            "files_hint": doc_targets(root),
            **({"story": stories[0]} if len(stories) == 1 else {}),
        },
    )
    opts = RunOptions(role="writer", profile_policy="doc-writer")
    rep = Orchestrator(ctx, opts).run_task(task_id)
    ctx.audit("docs.updated", {"target": target or "repo", "task": task_id, "state": rep.state}, ref=task_id)
    return {"task": task_id, "state": rep.state, "branch": rep.branch, "reason": rep.reason}


def _stamp() -> str:
    import time

    return time.strftime("%Y%m%d%H%M%S")


def _story_changes(ctx: AppContext, root: Path, stories: list[str]) -> list[str]:
    """Arquivos alterados pelas histórias (branch da história contra a base registrada na execução)."""
    out: set[str] = set()
    for sid in stories:
        row = ctx.conn.execute(
            "SELECT base_sha, branch FROM runs WHERE target_id = ? AND branch IS NOT NULL"
            " ORDER BY started_at DESC LIMIT 1",
            (sid,),
        ).fetchone()
        if row and row["base_sha"] and row["branch"]:
            diff = git.git(["diff", "--name-only", f"{row['base_sha']}...{row['branch']}"], root, check=False)
            out |= {ln for ln in diff.splitlines() if ln}
    return sorted(out)


# ---------------------------------------------------------------- base de conhecimento no cérebro


def knowledge(ctx: AppContext, root: Path) -> tuple[str, str]:
    """Resumo determinístico dos documentos para o cérebro: (caminho no cérebro, conteúdo)."""
    cfg = config.resolved(ctx).values
    product = _slug(str(cfg.get("product") or cfg.get("squad") or "produto"))
    service = _slug(str(cfg.get("project") or root.name))
    lines = [
        f"# {service}",
        "",
        f"Conhecimento do serviço `{service}` (produto `{product}`), gerado pelo agente writer a partir de `docs/`"
        " do repositório. Fonte oficial: o repositório; este resumo serve para busca pelos agentes de outros squads.",
        "",
    ]
    found = 0
    for rel, title in KNOWLEDGE_SECTIONS:
        path = root / rel
        if not path.is_file():
            continue
        body = path.read_text(encoding="utf-8", errors="replace").strip()
        body = re.sub(r"^#\s.*\n", "", body, count=1).strip()  # o título vira a seção
        if len(body) > MAX_SECTION:
            body = body[:MAX_SECTION].rsplit("\n", 1)[0] + "\n\n(resumo cortado; ver o repositório)"
        lines += [f"## {title}", "", body, ""]
        found += 1
    if not found:
        raise NexoError("nenhum documento em docs/ para virar conhecimento (rode `nexo docs update` antes)")
    return f"knowledge/{product}/{service}.md", "\n".join(lines).rstrip() + "\n"


def publish_knowledge(ctx: AppContext, dry_run: bool = False) -> dict[str, Any]:
    """`nexo docs knowledge`: propõe o resumo no cérebro por PR (ou issue, sem permissão de escrita)."""
    flags.require(ctx, "writer")
    root = ctx.project_root
    if root is None:
        raise NexoError("rode dentro de um repositório com o Nexo (nexo init)")
    path, content = knowledge(ctx, root)
    sensitive = {f.kind for f in find(content)} - {"email"}
    if sensitive:
        raise NexoError(f"a documentação contém dado sensível ({', '.join(sorted(sensitive))}); corrija antes")
    local = root / ".nexo" / path
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(content, encoding="utf-8")
    result: dict[str, Any] = {"path": path, "local": str(local), "chars": len(content), "pr": None, "issue": None}
    if dry_run:
        return result
    from nexo.modules.knowledge.app import brain

    repo = str(brain._cfg(ctx).get("github_repo") or "")
    if repo:
        try:
            from nexo.modules.delivery.adapters.scm.github import GitHubScm
            from nexo.modules.demands.app.tracker import github_token

            scm = GitHubScm(repo, github_token(ctx))
            branch = f"nexo/conhecimento/{Path(path).stem}-{_stamp()}"
            scm.commit_file(branch, path, content, f"docs: conhecimento de {Path(path).stem} (agente writer)")
            pr = scm.open_pr(
                branch,
                f"Conhecimento: {Path(path).stem}",
                "Resumo gerado pelo agente writer a partir de `docs/` do repositório do serviço.\n\n"
                "Revise o conteúdo; ao aprovar, a versão do cérebro precisa ser assinada no release.",
            )
            result["pr"] = pr.get("html_url") or pr.get("number")
        except (NexoError, OSError) as exc:
            result["error"] = str(exc)
    if not result["pr"]:
        result["issue"] = brain.propose(ctx, local, f"Conhecimento: {Path(path).stem}")
    ctx.audit("docs.knowledge", {"path": path, "pr": result["pr"], "issue": result["issue"]})
    return result
