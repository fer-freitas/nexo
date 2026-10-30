from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from nexo.kernel.config.cascade import (
    Layer,
    ensure_can_set,
    flatten,
    get_path,
    resolve,
    set_path,
    unset_path,
    violations,
)
from nexo.kernel.errors import PolicyViolation


def layers(**levels: dict[str, object]) -> list[Layer]:
    return [Layer.from_mapping(level, data) for level, data in levels.items()]


def test_precedence_personal_wins() -> None:
    res = resolve(
        layers(
            default={"profile": "balanceado"},
            project={"profile": "economico"},
            personal={"profile": "maxima-qualidade"},
        )
    )
    assert res.get("profile") == "maxima-qualidade"
    assert res.origin["profile"] == "personal"


def test_order_independent_of_input_order() -> None:
    ls = layers(personal={"a": 2}, org={"a": 1})
    assert resolve(ls).get("a") == 2


def test_org_locked_key_cannot_be_overridden() -> None:
    res = resolve(layers(org={"profile": "balanceado", "locked": ["profile"]}, personal={"profile": "economico"}))
    assert res.get("profile") == "balanceado"
    assert res.locked_by == {"profile": "org"}


def test_lock_covers_subtree_and_parent_override() -> None:
    ls = layers(
        org={"gates": {"deploy_prod": "human"}, "locked": ["gates.deploy_prod"]},
        squad={"gates": {"deploy_prod": "auto", "code": 0.9}},
    )
    res = resolve(ls)
    assert res.get("gates.deploy_prod") == "human"
    assert res.get("gates.code") == 0.9
    assert ("gates.deploy_prod", "squad", "org") in violations(ls)


def test_ensure_can_set_raises_for_locked() -> None:
    ls = layers(org={"telemetry": {"enabled": False}, "locked": ["telemetry"]})
    with pytest.raises(PolicyViolation):
        ensure_can_set(ls, "telemetry.enabled", "personal")
    ensure_can_set(ls, "profile", "personal")


def test_squad_lock_applies_to_project() -> None:
    ls = layers(squad={"max_parallel": 2, "locked": ["max_parallel"]}, project={"max_parallel": 5})
    assert resolve(ls).get("max_parallel") == 2
    with pytest.raises(PolicyViolation):
        ensure_can_set(ls, "max_parallel", "project")


def test_invalid_locked_format() -> None:
    with pytest.raises(ValueError, match="locked"):
        Layer.from_mapping("org", {"locked": "profile"})
    with pytest.raises(ValueError):
        Layer.from_mapping("nope", {})
    with pytest.raises(ValueError):
        ensure_can_set([], "a", "nope")


def test_path_helpers() -> None:
    data: dict[str, object] = {}
    set_path(data, "a.b.c", 1)
    assert get_path(data, "a.b.c") == 1
    assert flatten(data) == {"a.b.c": 1}
    assert unset_path(data, "a.b.c")
    assert not unset_path(data, "a.x.y")
    with pytest.raises(KeyError):
        get_path(data, "a.b.c")


keys = st.sampled_from(["profile", "lang", "max_parallel", "gates.code", "gates.merge"])
values = st.integers(0, 9)


@given(
    org_val=values,
    lower=st.lists(st.tuples(st.sampled_from(["squad", "project", "personal"]), values)),
    key=keys,
)
def test_property_org_locked_value_always_wins(org_val: int, lower: list[tuple[str, int]], key: str) -> None:
    org: dict[str, object] = {"locked": [key]}
    set_path(org, key, org_val)
    ls = [Layer.from_mapping("org", org)]
    for level, value in lower:
        data: dict[str, object] = {}
        set_path(data, key, value)
        ls.append(Layer.from_mapping(level, data))
    assert resolve(ls).get(key) == org_val


def test_unset_prunes_empty_parents_so_lower_layers_show_through() -> None:
    """`config unset` não deixa `{}` que esconderia o valor da org."""
    from nexo.kernel.config.cascade import Layer, resolve, unset_path

    project = {"standards": {"disabled": ["X-RULE"]}, "lang": "pt-BR"}
    assert unset_path(project, "standards.disabled") and project == {"lang": "pt-BR"}
    org = Layer.from_mapping("org", {"standards": {"disabled": ["DDD-DOMAIN-INFRA"]}}, "org.yaml")
    masked = Layer.from_mapping("project", {"standards": {}}, "p.yaml")  # arquivo antigo com dicionário vazio
    res = resolve([org, masked])
    assert res.get("standards.disabled") == ["DDD-DOMAIN-INFRA"] and res.origin["standards.disabled"] == "org"
