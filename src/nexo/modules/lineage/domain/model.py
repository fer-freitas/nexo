"""Linhagem em nível de componente: o elo entre repositórios é o canal, não o nome do outro serviço.

Tudo determinístico. Nunca afirma que algo não existe: a ausência de registro não prova ausência de uso, então o
vocabulário é "consumidores conhecidos" e cada ligação tem um estado de completude.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

SCHEMA = "nexo.lineage/v1"
CONFIRMED, PARTIAL, UNCONFIRMED, UNIDENTIFIED, STALE, DEVIATION = (
    "confirmado",
    "parcial",
    "não confirmado",
    "não identificado",
    "obsoleto",
    "desvio",
)
ALLOWED_TOP = {"schema", "team", "repo", "commit", "generated_at", "scanner", "components", "unresolved"}
ALLOWED_COMPONENT = {"id", "runtime", "publishes", "consumes", "calls", "exposes"}
ALLOWED_LINK = {"channel", "message", "evidence", "status"}
ALLOWED_EVIDENCE = {"source", "file", "line", "confidence"}
ACCOUNT_ID = re.compile(r"(?<!\d)\d{12}(?!\d)")
DEFAULT_ENV = ("dev", "hom", "homolog", "qa", "uat", "stg", "stage", "staging", "prd", "prod", "production")
RETRY = re.compile(r"[.\-_](retry)([.\-_]\d+)?$")
DLT = re.compile(r"[.\-_](dlt|dlq|dead[.\-_]?letter)$")
ARN = re.compile(r"^arn:aws:(sns|sqs):[^:]*:[^:]*:(.+)$")


def normalize(kind: str, name: str, environments: tuple[str, ...] = DEFAULT_ENV) -> tuple[str, str]:
    """(ID do canal, relação). Tira ambiente e conta, e liga retentativa e erro ao tópico principal."""
    value = name.strip()
    match = ARN.match(value)
    if match:
        kind, value = match.group(1), match.group(2)
    value = re.sub(rf"^(?:{'|'.join(environments)})[.\-_]", "", value, flags=re.I)
    value = re.sub(rf"[.\-_](?:{'|'.join(environments)})$", "", value, flags=re.I)
    relation = ""
    if RETRY.search(value):
        value, relation = RETRY.sub("", value), "retry"
    elif DLT.search(value):
        value, relation = DLT.sub("", value), "dlt"
    return f"{kind}:{value}", relation


@dataclass
class Link:
    component: str
    team: str
    direction: str  # publishes | consumes
    channel: str
    message: str = ""
    file: str = ""
    line: int = 0
    source: str = ""
    confidence: str = "medium"
    status: str = ""  # claimed: dito por outro time

    @property
    def evidence(self) -> str:
        return f"{self.source} {self.file}:{self.line}".strip()


@dataclass
class Graph:
    links: list[Link] = field(default_factory=list)
    components: dict[str, dict[str, Any]] = field(default_factory=dict)
    stale_teams: set[str] = field(default_factory=set)
    invalid: list[dict[str, str]] = field(default_factory=list)
    unresolved: list[dict[str, Any]] = field(default_factory=list)


def validate_manifest(data: dict[str, Any], expected_team: str = "") -> list[str]:
    """Roda antes de publicar e de novo ao ler: quem lê não confia no que encontra."""
    problems = []
    if data.get("schema") != SCHEMA:
        problems.append(f"schema inválido: {data.get('schema')}")
    if expected_team and data.get("team") != expected_team:
        problems.append(f"team {data.get('team')} difere do repositório do time {expected_team}")
    extra = set(data) - ALLOWED_TOP
    if extra:
        problems.append(f"campos proibidos: {', '.join(sorted(extra))}")
    for comp in data.get("components") or []:
        wrong = set(comp) - ALLOWED_COMPONENT
        if wrong:
            problems.append(f"{comp.get('id')}: campos proibidos: {', '.join(sorted(wrong))}")
        for direction in ("publishes", "consumes", "calls", "exposes"):
            for link in comp.get(direction) or []:
                bad = set(link) - ALLOWED_LINK
                if bad:
                    problems.append(f"{comp.get('id')}: campos proibidos: {', '.join(sorted(bad))}")
                bad_ev = set(link.get("evidence") or {}) - ALLOWED_EVIDENCE
                if bad_ev:
                    problems.append(f"{comp.get('id')}: evidência com campos proibidos: {', '.join(sorted(bad_ev))}")
                if "/" in str(link.get("channel", "")) and not str(link.get("channel", "")).startswith("http:"):
                    problems.append(f"canal com caminho: {link.get('channel')}")
    # o hash do commit pode ter 12 dígitos seguidos por acaso; o que importa é o resto
    if ACCOUNT_ID.search(str({k: v for k, v in data.items() if k != "commit"})):
        problems.append("contém ID de conta da AWS")
    return problems


def add_manifest(graph: Graph, data: dict[str, Any], stale: bool = False) -> None:
    team = str(data.get("team", ""))
    if stale:
        graph.stale_teams.add(team)
    for comp in data.get("components") or []:
        cid = str(comp.get("id", ""))
        graph.components[cid] = {
            "team": team,
            "runtime": comp.get("runtime", ""),
            "repo": data.get("repo", ""),
            "commit": data.get("commit", ""),
            "generated_at": data.get("generated_at", ""),
        }
        for direction in ("publishes", "consumes", "calls", "exposes"):
            for link in comp.get(direction) or []:
                ev = dict(link.get("evidence") or {})
                graph.links.append(
                    Link(
                        cid,
                        team,
                        direction,
                        str(link.get("channel", "")),
                        str(link.get("message", "")),
                        str(ev.get("file", "")),
                        int(ev.get("line", 0) or 0),
                        str(ev.get("source", "")),
                        str(ev.get("confidence", "medium")),
                        str(link.get("status", "")),
                    )
                )
    graph.unresolved += [{"repo": data.get("repo", ""), **dict(u)} for u in data.get("unresolved") or []]


PUBLISH = ("publishes", "exposes")
CONSUME = ("consumes", "calls")


def by_channel(graph: Graph) -> dict[str, dict[str, list[Link]]]:
    out: dict[str, dict[str, list[Link]]] = {}
    for link in graph.links:
        side = "pub" if link.direction in PUBLISH else "sub"
        out.setdefault(link.channel, {"pub": [], "sub": []})[side].append(link)
    return out


def _unknown(link: Link) -> bool:
    return link.status == "discovered" or link.component.startswith("?")


def state_of(channel: dict[str, list[Link]], stale_teams: set[str]) -> str:
    links = channel["pub"] + channel["sub"]
    if any(link.team in stale_teams for link in links):
        return STALE
    if any(link.component.startswith("?") for link in links):
        return UNIDENTIFIED
    declared = [link for link in links if link.status not in ("claimed", "discovered")]
    if not declared:
        return UNCONFIRMED
    pubs = [link for link in channel["pub"] if link.status not in ("claimed", "discovered")]
    subs = [link for link in channel["sub"] if link.status not in ("claimed", "discovered")]
    if pubs and subs:
        return CONFIRMED
    return PARTIAL


def neighbors(graph: Graph, component: str) -> dict[str, list[dict[str, str]]]:
    """Entradas e saídas do componente, com o estado de cada ligação."""
    channels = by_channel(graph)
    entries, outputs = [], []
    for link in graph.links:
        if link.component != component:
            continue
        data = channels[link.channel]
        state = state_of(data, graph.stale_teams)
        if link.direction in CONSUME:
            others = data["pub"] or [Link("?", "", "publishes", link.channel)]
            target = entries
        else:
            others = data["sub"] or [Link("?", "", "consumes", link.channel)]
            target = outputs
        for other in others:
            if other.component == component:
                continue
            target.append(
                {"channel": link.channel, "component": other.component, "state": state, "message": link.message or other.message}
            )
    key = lambda r: (r["channel"], r["component"])  # noqa: E731
    return {"entries": sorted(entries, key=key), "outputs": sorted(outputs, key=key)}


def walk(graph: Graph, component: str, direction: str, depth: int = 5) -> list[str]:
    """Quem vem antes (upstream) ou depois (downstream), até a profundidade pedida."""
    seen: list[str] = []
    frontier = [component]
    key = "entries" if direction == "upstream" else "outputs"
    for _ in range(depth):
        nexts = []
        for node in frontier:
            for row in neighbors(graph, node)[key]:
                target = row["component"]
                if target not in seen and target != component and target != "?":
                    seen.append(target)
                    nexts.append(target)
        frontier = nexts
        if not frontier:
            break
    return seen


def impact(graph: Graph, target: str) -> dict[str, Any]:
    """Quem pode ser afetado. "Consumidores conhecidos": a cobertura pode estar incompleta."""
    channels = by_channel(graph)
    hits = [c for c, d in channels.items() if c == target or any(target and target in (x.message or "") for x in d["pub"] + d["sub"])]
    consumers: list[dict[str, str]] = []
    for channel in hits:
        data = channels[channel]
        state = state_of(data, graph.stale_teams)
        for link in data["sub"]:
            consumers.append({"component": link.component, "team": link.team, "channel": channel, "state": state})
    return {
        "target": target,
        "channels": sorted(hits),
        "known_consumers": sorted(consumers, key=lambda r: (r["component"], r["channel"])),
        "complete": bool(hits) and all(state_of(channels[c], graph.stale_teams) == CONFIRMED for c in hits),
        "note": "consumidores conhecidos: ausência de registro não prova ausência de uso",
    }


def coverage(graph: Graph) -> dict[str, Any]:
    channels = by_channel(graph)
    counts: dict[str, int] = {}
    pending = []
    for name, data in sorted(channels.items()):
        state = state_of(data, graph.stale_teams)
        counts[state] = counts.get(state, 0) + 1
        if state != CONFIRMED:
            pending.append({"channel": name, "state": state})
    total = len(channels)
    return {
        "channels": total,
        "confirmed": counts.get(CONFIRMED, 0),
        "percent": round(100 * counts.get(CONFIRMED, 0) / total) if total else 0,
        "by_state": counts,
        "pending": pending,
        "unresolved": len(graph.unresolved),
        "invalid": graph.invalid,
        "stale_teams": sorted(graph.stale_teams),
    }


def nodes_and_edges(graph: Graph) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Mesmo formato do índice do cérebro, para o grafo futuro unir código, cérebro e linhagem."""
    nodes = [
        {"id": f"component:{c}", "type": "component", "title": c, "team": v["team"]} for c, v in sorted(graph.components.items())
    ]
    nodes += [{"id": f"channel:{c}", "type": "channel", "title": c} for c in sorted({link.channel for link in graph.links})]
    edges = sorted(
        (
            {
                "from": f"component:{link.component}",
                "rel": link.direction,
                "to": f"channel:{link.channel}",
            }
            for link in graph.links
        ),
        key=lambda e: (e["from"], e["rel"], e["to"]),
    )
    return nodes, edges
