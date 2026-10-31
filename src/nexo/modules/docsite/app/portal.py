"""Portal do projeto: um site Docusaurus que junta a documentação de todos os repositórios do projeto.

Cada parte do projeto (visão ou componente) vive num repositório próprio. O portal é montado no CI, sem IA, a partir
de duas fontes que já existem:

- o cérebro da org: `projetos/<id>/projeto.yaml` (partes, quem usa quem, squads), `arquitetura.md` e `decisoes/`;
- uma cópia de cada repositório (baixada pelo CI, só leitura): `.nexo/specs/` (features, requisito técnico, ADRs),
  `.nexo/entregas/` (uma por história) e o código (rotas expostas e outras partes citadas).

Nada é escrito à mão em `docs/`: o portal é refeito a cada execução. O esqueleto do site (configuração, cores da
marca) é criado uma vez e nunca sobrescrito. O que precisa de atenção também vai para `atencao.json`, para o CI.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from nexo.kernel import services
from nexo.kernel.context import PROJECT_DIR, AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.product import Part, Product
from nexo.kernel.runtime import yamlfile
from nexo.kernel.workspace import codemap, git, specs
from nexo.modules.docsite.app.docsite import DOCUSAURUS, SEARCH, _adr_title, _brand_css, _front, _strip_h1, _write
from nexo.modules.docsite.domain import portal as model

pc = services.load("planning.product_context")

CONFIG_SUFFIXES = {".yaml", ".yml", ".json", ".properties", ".env", ".toml", ".ini", ".xml", ".config", ".conf"}
SKIP_FILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "packages.lock.json"}
MAX_BYTES = 300_000
ELK = "0.1.9"
KIND_FOLDER = {"visao": "visoes", "componente": "componentes"}
ATTENTION_LABEL = {"desvio": "desvio", "completude": "completude", "dono": "dono", "leitura": "leitura"}


@dataclass
class Feature:
    id: str
    title: str
    folder: Path
    objective: str
    stories: list[tuple[str, str]]
    technical: bool
    adrs: list[Path]
    mentions: list[str]  # outras partes citadas no texto da feature


@dataclass
class RepoRead:
    part: Part
    root: Path | None
    head: str = ""
    features: list[Feature] = field(default_factory=list)
    deliveries: list[dict[str, Any]] = field(default_factory=list)
    endpoints: list[str] = field(default_factory=list)
    cites: dict[str, list[str]] = field(default_factory=dict)

    def delivered(self) -> dict[str, dict[str, Any]]:
        return {str(d.get("story")): d for d in self.deliveries}


# ---------------------------------------------------------------------- leitura dos repositórios


def _repo_name(part: Part) -> str:
    return part.repo.rsplit("/", 1)[-1] if part.repo else ""


def locate(repos: Path, part: Part) -> Path | None:
    """Onde o repositório da parte foi baixado: `<repos>/<nome do repositório>` ou `<repos>/<id da parte>`."""
    names = [_repo_name(part)] if part.repo else []
    names.append(part.id)
    for name in names:
        candidate = repos / name / part.path if part.path else repos / name
        if candidate.is_dir():
            return candidate
    if part.path and (repos / part.path).is_dir():
        return repos / part.path
    return None


def _code_files(root: Path) -> list[str]:
    out = []
    for rel in codemap.list_files(root):
        path = Path(rel)
        if path.name in SKIP_FILES or path.parts[0] in ("docs", "docs-site", PROJECT_DIR):
            continue
        if path.suffix in codemap.LANGS or path.suffix in CONFIG_SUFFIXES or path.name.startswith(".env"):
            out.append(rel)
    return out


def _features(product: Product, part: Part, root: Path) -> list[Feature]:
    base = specs.specs_root(root)
    out = []
    for folder in sorted(base.glob("F-*")) if base.is_dir() else []:
        if not folder.is_dir():
            continue
        try:
            spec = specs.load_spec(folder)
            backlog = specs.load_backlog(folder)
        except (NexoError, OSError, ValueError):
            continue
        text = " ".join([spec.title, spec.objective, spec.problem, *spec.scope_in])
        mentions = [p.id for p in _mentioned(product, text)]
        for story in backlog.stories if backlog else []:  # História feita em outro repositório do programa
            touched = next((p for p in product.parts if story.repo and story.repo in (p.id, _repo_name(p))), None)
            if touched is not None and touched.id not in mentions:
                mentions.append(touched.id)
        out.append(
            Feature(
                id=spec.id,
                title=spec.title,
                folder=folder,
                objective=spec.objective,
                stories=[(s.id, s.title) for s in backlog.stories] if backlog else [],
                technical=(folder / "requisito-tecnico.md").is_file(),
                adrs=sorted((folder / "adr").glob("*.md")) if (folder / "adr").is_dir() else [],
                mentions=[m for m in mentions if m != part.id],
            )
        )
    return out


def _mentioned(product: Product, text: str) -> list[Part]:
    """Partes citadas no texto da feature: id, nome ou apelido (o texto é escrito por pessoas)."""
    lowered = text.lower()
    found = []
    for p in product.parts:
        names = {p.id, p.name, *p.aliases} - {""}
        if any(re.search(rf"(?<![\w-]){re.escape(n.lower())}(?![\w-])", lowered) for n in names):
            found.append(p)
    return found


def scan_code(product: Product, part: Part, root: Path) -> tuple[list[str], dict[str, list[str]]]:
    """Rotas que o repositório expõe e as outras partes do projeto citadas no código e na configuração."""
    others = {p.id: model.code_names(p) for p in product.parts if p.id != part.id}
    endpoints: set[str] = set()
    cites: dict[str, list[str]] = {}
    for rel in _code_files(root):
        path = root / rel
        try:
            if path.stat().st_size > MAX_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if path.suffix in codemap.LANGS:
            endpoints.update(model.endpoints(text))
        for target, names in others.items():
            if model.cites(text, names):
                cites.setdefault(target, []).append(rel)
    return sorted(endpoints), cites


def read_repo(product: Product, part: Part, root: Path | None) -> RepoRead:
    read = RepoRead(part=part, root=root)
    if root is None:
        return read
    read.head = (git.head(root) or "")[:7] if git.is_repo(root) else ""
    read.endpoints, read.cites = scan_code(product, part, root)
    read.features = _features(product, part, root)
    folder = root / PROJECT_DIR / "entregas"
    for item in sorted(folder.glob("*.yaml")) if folder.is_dir() else []:
        data = yamlfile.load(item)
        if isinstance(data, dict) and data.get("story"):
            read.deliveries.append(data)
    return read


def own_deviations(ctx: AppContext, root: Path) -> list[tuple[Part, list[str]]]:
    """Desvios deste repositório: outras partes do projeto que o código usa sem estar em `uses` (o mesmo que o portal
    aponta). A varredura fica em cache por commit em `.nexo/map/ligacoes.json` (fora do git)."""
    cur = pc.current(ctx)
    if cur.product is None or not cur.part_id:
        return []
    part = cur.product.part(cur.part_id)
    if part is None:
        return []
    head = (git.head(root) or "") if git.is_repo(root) else ""
    key = f"{head}:{','.join(sorted(p.id for p in cur.product.parts))}"
    cache = root / PROJECT_DIR / "map" / "ligacoes.json"
    cites: dict[str, list[str]] | None = None
    if head and cache.is_file():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            cites = data["cites"] if data.get("key") == key else None
        except (ValueError, KeyError):
            cites = None
    if cites is None:
        cites = scan_code(cur.product, part, root)[1]
        if head:
            _write(cache, json.dumps({"key": key, "cites": cites}, ensure_ascii=False))
    edges = model.edges(cur.product, {part.id: cites})
    out = []
    for e in edges:
        target = cur.product.part(e.target)
        if e.source == part.id and e.kind == "deviation" and target is not None:
            out.append((target, e.evidence))
    return out


# ---------------------------------------------------------------------- esqueleto do site


def scaffold(ctx: AppContext, out: Path, product: Product) -> list[str]:
    """Cria o projeto Docusaurus do portal (com diagramas Mermaid e busca local). Não sobrescreve nada."""
    name = product.titled
    files = {
        "package.json": json.dumps(
            {
                "name": f"{product.id}-portal",
                "private": True,
                "scripts": {"start": "docusaurus start", "build": "docusaurus build", "serve": "docusaurus serve"},
                "dependencies": {
                    "@docusaurus/core": DOCUSAURUS,
                    "@docusaurus/preset-classic": DOCUSAURUS,
                    "@docusaurus/theme-mermaid": DOCUSAURUS,
                    "@easyops-cn/docusaurus-search-local": SEARCH,
                    "@mermaid-js/layout-elk": ELK,  # par opcional do tema Mermaid, mas o build falha sem ele
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
        "docusaurus.config.js": f"""// Gerado pelo Nexo uma vez; ajuste à vontade. docs/ é gerado no CI.
