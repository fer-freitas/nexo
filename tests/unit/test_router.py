from __future__ import annotations

import time

import pytest

from nexo.kernel.runtime.resources import load_yaml
from nexo.modules.execution.domain.routing.catalog import Catalog, Rule
from nexo.modules.execution.domain.routing.router import RouteAvailability, RoutingRequest, choose

CATALOG = Catalog.from_mapping(load_yaml("routing.yaml"))
ALL = {t: RouteAvailability() for t in ("claude", "copilot", "devin")}


def pick(role: str = "developer", profile: str = "balanceado", avail=ALL, **kw) -> str | None:  # type: ignore[no-untyped-def]
    facts = kw.pop("facts", {"kind": "backend", "size": "M", "stack": "python"})
    d = choose(CATALOG, RoutingRequest(role, facts, profile, now=time.time(), **kw), avail)
    return d.route.id if d.route else None


# Casos golden: (descrição, parâmetros, rota esperada)
GOLDEN = [
    ("dev balanceado", {}, "claude/sonnet"),
    ("dev economico evita modelo fraco", {"profile": "economico"}, "claude/sonnet"),
    ("dev máxima qualidade", {"profile": "maxima-qualidade"}, "copilot/gpt-5"),
    ("classificador usa o mais barato", {"role": "classifier", "facts": {"size": "S"}}, "claude/haiku"),
    ("migração prefere opus", {"facts": {"kind": "migration", "size": "M"}}, "claude/opus"),
    ("dotnet grande prefere devin", {"facts": {"kind": "backend", "size": "L", "stack": "dotnet"}}, "devin/default"),
    ("forçar ferramenta", {"forced_tool": "copilot"}, "copilot/auto"),
    ("forçar modelo", {"forced_tool": "claude", "forced_model": "haiku"}, "claude/haiku"),
    ("exclui rota que falhou", {"exclude": frozenset({"claude/sonnet"})}, "copilot/auto"),
    ("vendors restritos a anthropic", {"allowed_vendors": ("anthropic",)}, "claude/sonnet"),
    (
        "revisor de outro fornecedor",
        {"role": "reviewer", "author_vendor": "anthropic"},
        "copilot/gpt-5",
    ),  # auto fica de fora
    (
        "forçar ferramenta na revisão",
        {"role": "reviewer", "author_vendor": "anthropic", "forced_tool": "copilot"},
        "copilot/gpt-5",
    ),
]


@pytest.mark.parametrize(("name", "params", "expected"), GOLDEN, ids=[g[0] for g in GOLDEN])
def test_golden_routing(name: str, params: dict, expected: str) -> None:  # type: ignore[type-arg]
    assert pick(**params) == expected


def test_quota_and_auth_filters() -> None:
    avail = {
        "claude": RouteAvailability(limited_until=time.time() + 600),
        "copilot": RouteAvailability(authenticated=False),
        "devin": RouteAvailability(installed=False),
    }
    d = choose(CATALOG, RoutingRequest("developer", {"size": "M"}, now=time.time()), avail)
    assert d.route is None
    assert d.earliest_reset is not None
    assert set(d.rejected.values()) >= {"cota esgotada", "ferramenta não autenticada", "ferramenta não instalada"}


def test_quota_expired_is_available() -> None:
    avail = {**ALL, "claude": RouteAvailability(limited_until=time.time() - 1)}
    assert pick(avail=avail) == "claude/sonnet"


def test_restricted_data_class_rejects_everything() -> None:
    every_tool = {r.tool: RouteAvailability() for r in CATALOG.routes.values()}  # inclui as rotas de API
    d = choose(CATALOG, RoutingRequest("developer", {}, data_class="restricted", allowed_vendors=(), now=0), every_tool)
    assert d.route is None and all(r.startswith("classe de dados") for r in d.rejected.values())


def test_budget_filter() -> None:
    avail = {"claude": RouteAvailability(budget_ok=False)}
    d = choose(CATALOG, RoutingRequest("developer", {}, now=0), avail)
    assert d.route is None and "orçamento esgotado" in d.rejected.values()


def test_rule_vendor_restriction_and_human_gate() -> None:
    catalog = Catalog(
        routes=CATALOG.routes,
        priors=CATALOG.priors,
        profiles=CATALOG.profiles,
        defaults=CATALOG.defaults,
        rules=(Rule({"kind": "payments"}, ("claude/opus",), ("anthropic",), True),),
        failure_penalty=CATALOG.failure_penalty,
    )
    d = choose(catalog, RoutingRequest("developer", {"kind": "payments"}, now=0), ALL)
    assert d.route is not None and d.route.model_vendor == "anthropic" and d.human_gate


def test_catalog_validation() -> None:
    with pytest.raises(ValueError, match="inexistentes"):
        Catalog.from_mapping({"routes": [], "defaults": {"developer": ["x/y"]}})
    with pytest.raises(ValueError, match="tier"):
        Catalog.from_mapping(
            {
                "routes": [
                    {
                        "id": "a/b",
                        "tool": "a",
                        "model": "b",
                        "tool_vendor": "v",
                        "model_vendor": "v",
                        "tier": "gold",
                        "cost": 1,
                    }
                ]
            }
        )
    with pytest.raises(ValueError, match="inexistentes"):
        Catalog.from_mapping({"routes": [], "rules": [{"when": {}, "prefer": ["x"]}]})


def test_rule_list_match() -> None:
    assert Rule({"kind": ["a", "b"]}).matches({"kind": "b"})
    assert not Rule({"kind": ["a"]}).matches({"kind": "c"})
