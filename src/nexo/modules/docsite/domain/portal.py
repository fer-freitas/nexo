"""Portal do projeto: o desenho da solução e o que precisa de atenção, a partir do produto e do código.

O `produto.yaml` diz o que **deveria** existir (partes, quem usa quem, squads). O código de cada repositório diz o
que **existe**: as rotas que ele expõe e as outras partes que ele cita em código e configuração (URLs, nomes de
serviço, variáveis). Cruzar os dois dá três tipos de ligação:

- `ok`: declarada no produto e vista no código;
- `declared`: declarada, mas não vista no código (chamada indireta ou repositório não lido);
- `deviation`: vista no código, mas o produto não declara: desvio do desenho, vira alerta para o arquiteto.

Tudo determinístico, sem IA. O nome técnico de uma parte é o `id` (é o que aparece em URLs e configurações), com
`-` ou `_`; os apelidos servem ao texto das features, não ao código.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from nexo.kernel.product import Part, Product

# Rotas expostas, por framework. Prefixo de classe (Spring `@RequestMapping`, ASP.NET `[Route]`) fica de fora.
_METHODS = "get|post|put|patch|delete"
ENDPOINTS: tuple[re.Pattern[str], ...] = (
    # FastAPI, Express (o cliente HTTP do front, como `api.get`, fica de fora: é chamada, não rota)
    re.compile(rf"""(?:app|router|bp|blueprint)\s*\.\s*({_METHODS})\s*\(\s*['"`](/[^'"`]*)['"`]""", re.I),
    re.compile(rf"""@({_METHODS})Mapping\s*\(\s*(?:(?:value|path)\s*=\s*)?['"]([^'"]*)['"]""", re.I),
    re.compile(rf"""\[\s*Http({_METHODS})\s*\(\s*"([^"]*)"\s*\)\s*\]""", re.I),
    re.compile(rf"""\.\s*Map({_METHODS})\s*\(\s*"([^"]+)\"""", re.I),
)
FLASK_ROUTE = re.compile(r"""@\w+\.route\(\s*['"]([^'"]+)['"](?:[^)]*methods\s*=\s*[\[(]([^\])]*)[\])])?""")

ATTENTION_ORDER = ("desvio", "completude", "dono", "leitura")


def endpoints(text: str) -> list[str]:
    """Rotas que o arquivo expõe, como `GET /posicoes/{conta}`."""
    found: set[str] = set()
    for pattern in ENDPOINTS:
        for m in pattern.finditer(text):
            path = m.group(2) or "/"
            found.add(f"{m.group(1).upper()} {path if path.startswith('/') else '/' + path}")
    for m in FLASK_ROUTE.finditer(text):
        methods = re.findall(r"[A-Za-z]+", m.group(2) or "") or ["GET"]
        found.update(f"{method.upper()} {m.group(1)}" for method in methods)
    return sorted(found)


def code_names(part: Part) -> list[str]:
    """Como a parte aparece no código: o id com hífen ou sublinhado (ex.: posicao-api, posicao_api)."""
    return sorted({part.id, part.id.replace("-", "_")})


def cites(text: str, names: list[str]) -> bool:
    """O texto cita algum dos nomes técnicos (sem diferenciar maiúsculas; letra ou número não podem encostar)."""
    lowered = text.lower()
    return any(re.search(rf"(?<![a-z0-9]){re.escape(n.lower())}(?![a-z0-9])", lowered) for n in names)


@dataclass
class Edge:
    source: str
    target: str
    declared: bool
    evidence: list[str] = field(default_factory=list)  # arquivos onde a ligação aparece no código

    @property
    def kind(self) -> str:
        if self.declared:
            return "ok" if self.evidence else "declared"
        return "deviation"


def edges(product: Product, found: dict[str, dict[str, list[str]]]) -> list[Edge]:
    """Ligações do desenho: as declaradas no produto mais as achadas no código (`found[origem][destino] = arquivos`)."""
    out: dict[tuple[str, str], Edge] = {}
    for part in product.parts:
        for used in part.uses:
            out[(part.id, used)] = Edge(part.id, used, True)
    for source, targets in found.items():
        for target, files in targets.items():
            if source == target or product.part(target) is None or product.part(source) is None:
                continue
            edge = out.setdefault((source, target), Edge(source, target, False))
            edge.evidence = sorted(set(edge.evidence) | set(files))
    return sorted(out.values(), key=lambda e: (e.source, e.target))


@dataclass
class Attention:
    kind: str  # desvio | completude | dono | leitura
    title: str
    detail: str = ""
    owner: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "title": self.title, "detail": self.detail, "owner": self.owner}


def structural_attention(product: Product, all_edges: list[Edge], read: set[str]) -> list[Attention]:
    """O que o produto e o código mostram sem olhar as features: desvios, partes sem dono e repositórios não lidos."""
    items: list[Attention] = []
    for e in all_edges:
        if e.kind != "deviation":
            continue
        source, target = product.part(e.source), product.part(e.target)
        if source is None or target is None:  # pragma: no cover - edges() só gera ligações entre partes conhecidas
            continue
        files = ", ".join(e.evidence[:3]) + ("…" if len(e.evidence) > 3 else "")
        items.append(
            Attention(
                "desvio",
                f"{source.label} usa {target.label} no código, mas o produto não declara",
                f"Visto em {files}. Declare em `uses` no produto.yaml ou corrija o código.",
                "arquiteto",
            )
        )
    for part in product.parts:
        if part.kind == "externo":
            continue
        if not part.squads:
            users = ", ".join(p.label for p in product.consumers(part.id))
            items.append(
                Attention(
                    "dono",
                    f"{part.label} sem squad dono",
                    f"Usado por {users}." if users else "Defina `squads` no produto.yaml.",
                    "produto.yaml",
                )
            )
        if part.id not in read:
            items.append(
                Attention(
                    "leitura",
                    f"Repositório de {part.label} não foi lido",
                    f"Esperado: {part.repo or part.path or part.id}. O CI do portal precisa baixar esse repositório.",
                    "CI do portal",
                )
            )
    return items


def sort_attention(items: list[Attention]) -> list[Attention]:
    return sorted(items, key=lambda a: (ATTENTION_ORDER.index(a.kind) if a.kind in ATTENTION_ORDER else 99, a.title))


# ---------------------------------------------------------------------- diagrama (Mermaid)

SQUAD_COLORS = ("#2B7A9E", "#7B57B5", "#B4507A", "#1F8F62", "#A86A00", "#2F5FC4")
DEVIATION = "#C2372F"
GROUPS = (("visao", "Visões"), ("componente", "Componentes"), ("externo", "Sistemas externos"))


def _node(part_id: str) -> str:
    return "p_" + re.sub(r"[^A-Za-z0-9_]", "_", part_id)


def _label(text: str) -> str:
    return text.replace('"', "#quot;").replace("<", "#lt;").replace(">", "#gt;")


def mermaid(product: Product, all_edges: list[Edge]) -> str:
    """Desenho da solução: visões, componentes e sistemas externos; cor da borda = squad dono; ligação vermelha
    tracejada = desvio; tracejada = declarada e não vista no código."""
    colors = squad_colors(product)
    squads = list(colors)
    lines = ["flowchart TB"]
    for kind, title in GROUPS:
        parts = [p for p in product.parts if p.kind == kind]
        if not parts:
            continue
        lines.append(f'  subgraph g_{kind}["{title}"]')
        for p in parts:
            sub = p.repo or p.path or ("fora do produto" if kind == "externo" else "")
            text = _label(p.label) + (f"<br/><small>{_label(sub)}</small>" if sub else "")
            lines.append(f'    {_node(p.id)}["{text}"]')
        lines.append("  end")
    styles: list[str] = []
    for i, e in enumerate(all_edges):
        arrow = "-.->" if e.kind != "ok" else "-->"
        lines.append(f"  {_node(e.source)} {arrow} {_node(e.target)}")
        if e.kind == "deviation":
            styles.append(f"  linkStyle {i} stroke:{DEVIATION},stroke-width:2px,color:{DEVIATION}")
    lines += styles
    for i, squad in enumerate(squads):
        lines.append(f"  classDef sq{i} stroke:{colors[squad]},stroke-width:2px")
    lines.append("  classDef semdono stroke-dasharray:5 4")
    for p in product.parts:
        if p.kind == "externo":
            lines.append(f"  class {_node(p.id)} semdono")
        elif p.squads:
            lines.append(f"  class {_node(p.id)} sq{squads.index(p.squads[0])}")
        else:
            lines.append(f"  class {_node(p.id)} semdono")
    return "\n".join(lines)


def squad_colors(product: Product) -> dict[str, str]:
    squads = list(product.squads) + sorted({s for p in product.parts for s in p.squads} - set(product.squads))
    return {s: SQUAD_COLORS[i % len(SQUAD_COLORS)] for i, s in enumerate(squads)}
