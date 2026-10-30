"""identidade visual: um brand.yaml só para o dashboard e o console; a org troca só o que muda."""

from __future__ import annotations

import pytest

from nexo.kernel import brand
from nexo.kernel.errors import NexoError
from nexo.kernel.finops.money import brl


def default() -> brand.Brand:
    from nexo.modules.setup.app import brand as brand_app

    return brand_app.default()


def test_default_brand_has_every_token_for_both_themes() -> None:
    b = default()
    assert b.name == "Nexo"
    assert set(b.light) == set(b.dark) == set(brand.THEME_KEYS)
    assert b.light["accent"] == "#2F5FC4" and b.dark["accent"] == "#7FA6F5"
    css = brand.css(b)
    assert "--accent:#2F5FC4" in css and "--surface-2:" in css and "--sans:" in css
    assert ':root:not([data-theme="light"])' in css and ':root[data-theme="dark"]' in css  # três estados do tema
    assert css.count(";color-scheme:dark") == 2


def test_org_overrides_only_what_changes() -> None:
    base = {k: getattr(default(), k) for k in ("fonts", "light", "dark", "terminal")} | {"name": "Nexo"}
    b = brand.merge(base, {"name": "Acme Dev", "light": {"accent": "#C2185B"}, "terminal": {"accent": "#D81B60"}})
    assert b.name == "Acme Dev" and b.light["accent"] == "#C2185B"
    assert b.light["bg"] == default().light["bg"] and b.dark["accent"] == default().dark["accent"]  # herdado
    assert brand.terminal_styles(b)["nexo.accent"] == "#D81B60"


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"light": {"accent": "vermelho"}}, "cor inválida light.accent=vermelho"),
        ({"light": {"destaque": "#FFFFFF"}}, "light.destaque não existe"),
        ({"cores": {}}, "seção desconhecida cores"),
        ({"dark": "#000000"}, "`dark` deve ser um mapeamento"),
    ],
)
def test_invalid_brand_explains_what_is_wrong(override: dict, message: str) -> None:
    base = {k: getattr(default(), k) for k in ("fonts", "light", "dark", "terminal")}
    with pytest.raises(NexoError, match=message.replace("(", r"\(").replace("[", r"\[")):
        brand.merge(base, override, "brand.yaml")


def test_money_is_always_brazilian() -> None:
    assert brl(4.32) == "R$ 4,32" and brl(1234567.891) == "R$ 1.234.567,89" and brl(None) == "—" and brl(0) == "R$ 0,00"
