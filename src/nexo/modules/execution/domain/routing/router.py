"""Escolha de rota: filtros determinísticos + pontuação por perfil."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nexo.modules.execution.domain.routing.catalog import Catalog, Route
from nexo.modules.knowledge.domain.learning.model import RouteStats, posterior

NEEDS_GUARD = frozenset({"confidential", "restricted"})


@dataclass(frozen=True)
class RouteAvailability:
    installed: bool = True
    authenticated: bool | None = True
    limited_until: float | None = None  # epoch; cota esgotada até
    budget_ok: bool = True


@dataclass(frozen=True)
class RoutingRequest:
    role: str
    facts: dict[str, Any]  # kind, size, stack, risk_tags...
    profile: str = "balanceado"
    data_class: str = "confidential"
    allowed_vendors: tuple[str, ...] | None = None  # None = sem restrição
    unguarded: frozenset[str] = frozenset()  # ferramentas sem prova de que a guarda bloqueia
    author_vendor: str | None = None  # para revisores: fornecedor do modelo ≠ autor
    author_vendors: tuple[str, ...] = ()
    exclude: frozenset[str] = frozenset()
    observed: dict[str, tuple[int, int]] = field(default_factory=dict)  # Rota → (sucessos, falhas)
    forced_tool: str | None = None
    forced_model: str | None = None
    now: float = 0.0
    blocked: dict[str, str] = field(default_factory=dict)  # rota → motivo do kill switch


@dataclass(frozen=True)
class Candidate:
    route: Route
    p_success: float
    expected_cost: float
    score: float
    rank: int


@dataclass
class RouteDecision:
    route: Route | None
    reason: str
    candidates: list[Candidate] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)
    human_gate: bool = False
    earliest_reset: float | None = None


def _preference(
    catalog: Catalog, req: RoutingRequest
) -> tuple[list[str], list[str], bool, tuple[str, ...] | None, set[str]]:
    """Ordem de preferência, regras aplicadas, portão humano, restrição de fornecedores e rotas preferidas."""
    facts = {"role": req.role, **req.facts}
    order: list[str] = []
    applied: list[str] = []
    human_gate = False
    vendors: tuple[str, ...] | None = req.allowed_vendors
    for rule in catalog.rules:
        if rule.matches(facts):
            applied.append(" ".join(f"{k}={v}" for k, v in rule.when.items()))
            order.extend(r for r in rule.prefer if r not in order)
            human_gate = human_gate or rule.human_gate
            if rule.allow_vendors is not None:
                vendors = (
                    rule.allow_vendors if vendors is None else tuple(v for v in vendors if v in rule.allow_vendors)
                )
    preferred = set(order)
    order.extend(r for r in catalog.defaults.get(req.role, ()) if r not in order)
    order.extend(r for r in catalog.routes if r not in order)
    return order, applied, human_gate, vendors, preferred


def choose(catalog: Catalog, req: RoutingRequest, availability: dict[str, RouteAvailability]) -> RouteDecision:
    order, applied, human_gate, vendors, preferred = _preference(catalog, req)
    rejected: dict[str, str] = {}
    survivors: list[tuple[int, Route]] = []
    resets: list[float] = []
    for rank, route_id in enumerate(order):
        route = catalog.routes[route_id]
        avail = availability.get(route.tool, RouteAvailability(installed=False))
        reason = None
        if route_id in req.blocked:
            reason = f"kill switch: {req.blocked[route_id]}"
        elif req.forced_tool and route.tool != req.forced_tool:
            reason = "rota forçada para outra ferramenta"
        elif req.forced_model and route.model != req.forced_model:
            reason = "modelo forçado diferente"
        elif route_id in req.exclude:
            reason = "excluída nesta execução (falhas anteriores)"
        elif not avail.installed:
            reason = "ferramenta não instalada"
        elif avail.authenticated is False:
            reason = "ferramenta não autenticada"
        elif avail.limited_until is not None and avail.limited_until > req.now:
            reason = "cota esgotada"
            resets.append(avail.limited_until)
        elif not avail.budget_ok:
            reason = "orçamento esgotado"
        elif route.tool in req.unguarded and req.data_class in NEEDS_GUARD:
            reason = f"guarda não comprovada: {route.tool} não recebe dados '{req.data_class}'"
        elif vendors is not None and not route.vendors <= set(vendors):
            reason = f"classe de dados '{req.data_class}' não permite {sorted(route.vendors - set(vendors))}"
        elif (req.author_vendor or req.author_vendors) and not route.model_fixed:
            reason = "modelo escolhido pela ferramenta: revisão independente não garantida"
        elif route.model_vendor in {*req.author_vendors, *([req.author_vendor] if req.author_vendor else [])}:
            reason = "mesmo fornecedor do autor (revisão independente)"
        if reason:
            rejected[route_id] = reason
        else:
            survivors.append((rank, route))

    if not survivors:
        earliest = min(resets) if resets else None
        return RouteDecision(None, "nenhuma rota disponível", [], rejected, human_gate, earliest)

    # Regras com `prefer` restringem às rotas preferidas quando alguma está disponível.
    by_rule = bool(preferred) and any(r.id in preferred for _, r in survivors)
    if by_rule:
        survivors = [(rank, r) for rank, r in survivors if r.id in preferred]
    limits = catalog.profiles.get(req.profile, {})
    candidates: list[Candidate] = []
    for rank, route in survivors:
        p = catalog.prior(req.role, route.tier)
        if route.id in req.observed:
            p = posterior(p, RouteStats(*req.observed[route.id]))
        # custo esperado = tentativas esperadas × custo + penalidade de falha (retrabalho)
        expected = route.cost / max(p, 0.01) + catalog.penalty(req.role) * (1 - p)
        if req.profile == "economico":
            score = -expected if p >= limits.get("min_success", 0.0) else -1e9 - expected
        elif req.profile == "maxima-qualidade":
            within = route.cost <= limits.get("max_cost", float("inf"))
            score = (p if within else p - 1.0) - route.cost * 1e-4  # empate → mais barato
        else:
            score = p / expected
        candidates.append(Candidate(route, p, expected, score, rank))
    # Com regra aplicável, vale a ordem declarada na regra (decisão explícita do time); senão, o score.
    # Regra aplicável define o conjunto e a ordem; com histórico observado, o histórico ordena dentro dele.
    learned = any(c.route.id in req.observed for c in candidates)
    candidates.sort(key=lambda c: (c.rank,) if by_rule and not learned else (-c.score, c.rank))
    best = candidates[0]
    parts = [f"perfil {req.profile}", f"p≈{best.p_success:.2f}", f"custo esperado {best.expected_cost:.1f}"]
    if applied:
        parts.append(f"regras {', '.join(applied)}")
    if req.forced_tool or req.forced_model:
        parts.append("rota forçada pelo usuário")
    return RouteDecision(best.route, "; ".join(parts), candidates, rejected, human_gate)
