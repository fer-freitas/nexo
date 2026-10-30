from __future__ import annotations

import random

from nexo.kernel.learning.model import (
    Lesson,
    RouteStats,
    VariantResult,
    added_dependencies,
    adoptions,
    compare,
    dedup_lessons,
    posterior,
    thompson,
    wilson,
)
from nexo.kernel.runtime.resources import load_yaml
from nexo.modules.execution.domain.routing.catalog import Catalog
from nexo.modules.execution.domain.routing.router import RouteAvailability, RoutingRequest, choose


def test_dedup_lessons() -> None:
    a = Lesson(
        "pitfall",
        "Estorno sem idempotência",
        "Toda chamada de estorno deve usar chave de idempotência "
        "para evitar duplicidade quando o cliente repete a requisição.",
        ["bug:B-1"],
    )
    b = Lesson(
        "pitfall",
        "Estorno sem idempotência (2)",
        "Toda chamada de estorno deve usar chave de idempotência "
        "para evitar duplicidade quando o cliente repete a requisição.",
        ["bug:B-2"],
    )
    c = Lesson("standard", "Logs sem CPF", "Mascarar CPF em logs estruturados.", ["prc:1"])
    new, dup = dedup_lessons([a, b, c], ["Logs sem CPF\nMascarar CPF em logs estruturados sempre."])
    assert [x.title for x in new] == ["Estorno sem idempotência"] and len(dup) == 2


def test_dependency_adoption() -> None:
    npm = '+    "zod": "^3.23.0",\n     "react": "18"\n'
    py = '+    "httpx>=0.27",\n+    "pydantic[email]>=2",\n'
    cs = '+    <PackageReference Include="Polly" Version="8.0.0" />\n'
    assert added_dependencies(npm) == {("npm", "zod")}
    assert added_dependencies(py) == {("pypi", "httpx"), ("pypi", "pydantic")}
    assert added_dependencies(cs) == {("nuget", "polly")}
    found = adoptions({"a": npm, "b": npm + py, "c": cs})
    assert [(x.package, len(x.commits)) for x in found] == [("zod", 2)]


def test_eval_stats() -> None:
    lo, hi = wilson(8, 10)
    assert 0.5 < lo < 0.8 < hi <= 1.0 and wilson(0, 0) == (0.0, 1.0)
    strong = compare(VariantResult("a", 10, 9, [1.0] * 10), VariantResult("b", 10, 3, [1.0] * 10))
    assert strong["winner"] == "a" and strong["p_value"] < 0.1
    tie = compare(VariantResult("a", 10, 7, [2.0] * 10), VariantResult("b", 10, 7, [1.0] * 10))
    assert tie["winner"] == "b" and "mais barato" in tie["verdict"]
    small = compare(VariantResult("a", 3, 3), VariantResult("b", 3, 0))
    assert small["winner"] is None and "insuficiente" in small["verdict"]
    same = compare(VariantResult("a", 6, 3, [1.0] * 6), VariantResult("b", 6, 3, [1.0] * 6))
    assert same["winner"] is None


def test_posterior_and_router_learning() -> None:
    assert posterior(0.75, RouteStats()) == 0.75
    assert posterior(0.75, RouteStats(0, 10)) < 0.4
    assert 0 <= thompson(0.5, RouteStats(3, 1), random.Random(1)) <= 1
    cat = Catalog.from_mapping(load_yaml("routing.yaml"))
    avail = {t: RouteAvailability() for t in ("claude", "copilot", "devin")}
    base = choose(cat, RoutingRequest("developer", {"kind": "backend", "size": "M"}), avail)
    assert base.route and base.route.id == "claude/sonnet"
    learned = choose(
        cat,
        RoutingRequest(
            "developer",
            {"kind": "backend", "size": "M"},
            observed={"claude/sonnet": (1, 12), "copilot/claude-sonnet-4.5": (12, 0)},
        ),
        avail,
    )
    assert learned.route and learned.route.id == "copilot/claude-sonnet-4.5"
