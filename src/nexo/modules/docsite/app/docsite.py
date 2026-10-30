"""Base de conhecimento do projeto em Docusaurus 3. O Nexo gera o site; publicá-lo fica com quem hospeda.

O site é uma **vista** gerada, não uma fonte: as fontes são o `.nexo/` do projeto (specs, requisito técnico, ADRs,
histórias, execuções) e o cérebro da org (projeto e glossário). Nada é editado no site; mudar uma informação é mudar
a fonte e gerar de novo (`nexo docs site`). Os agentes leem as fontes, nunca o site.

- `scaffold`: cria o projeto Docusaurus (package.json, configuração e CSS com as cores da marca). Não sobrescreve o
  que já existe e não cria workflow do GitHub Actions.
- `sync`: regenera `docs/` com início, projeto, features (requisitos, análise técnica, requisito técnico, decisões,
  histórias), entregas, decisões e mapa do código.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from nexo.kernel.context import PROJECT_DIR, AppContext
from nexo.kernel.platform import config
from nexo.kernel.workspace import git, specs

DOCUSAURUS = "3.10.2"
SEARCH = "0.55.3"
STATE_LABEL = {
    "Concluido": "concluída",
    "AguardandoHumano": "aguardando decisão",
    "EmDev": "em andamento",
    "EmQA": "em teste",
    "EmReview": "em revisão",
    "Pronto": "pronta",
    "Refinamento": "em refinamento",
    "Escalado": "escalada",
    "Bloqueado": "bloqueada",
    "Ideia": "ideia",
}


def settings(ctx: AppContext) -> dict[str, Any]:
    publish = (config.resolved(ctx).values.get("docs") or {}).get("publish") or {}
    return dict(publish.get("docusaurus") or {}) if isinstance(publish, dict) else {}


def site_dir(ctx: AppContext, root: Path) -> Path:
    return root / str(settings(ctx).get("path") or "docs-site")


def _front(title: str, position: int | None = None, slug: str | None = None) -> str:
    lines = ["---", f"title: {json.dumps(title, ensure_ascii=False)}"]
    if position is not None:
        lines.append(f"sidebar_position: {position}")
    if slug:
        lines.append(f"slug: {slug}")
    lines += ["---", ""]
    return "\n".join(lines)


def _strip_h1(text: str) -> str:
    """O título vai no front matter; o primeiro `# título` do documento sairia repetido."""
    return re.sub(r"\A\s*#\s[^\n]*\n+", "", text, count=1)


def _adr_title(path: Path) -> str:
    found = re.match(r"\s*#\s+(.+)", path.read_text(encoding="utf-8"))
    return found.group(1).strip() if found else path.stem


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------- projeto Docusaurus


def _site_url(ctx: AppContext) -> tuple[str, str]:
    s = settings(ctx)
    if s.get("url"):
        return str(s["url"]).rstrip("/"), str(s.get("base_url") or "/")
    repo = str((config.resolved(ctx).values.get("scm") or {}).get("repo") or "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner}.github.io", f"/{name}/"
    return "https://exemplo.github.io", "/"


def _brand_css(ctx: AppContext) -> str:
    from nexo.kernel import services

    brand = services.load("setup.brand")

    try:
        b = brand.load(ctx.brain_dir)
    except Exception:
        b = brand.default()
    light, dark = b.light, b.dark
    return (
        "/* Cores da marca (brand.yaml do cérebro da org). Gerado pelo Nexo. */\n"
        f":root {{\n  --ifm-color-primary: {light['accent']};\n  --ifm-color-primary-dark: {light['accent']};\n"
        f"  --ifm-color-primary-darker: {light['accent']};\n  --ifm-color-primary-darkest: {light['accent']};\n"
        f"  --ifm-color-primary-light: {light['accent']};\n  --ifm-color-primary-lighter: {light['accent']};\n"
        f"  --ifm-color-primary-lightest: {light['accent']};\n  --ifm-background-color: {light['bg']};\n"
        f"  --ifm-font-family-base: {b.fonts['sans']};\n  --ifm-font-family-monospace: {b.fonts['mono']};\n}}\n"
        f"[data-theme='dark'] {{\n  --ifm-color-primary: {dark['accent']};\n"
        f"  --ifm-color-primary-dark: {dark['accent']};\n"
        f"  --ifm-color-primary-darker: {dark['accent']};\n  --ifm-color-primary-darkest: {dark['accent']};\n"
        f"  --ifm-color-primary-light: {dark['accent']};\n  --ifm-color-primary-lighter: {dark['accent']};\n"
        f"  --ifm-color-primary-lightest: {dark['accent']};\n  --ifm-background-color: {dark['bg']};\n}}\n"
    )


def scaffold(ctx: AppContext, root: Path) -> list[str]:
    """Cria o projeto Docusaurus (só o que ainda não existe)."""
    site = site_dir(ctx, root)
    name = str(config.resolved(ctx).values.get("project") or root.name)
    url, base = _site_url(ctx)
    rel = site.relative_to(root).as_posix()
    files = {
        "package.json": json.dumps(
            {
                "name": f"{re.sub(r'[^a-z0-9-]+', '-', name.lower()).strip('-') or 'projeto'}-base-de-conhecimento",
                "private": True,
                "scripts": {"start": "docusaurus start", "build": "docusaurus build", "serve": "docusaurus serve"},
                "dependencies": {
                    "@docusaurus/core": DOCUSAURUS,
                    "@docusaurus/preset-classic": DOCUSAURUS,
                    "@easyops-cn/docusaurus-search-local": SEARCH,
                    "@mdx-js/react": "^3.0.0",
                    "clsx": "^2.0.0",
                    "react": "^18.3.1",
                    "react-dom": "^18.3.1",
                },
                "engines": {"node": ">=18.0"},
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        "docusaurus.config.js": f"""// Gerado pelo Nexo uma vez; ajuste à vontade (não é sobrescrito).
// @ts-check
/** @type {{import('@docusaurus/types').Config}} */
const config = {{
  title: {json.dumps(name, ensure_ascii=False)},
  tagline: 'Base de conhecimento do projeto: o que foi decidido e o que foi entregue',
  url: {json.dumps(url)},
  baseUrl: {json.dumps(base)},
  onBrokenLinks: 'warn',
  markdown: {{ format: 'detect', hooks: {{ onBrokenMarkdownLinks: 'warn' }} }},
  i18n: {{ defaultLocale: 'pt-BR', locales: ['pt-BR'] }},
  presets: [
    ['classic', {{
      docs: {{ routeBasePath: '/', sidebarPath: require.resolve('./sidebars.js') }},
      blog: false,
      theme: {{ customCss: require.resolve('./src/css/custom.css') }},
    }}],
  ],
  themes: [
    [require.resolve('@easyops-cn/docusaurus-search-local'), {{
      hashed: true, language: ['pt'], indexBlog: false, docsRouteBasePath: '/',
    }}],
  ],
  themeConfig: {{
    navbar: {{
      title: {json.dumps(name, ensure_ascii=False)},
      items: [
        {{ to: '/entregas', label: 'Entregas', position: 'left' }},
        {{ to: '/decisoes', label: 'Decisões', position: 'left' }},
        {{ to: '/codigo', label: 'Código', position: 'left' }},
      ],
    }},
    colorMode: {{ respectPrefersColorScheme: true }},
    footer: {{ style: 'dark', copyright: 'Gerado pelo Nexo a partir do repositório e do cérebro da org.' }},
  }},
}};
module.exports = config;
""",
        "sidebars.js": (
            "// Navegação gerada a partir das pastas de docs/.\n"
            "module.exports = { docs: [{ type: 'autogenerated', dirName: '.' }] };\n"
        ),
        "src/css/custom.css": _brand_css(ctx),
        "README.md": (
            f"# Base de conhecimento de {name}\n\n"
            "Gerada pelo Nexo (`nexo docs site`). **Não edite `docs/`**: o conteúdo vem do `.nexo/` do projeto e do "
            "cérebro da org e é regenerado a cada aprovação de plano e entrega. Para mudar uma informação, mude a "
            "fonte.\n\nVer localmente:\n\n```bash\nnpm install\nnpm start\n```\n\n"
            "Publicação: `npm run build` gera `build/`, para hospedar onde a empresa definir. O Nexo não cria "
            "workflow do GitHub Actions.\n"
        ),
        ".gitignore": "node_modules/\nbuild/\n.docusaurus/\n",
    }
    created: list[str] = []
    for rel_path, text in files.items():
        path = site / rel_path
        if not path.exists():
            _write(path, text)
            created.append(f"{rel}/{rel_path}")
    if created:
        ctx.audit("docs.site_scaffold", {"files": created})
    return created


# ---------------------------------------------------------------------- conteúdo


def _features(root: Path) -> list[Path]:
    base = specs.specs_root(root)
    return sorted(f for f in base.glob("F-*") if f.is_dir()) if base.is_dir() else []


def _fid(folder: Path) -> str:
    return "-".join(folder.name.split("-")[:2])


def _feature_state(store: Any, folder: Path) -> str:
    """Pelo que foi entregue: concluída, "n de m histórias entregues", planejada ou o estado do refinamento."""
    backlog = specs.load_backlog(folder)
    if backlog is None or not backlog.stories:
        state = store.item_state(_fid(folder)) or "Refinamento"
        return STATE_LABEL.get(state, state)
    done = sum(1 for s in backlog.stories if store.item_state(s.id) == "Concluido")
    total = len(backlog.stories)
    if done == total:
        return "concluída"
    return f"{done} de {total} histórias entregues" if done else "planejada"


def _store(ctx: AppContext, root: Path) -> Any:
    from nexo.store.runs import RunStore, project_key

    return RunStore(ctx.conn, project_key(root))


def _story_runs(ctx: AppContext, root: Path, story_id: str) -> dict[str, Any]:
    store = _store(ctx, root)
    for run in reversed(store.runs(story_id)):
        path = root / PROJECT_DIR / "runs" / run.id / "story.json"
        if path.is_file():
            return {"run": run, **json.loads(path.read_text(encoding="utf-8"))}
    return {}


def _approver(ctx: AppContext, story_id: str) -> str:
    row = ctx.conn.execute(
        "SELECT payload FROM events WHERE type = 'gate.decision' AND ref = ? ORDER BY seq DESC LIMIT 1", (story_id,)
    ).fetchone()
    if row is None:
        return ""
    try:
        data = json.loads(row["payload"])
    except (TypeError, ValueError):
        return ""
    return str(data.get("approver") or ("automático" if data.get("decision") == "auto" else ""))


def _feature_pages(ctx: AppContext, root: Path, docs: Path, folder: Path, position: int) -> list[dict[str, Any]]:
    """Uma pasta por feature: visão geral, requisitos, análise, requisito técnico, decisões e histórias."""
    fid = _fid(folder)
    spec = specs.load_spec(folder)
    store = _store(ctx, root)
    out = docs / "features" / folder.name
    _write(
        out / "_category_.json", json.dumps({"label": f"{fid} {spec.title}", "position": position}, ensure_ascii=False)
    )
    backlog = specs.load_backlog(folder)
    stories = backlog.stories if backlog else []
    rows = []
    delivered: list[dict[str, Any]] = []
    for s in stories:
        state = store.item_state(s.id) or "Pronto"
        info = _story_runs(ctx, root, s.id)
        pr = str(info.get("pr") or "")
        rows.append(
            f"| [{s.id}](historias/{s.id}) | {s.title} | {STATE_LABEL.get(state, state)} | {pr and f'[PR]({pr})'} |"
        )
        if state == "Concluido":
            delivered.append({"story": s.id, "title": s.title, "feature": fid, "info": info, "folder": folder})
    overview = [
        _front(f"{fid} — {spec.title}", 1, None),
        f"**Estado**: {_feature_state(store, folder)}  ",
        f"**Objetivo**: {spec.objective or '—'}\n",
    ]
    if spec.problem:
        overview.append(f"**Problema**: {spec.problem}\n")
    overview += ["## Histórias", "", "| história | título | estado | PR |", "|---|---|---|---|", *rows, ""]
    docs_list = [
        ("spec.md", "Especificação", 2),
        ("requisitos.md", "Requisitos funcionais", 3),
        ("analise-tecnica.md", "Análise técnica", 4),
        ("requisito-tecnico.md", "Requisito técnico", 5),
    ]
    overview += ["## Documentos", ""]
    for name, title, pos in docs_list:
        src = folder / name
        if src.is_file():
            _write(out / name, _front(title, pos) + _strip_h1(src.read_text(encoding="utf-8")))
            overview.append(f"- [{title}]({name.removesuffix('.md')})")
    adrs = sorted((folder / "adr").glob("*.md")) if (folder / "adr").is_dir() else []
    for i, adr in enumerate(adrs, start=1):
        text = adr.read_text(encoding="utf-8")
        # o Docusaurus tira o número do início do nome (0001-…) ao montar o endereço: "adr-" mantém o link estável
        _write(out / "decisoes" / f"adr-{adr.name}", _front(_adr_title(adr), i) + _strip_h1(text))
    if adrs:
        _write(out / "decisoes" / "_category_.json", json.dumps({"label": "Decisões", "position": 6}))
        overview.append(f"- Decisões: {len(adrs)} ADR(s)")
    for i, s in enumerate(stories, start=1):
        src = folder / "backlog" / f"{s.id}.md"
        text = _strip_h1(src.read_text(encoding="utf-8")) if src.is_file() else f"{s.title}\n"
        _write(out / "historias" / f"{s.id}.md", _front(f"{s.id} — {s.title}", i) + text)
    if stories:
        _write(out / "historias" / "_category_.json", json.dumps({"label": "Histórias", "position": 7}))
    _write(out / "index.md", "\n".join(overview) + "\n")
    return delivered


def _deliveries_page(root: Path, docs: Path, delivered: list[dict[str, Any]], approver: Any) -> None:
    lines = [
        _front("Entregas", 2, "/entregas"),
        "O que foi desenvolvido, história por história (mais recente primeiro).",
        "",
        "| quando | história | feature | o que mudou | aprovado por | PR |",
        "|---|---|---|---|---|---|",
    ]
    items = sorted(delivered, key=lambda d: str(getattr(d["info"].get("run"), "ended_at", "") or ""), reverse=True)
    for d in items:
        info = d["info"]
        run = info.get("run")
        when = str(getattr(run, "ended_at", "") or "")[:10]
        branch, base = str(info.get("branch") or ""), str(info.get("base") or "")
        changed = ""
        if branch and base and git.branch_exists(root, branch):
            try:
                changed = git.git(["diff", "--shortstat", f"{base}...{branch}"], root, check=False).strip()
            except Exception:
                changed = ""
        pr = str(info.get("pr") or "")
        folder_rel = f"features/{d['folder'].name}/historias/{d['story']}"
        lines.append(
            f"| {when} | [{d['story']}]({folder_rel}) {d['title']} | {d['feature']} | {changed or '—'} | "
            f"{approver(d['story']) or '—'} | {pr and f'[PR]({pr})'} |"
        )
    if not items:
        lines.append("| — | nenhuma história concluída ainda | | | | |")
    _write(docs / "entregas.md", "\n".join(lines) + "\n")


def _decisions_page(root: Path, docs: Path) -> None:
    lines = [_front("Decisões", 3, "/decisoes"), "Todas as decisões do projeto (ADRs), por feature.", ""]
    found = False
    for folder in _features(root):
        adrs = sorted((folder / "adr").glob("*.md")) if (folder / "adr").is_dir() else []
        if adrs:
            found = True
            lines.append(f"## {_fid(folder)} — {specs.load_spec(folder).title}\n")
            for adr in adrs:
                lines.append(f"- [{_adr_title(adr)}](features/{folder.name}/decisoes/adr-{adr.stem})")
            lines.append("")
    if not found:
        lines.append("Nenhuma decisão registrada ainda.")
    lines += ["", "Decisões que valem para toda a org ficam no cérebro da org, não aqui."]
    _write(docs / "decisoes.md", "\n".join(lines) + "\n")


def _code_page(root: Path, docs: Path) -> None:
    from nexo.kernel import services

    discover = services.load("planning.discover")

    data = discover.load_map(root) or {}
    lines = [_front("Mapa do código", 4, "/codigo"), "Gerado pelo `nexo discover` (sem IA).", ""]
    files = discover.load_files(root)
    if not files:
        lines.append("O mapa ainda não foi gerado: rode `nexo discover`.")
    else:
        langs = data.get("languages") or {}
        if langs:
            lines.append("**Linguagens**: " + ", ".join(f"{k} ({v})" for k, v in langs.items()) + "\n")
        entry = data.get("entrypoints") or []
        if entry:
            lines += ["## Pontos de entrada", "", *[f"- `{e}`" for e in entry[:20]], ""]
        lines += ["## Arquivos principais", "", "| arquivo | símbolos |", "|---|---|"]
        for f in sorted((f for f in files if not f.is_test), key=lambda f: -f.hotspot)[:60]:
            symbols = ", ".join(f.symbols[:6]) + ("…" if len(f.symbols) > 6 else "")
            lines.append(f"| `{f.path}` | {symbols or '—'} |")
    conventions = discover.map_dir(root) / "convencoes.md"
    if conventions.is_file():
        lines += ["", "## Convenções do repositório", "", _strip_h1(conventions.read_text(encoding="utf-8"))]
    _write(docs / "codigo.md", "\n".join(lines) + "\n")


def _product_page(ctx: AppContext, docs: Path) -> bool:
    from nexo.kernel import services

    pc = services.load("planning.product_context")
    from nexo.kernel import product as model

    cur = pc.current(ctx)
    if cur.product is None:
        return False
    lines = [
        _front(f"Projeto: {cur.product.label}", 5, "/projeto"),
        ":::info Fonte: cérebro da org",
        "Esta página é uma cópia de leitura. Para mudar o projeto ou o glossário, proponha a mudança ao cérebro "
        f"(`nexo context propose {cur.product.id}`).",
        ":::",
        "",
        model.describe(cur.product, cur.part_id).replace("\n", "  \n"),
        "",
    ]
    if cur.folder is not None:
        for name, title in (("contexto.md", "Contexto"), ("glossario.md", "Glossário")):
            if (cur.folder / name).is_file():
                lines += [f"## {title}", "", _strip_h1((cur.folder / name).read_text(encoding="utf-8")), ""]
    _write(docs / "projeto.md", "\n".join(lines) + "\n")
    return True


def sync(ctx: AppContext, root: Path | None = None) -> dict[str, Any]:
    """Regenera `docs/` da base de conhecimento a partir das fontes (o resto do site não é tocado)."""
    root = root or ctx.project_root
    if root is None:
        from nexo.kernel.errors import NexoError

        raise NexoError("nenhum projeto encontrado; rode `nexo init`")
    scaffold(ctx, root)
    site = site_dir(ctx, root)
    docs = site / "docs"
    if docs.exists():
        shutil.rmtree(docs)
    values = config.resolved(ctx).values
    name = str(values.get("project") or root.name)
    about = root / PROJECT_DIR / "contexto.md"
    delivered: list[dict[str, Any]] = []
    features = _features(root)
    for i, folder in enumerate(features, start=1):
        delivered += _feature_pages(ctx, root, docs, folder, 10 + i)
    _write(docs / "features" / "_category_.json", json.dumps({"label": "Features", "position": 6}))
    home = [
        _front(name, 1, "/"),
        _strip_h1(about.read_text(encoding="utf-8")) if about.is_file() else "Base de conhecimento do projeto.",
        "",
        f"**Stack**: {', '.join(str(s) for s in values.get('stacks') or []) or '—'}",
        "",
        "## Features",
        "",
    ]
    store = _store(ctx, root)
    for folder in features:
        title = specs.load_spec(folder).title
        home.append(f"- [{_fid(folder)} — {title}](features/{folder.name}) · {_feature_state(store, folder)}")
    if not features:
        home.append("Nenhuma feature planejada ainda.")
    home += ["", "Veja também: [Entregas](entregas) · [Decisões](decisoes) · [Mapa do código](codigo)."]
    has_product = _product_page(ctx, docs)
    if has_product:
        home.append("Este projeto faz parte de um projeto: [Projeto e glossário](projeto).")
    _write(docs / "index.md", "\n".join(home) + "\n")
    _deliveries_page(root, docs, delivered, lambda sid: _approver(ctx, sid))
    _decisions_page(root, docs)
    _code_page(root, docs)
    ctx.audit("docs.site_sync", {"features": len(features), "delivered": len(delivered), "product": has_product})
    return {"site": str(site), "features": len(features), "delivered": len(delivered)}
