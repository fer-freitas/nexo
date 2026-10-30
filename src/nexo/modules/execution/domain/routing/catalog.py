"""Catálogo de rotas (ferramenta + modelo) e regras de roteamento."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

TIERS = ("cheap", "mid", "top")


@dataclass(frozen=True)
class Route:
    id: str
    tool: str
    model: str
    tool_vendor: str
    model_vendor: str
    tier: str
    cost: float
    model_fixed: bool = (
        True  # False: a ferramenta escolhe o modelo (ex.: copilot/auto); não serve à revisão independente
    )

    @property
    def vendors(self) -> frozenset[str]:
        return frozenset({self.tool_vendor, self.model_vendor})


@dataclass(frozen=True)
class Rule:
    when: dict[str, Any]
    prefer: tuple[str, ...] = ()
    allow_vendors: tuple[str, ...] | None = None
    human_gate: bool = False

    def matches(self, facts: dict[str, Any]) -> bool:
        for key, expected in self.when.items():
            value = facts.get(key)
            if isinstance(expected, list):
                if value not in expected:
                    return False
            elif value != expected:
                return False
        return True


@dataclass(frozen=True)
class Catalog:
    routes: dict[str, Route]
    priors: dict[str, dict[str, float]]
    profiles: dict[str, dict[str, float]]
    defaults: dict[str, tuple[str, ...]]
    rules: tuple[Rule, ...] = field(default_factory=tuple)
    failure_penalty: dict[str, float] = field(default_factory=dict)

    @staticmethod
    def from_mapping(data: dict[str, Any]) -> Catalog:
        routes: dict[str, Route] = {}
        for raw in data.get("routes", []):
            route = Route(
                id=str(raw["id"]),
                tool=str(raw["tool"]),
                model=str(raw["model"]),
                tool_vendor=str(raw["tool_vendor"]),
                model_vendor=str(raw["model_vendor"]),
                tier=str(raw["tier"]),
                cost=float(raw["cost"]),
                model_fixed=bool(raw.get("model_fixed", True)),
            )
            if route.tier not in TIERS:
                raise ValueError(f"rota {route.id}: tier inválido {route.tier}")
            routes[route.id] = route
        defaults = {role: tuple(ids) for role, ids in (data.get("defaults") or {}).items()}
        rules = tuple(
            Rule(
                when=dict(r.get("when") or {}),
                prefer=tuple(r.get("prefer") or ()),
                allow_vendors=tuple(r["allow_vendors"]) if "allow_vendors" in r else None,
                human_gate=bool(r.get("human_gate", False)),
            )
            for r in data.get("rules", [])
        )
        for role, ids in defaults.items():
            missing = [i for i in ids if i not in routes]
            if missing:
                raise ValueError(f"defaults.{role} referencia rotas inexistentes: {missing}")
        for rule in rules:
            missing = [i for i in rule.prefer if i not in routes]
            if missing:
                raise ValueError(f"regra {rule.when} referencia rotas inexistentes: {missing}")
        return Catalog(
            routes=routes,
            priors={k: {t: float(p) for t, p in v.items()} for k, v in (data.get("priors") or {}).items()},
            profiles={k: {m: float(x) for m, x in (v or {}).items()} for k, v in (data.get("profiles") or {}).items()},
            defaults=defaults,
            rules=rules,
            failure_penalty={k: float(v) for k, v in (data.get("failure_penalty") or {}).items()},
        )

    def penalty(self, role: str) -> float:
        return float(self.failure_penalty.get(role, self.failure_penalty.get("default", 0.0)))

    def prior(self, role: str, tier: str) -> float:
        table = self.priors.get(role) or self.priors.get("default") or {}
        return float(table.get(tier, 0.5))