// @ts-check
/** @type {{import('@docusaurus/types').Config}} */
const config = {{
  title: {json.dumps(name, ensure_ascii=False)},
  tagline: 'Portal do projeto: desenho da solução, visões, componentes, features e decisões',
  url: process.env.PORTAL_URL || 'https://exemplo.github.io',
  baseUrl: process.env.PORTAL_BASE_URL || '/',
  onBrokenLinks: 'warn',
  markdown: {{ mermaid: true, format: 'detect', hooks: {{ onBrokenMarkdownLinks: 'warn' }} }},
  i18n: {{ defaultLocale: 'pt-BR', locales: ['pt-BR'] }},
  presets: [
    ['classic', {{
      docs: {{ routeBasePath: '/', sidebarPath: require.resolve('./sidebars.js') }},
      blog: false,
      theme: {{ customCss: require.resolve('./src/css/custom.css') }},
    }}],
  ],
  themes: [
    '@docusaurus/theme-mermaid',
    [require.resolve('@easyops-cn/docusaurus-search-local'), {{
      hashed: true, language: ['pt'], indexBlog: false, docsRouteBasePath: '/',
    }}],
  ],
  themeConfig: {{
    navbar: {{
      title: {json.dumps(name, ensure_ascii=False)},
      items: [
        {{ to: '/desenho', label: 'Desenho', position: 'left' }},
        {{ to: '/entregas', label: 'Entregas', position: 'left' }},
        {{ to: '/decisoes', label: 'Decisões', position: 'left' }},
      ],
    }},
    colorMode: {{ defaultMode: "light", disableSwitch: true, respectPrefersColorScheme: false }},
    footer: {{ style: 'dark', copyright: 'Gerado pelo Nexo dos repositórios do projeto e do cérebro.' }},
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
            f"# Portal de {name}\n\n"
            "Gerado pelo Nexo (`nexo docs portal`). **Não edite `docs/`**: o conteúdo vem do cérebro da org "
            f"(`projetos/{product.id}/`) e dos repositórios do projeto, e é refeito a cada execução.\n\n"
            "## No CI\n\n"
            "1. Instale o Nexo (pelo fluxo interno da empresa) e baixe o cérebro da org.\n"
            "2. Baixe cada repositório listado no `projeto.yaml` numa mesma pasta, com o nome do repositório "
            "(ex.: `repos/posicao-api`). Basta um clone raso, com token só de leitura.\n"
            f"3. `nexo docs portal --product {product.id} --repos repos --out .` monta `docs/` e `atencao.json`.\n"
            "4. `npm ci && npm run build` e publique `build/` no GitHub Pages da org "
            "(`PORTAL_URL` e `PORTAL_BASE_URL` definem o endereço).\n\n"
            "Rode a cada merge na branch principal de qualquer repositório do projeto e uma vez por noite. "
            "`--check` faz o comando terminar com erro quando há desvio do desenho ou entrega sem requisito técnico.\n"
        ),
        ".gitignore": "node_modules/\nbuild/\n.docusaurus/\ndocs/\natencao.json\n",
    }
    created: list[str] = []
    for rel_path, text in files.items():
        path = out / rel_path
        if not path.exists():
            _write(path, text)
            created.append(rel_path)
    if created:
        ctx.audit("docs.portal_scaffold", {"product": product.id, "files": created})
    return created


# ---------------------------------------------------------------------- páginas


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _part_link(product: Product, part_id: str) -> str:
    part = product.part(part_id)
    if part is None:
        return part_id
    if part.kind == "externo":
        return f"[{part.label}](/sistemas-externos)"
    return f"[{part.label}](/{KIND_FOLDER[part.kind]}/{part.id})"


def _feature_slug(part: Part, feature: Feature) -> str:
    return f"{part.id}-{feature.id}".lower()


def _feature_state(feature: Feature, delivered: dict[str, dict[str, Any]]) -> str:
    if not feature.stories:
        return "em planejamento"
    done = sum(1 for sid, _ in feature.stories if sid in delivered)
    if done == len(feature.stories):
        return "entregue"
    return f"{done} de {len(feature.stories)} histórias entregues" if done else "planejada"


def _squads(part: Part) -> str:
    return ", ".join(part.squads) or "sem squad dono"


def _attention_table(items: list[model.Attention]) -> list[str]:
    if not items:
        return ["Nada precisa de atenção agora."]
    lines = ["| tipo | o quê | detalhe | com quem |", "|---|---|---|---|"]
    for a in items:
        lines.append(f"| {ATTENTION_LABEL[a.kind]} | {_cell(a.title)} | {_cell(a.detail)} | {_cell(a.owner)} |")
    return lines


def _demote(text: str) -> str:
    """Títulos do documento descem um nível para caber dentro da página da feature."""
    return re.sub(r"^(#{1,5}) ", r"#\1 ", text, flags=re.M)


def _write_feature(docs: Path, product: Product, read: RepoRead, feature: Feature, position: int) -> None:
    part = read.part
    delivered = read.delivered()
    lines = [
        _front(f"{feature.id} {feature.title}", position, f"/features/{_feature_slug(part, feature)}"),
        f"**Planejada em**: {_part_link(product, part.id)} (`{part.repo or part.id}`)  ",
        f"**Estado**: {_feature_state(feature, delivered)}  ",
        f"**Objetivo**: {feature.objective or '—'}",
        "",
    ]
    if feature.mentions:
        lines += ["**Também mexe em**: " + ", ".join(_part_link(product, m) for m in feature.mentions), ""]
    lines += ["## Histórias", "", "| história | título | entregue em | aprovada por |", "|---|---|---|---|"]
    for sid, title in feature.stories:
        d = delivered.get(sid) or {}
        lines.append(
            f"| {sid} | {_cell(title)} | {d.get('delivered_at') or '—'} | {_cell(str(d.get('approved_by') or '—'))} |"
        )
    if not feature.stories:
        lines.append("| — | ainda sem histórias | | |")
    lines += ["", "## Requisito técnico", ""]
    tech = feature.folder / "requisito-tecnico.md"
    if tech.is_file():
        lines.append(_demote(_strip_h1(tech.read_text(encoding="utf-8"))))
    else:
        lines.append("Ainda não escrito (`nexo tech spec` no repositório da feature).")
    if feature.adrs:
        lines += ["", "## Decisões", "", *[f"- {_adr_title(a)} (`{a.name}`)" for a in feature.adrs]]
    _write(docs / "features" / f"{_feature_slug(part, feature)}.md", "\n".join(lines) + "\n")


def _write_part(docs: Path, product: Product, read: RepoRead, all_edges: list[model.Edge], position: int) -> None:
    part = read.part
    users_declared = [p.id for p in product.consumers(part.id)]
    users_code = [e.source for e in all_edges if e.target == part.id and e.kind == "deviation"]
    lines = [
        _front(part.label, position, f"/{KIND_FOLDER[part.kind]}/{part.id}"),
        part.description or "",
        "",
        f"**Squad**: {_squads(part)}  ",
        f"**Repositório**: `{part.repo or part.path or part.id}`"
        + (f" · commit `{read.head}`" if read.head else " · não lido nesta execução")
        + "  ",
    ]
    if part.aliases:
        lines.append(f"**Também chamado de**: {', '.join(part.aliases)}  ")
    lines.append("")
    if part.uses:
        lines += ["## Usa", "", *[f"- {_part_link(product, u)}" for u in part.uses], ""]
    if users_declared or users_code:
        lines += ["## Quem usa", ""]
        lines += [f"- {_part_link(product, u)}" for u in users_declared]
        lines += [f"- {_part_link(product, u)} — **desvio**: visto no código, não declarado" for u in users_code]
        lines.append("")
    if read.endpoints:
        shown = read.endpoints[:40]
        lines += ["## Rotas expostas (do código)", "", *[f"- `{e}`" for e in shown]]
        if len(read.endpoints) > len(shown):
            lines.append(f"- … e mais {len(read.endpoints) - len(shown)}")
        lines.append("")
    if read.features:
        delivered = read.delivered()
        lines += ["## Features", "", "| feature | estado | também mexe em |", "|---|---|---|"]
        for f in read.features:
            others = ", ".join(_part_link(product, m) for m in f.mentions) or "—"
            link = f"[{f.id} {_cell(f.title)}](/features/{_feature_slug(part, f)})"
            lines.append(f"| {link} | {_feature_state(f, delivered)} | {others} |")
        lines.append("")
    if read.deliveries:
        recent = sorted(read.deliveries, key=lambda d: str(d.get("delivered_at") or ""), reverse=True)[:10]
        lines += ["## Últimas entregas", "", "| história | feature | entregue em | o que mudou |", "|---|---|---|---|"]
        for d in recent:
            lines.append(
                f"| {d.get('story')} {_cell(str(d.get('title') or ''))} | {d.get('feature') or '—'} | "
                f"{d.get('delivered_at') or '—'} | {_cell(str(d.get('changed') or '—'))} |"
            )
        lines.append("")
    _write(docs / KIND_FOLDER[part.kind] / f"{part.id}.md", "\n".join(lines) + "\n")


def _completeness(product: Product, reads: list[RepoRead]) -> list[model.Attention]:
    items = []
    for read in reads:
        delivered = read.delivered()
        for f in read.features:
            done = [sid for sid, _ in f.stories if sid in delivered]
            if done and not f.technical:
                items.append(
                    model.Attention(
                        "completude",
                        f"{f.id} entregue em {read.part.label} sem requisito técnico",
                        f"{', '.join(done)} já entregue(s). Escreva com `nexo tech spec {f.id}` no repositório.",
                        _squads(read.part),
                    )
                )
    return items


def _decisions(docs: Path, product: Product, folder: Path | None, reads: list[RepoRead]) -> int:
    lines = [_front("Decisões", 4, "/decisoes")]
    count = 0
    product_adrs = sorted((folder / "decisoes").glob("*.md")) if folder and (folder / "decisoes").is_dir() else []
    lines += ["## Do projeto (valem para todas as frentes)", ""]
    for i, adr in enumerate(product_adrs, start=1):
        _write(
            docs / "decisoes-do-projeto" / f"adr-{adr.name}",
            _front(_adr_title(adr), i) + _strip_h1(adr.read_text(encoding="utf-8")),
        )
        lines.append(f"- [{_adr_title(adr)}](/decisoes-do-projeto/adr-{adr.stem})")
        count += 1
    if product_adrs:
        _write(
            docs / "decisoes-do-projeto" / "_category_.json",
            json.dumps({"label": "Decisões do projeto", "position": 5}),
        )
    else:
        lines.append(f"Nenhuma ainda (ficam em `projetos/{product.id}/decisoes/` no cérebro).")
    lines += ["", "## Dos componentes e visões", "", "| decisão | onde | feature |", "|---|---|---|"]
    found = False
    for read in reads:
        for f in read.features:
            for adr in f.adrs:
                found = True
                count += 1
                link = f"[{f.id}](/features/{_feature_slug(read.part, f)})"
                lines.append(f"| {_cell(_adr_title(adr))} | {_part_link(product, read.part.id)} | {link} |")
    if not found:
        lines.append("| nenhuma ainda | | |")
    _write(docs / "decisoes.md", "\n".join(lines) + "\n")
    return count


def _deliveries(docs: Path, product: Product, reads: list[RepoRead]) -> int:
    rows = [(read.part, d) for read in reads for d in read.deliveries]
    rows.sort(key=lambda r: str(r[1].get("delivered_at") or ""), reverse=True)
    lines = [
        _front("Entregas", 3, "/entregas"),
        "Todas as histórias entregues no projeto, da mais recente para a mais antiga.",
        "",
        "| entregue em | história | onde | feature | aprovada por | o que mudou |",
        "|---|---|---|---|---|---|",
    ]
    for part, d in rows:
        lines.append(
            f"| {d.get('delivered_at') or '—'} | {d.get('story')} {_cell(str(d.get('title') or ''))} | "
            f"{_part_link(product, part.id)} | {d.get('feature') or '—'} | {_cell(str(d.get('approved_by') or '—'))} | "
            f"{_cell(str(d.get('changed') or '—'))} |"
        )
    if not rows:
        lines.append("| — | nenhuma entrega registrada ainda | | | | |")
    _write(docs / "entregas.md", "\n".join(lines) + "\n")
    return len(rows)


def build(ctx: AppContext, pid: str, repos: Path, out: Path) -> dict[str, Any]:
    """Monta o portal do projeto `pid` com os repositórios baixados em `repos`, gerando `out/docs/`."""
    from nexo.kernel import services

    banner = services.load("setup.banner")

    product = pc.load(ctx, pid)
    folder, _ = pc.locate(ctx, pid)
    if not repos.is_dir():
        raise NexoError(f"pasta dos repositórios não existe: {repos}")
    scaffold(ctx, out, product)
    docs = out / "docs"
    if docs.exists():
        shutil.rmtree(docs)
    reads = [read_repo(product, p, locate(repos, p)) for p in product.parts if p.kind != "externo"]
    all_edges = model.edges(product, {r.part.id: r.cites for r in reads})
    attention = model.sort_attention(
        model.structural_attention(product, all_edges, {r.part.id for r in reads if r.root is not None})
        + _completeness(product, reads)
    )
    when = datetime.now().strftime("%d/%m/%Y %H:%M")
    brain = banner.brain_version(ctx.brain_dir) or "—"
    stamp = (
        f"Atualizado em **{when}** · {sum(1 for r in reads if r.root)} de {len(reads)} repositórios lidos · "
        f"cérebro `{brain}`"
    )

    # desenho
    colors = model.squad_colors(product)
    legend = [f"- Squad **{s}**: borda `{c}`" for s, c in colors.items()]
    _write(
        docs / "desenho.md",
        "\n".join(
            [
                _front("Desenho da solução", 2, "/desenho"),
                stamp,
                "",
                "Gerado do `projeto.yaml` (partes, quem usa quem, squads) e do código de cada repositório (outras "
                "partes citadas em código e configuração).",
                "",
                "```mermaid",
                model.mermaid(product, all_edges),
                "```",
                "",
                "**Como ler**",
                "",
                *legend,
                "- Borda tracejada: sem squad dono, ou sistema externo",
                "- Seta cheia: declarada no projeto e vista no código",
                "- Seta tracejada: declarada no projeto, não vista no código (indireta ou repositório não lido)",
                "- Seta vermelha: vista no código, mas o projeto não declara (**desvio**)",
                "",
                "## Ligações",
                "",
                "| de | para | situação | onde aparece no código |",
                "|---|---|---|---|",
                *[
                    f"| {_part_link(product, e.source)} | {_part_link(product, e.target)} | "
                    f"{ {'ok': 'declarada e vista', 'declared': 'só declarada', 'deviation': '**desvio**'}[e.kind] } | "
                    f"{_cell(', '.join(f'`{x}`' for x in e.evidence[:3])) or '—'} |"
                    for e in all_edges
                ],
            ]
        )
        + "\n",
    )
    architecture = folder / "arquitetura.md" if folder else None
    if architecture and architecture.is_file():
        _write(
            docs / "arquitetura.md",
            _front("Arquitetura de referência", 3, "/arquitetura")
            + f":::info Fonte: cérebro da org\nCópia de leitura de `projetos/{product.id}/arquitetura.md`.\n:::\n\n"
            + _strip_h1(architecture.read_text(encoding="utf-8")),
        )

    # visões, componentes, sistemas externos e features
    by_id = {r.part.id: r for r in reads}
    for kind, label, pos in (("visao", "Visões", 6), ("componente", "Componentes", 7)):
        parts = [p for p in product.parts if p.kind == kind]
        if parts:
            _write(docs / KIND_FOLDER[kind] / "_category_.json", json.dumps({"label": label, "position": pos}))
        for i, p in enumerate(parts, start=1):
            _write_part(docs, product, by_id[p.id], all_edges, i)
    external = [p for p in product.parts if p.kind == "externo"]
    if external:
        lines = [_front("Sistemas externos", 8, "/sistemas-externos"), "Fora do projeto, usados por ele.", ""]
        for p in external:
            users = [x.label for x in product.consumers(p.id)]
            lines.append(f"- **{p.label}**: {p.description or '—'} · usado por {', '.join(users) or 'ninguém'}")
        _write(docs / "sistemas-externos.md", "\n".join(lines) + "\n")
    features = [(r, f) for r in reads for f in r.features]
    if features:
        _write(docs / "features" / "_category_.json", json.dumps({"label": "Features", "position": 9}))
    for i, (read, f) in enumerate(features, start=1):
        _write_feature(docs, product, read, f, i)
    decisions = _decisions(docs, product, folder, reads)
    delivered = _deliveries(docs, product, reads)

    # início
    done = sum(1 for r, f in features if f.stories and _feature_state(f, r.delivered()) == "entregue")
    with_deliveries = [(r, f) for r, f in features if any(sid in r.delivered() for sid, _ in f.stories)]
    tech_pct = (
        round(100 * sum(1 for _, f in with_deliveries if f.technical) / len(with_deliveries))
        if with_deliveries
        else 100
    )
    home = [
        _front(product.titled, 1, "/"),
        product.description or "",
        "",
        stamp,
        "",
        f"**{len([p for p in product.parts if p.kind == 'visao'])}** visões · "
        f"**{len([p for p in product.parts if p.kind == 'componente'])}** componentes · "
        f"**{len(colors)}** squads · **{done}** features entregues · **{len(features) - done}** em andamento · "
        f"**{decisions}** decisões · **{tech_pct}%** das features com entrega têm requisito técnico",
        "",
        "## Precisa de atenção",
        "",
        *_attention_table(attention),
        "",
        "## Por onde começar",
        "",
        "- [Desenho da solução](/desenho): visões, componentes, squads e ligações",
        "- [Entregas](/entregas): o que foi entregue em todas as frentes",
        "- [Decisões](/decisoes): do projeto e de cada componente",
    ]
    if architecture and architecture.is_file():
        home.append("- [Arquitetura de referência](/arquitetura)")
    _write(docs / "index.md", "\n".join(home) + "\n")
    (out / "atencao.json").write_text(
        json.dumps([a.as_dict() for a in attention], ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    result = {
        "product": product.id,
        "out": str(out),
        "repos_read": sum(1 for r in reads if r.root),
        "repos_total": len(reads),
        "features": len(features),
        "deliveries": delivered,
        "decisions": decisions,
        "deviations": sum(1 for e in all_edges if e.kind == "deviation"),
        "attention": [a.as_dict() for a in attention],
    }
    ctx.audit("docs.portal", {k: v for k, v in result.items() if k != "attention"} | {"attention": len(attention)})
    return result
