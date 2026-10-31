"""identidade visual: um brand.yaml só para o dashboard e o console; a org troca só o que muda."""

from __future__ import annotations

import re
from pathlib import Path

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
    assert b.light["accent"] == "#A84A00" and b.dark["accent"] == "#FF9F47"
    css = brand.css(b)
    assert "--accent:#A84A00" in css and "--surface-2:" in css and "--sans:" in css
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


def _luminance(color: str) -> float:
    channels = [int(color.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: str, b: str) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


TEXT_ON = [
    ("text", "bg"), ("text", "surface"), ("text", "surface_2"), ("muted", "bg"), ("muted", "surface"),
    ("muted", "surface_2"), ("faint", "bg"), ("faint", "surface"), ("accent", "bg"), ("accent", "surface"),
    ("accent", "surface_2"), ("accent", "accent_soft"), ("on_accent", "accent"), ("on_brand", "brand"), ("good", "good_soft"),
    ("warn", "warn_soft"), ("bad", "bad_soft"), ("good", "surface"), ("warn", "surface"), ("bad", "surface"),
]  # fmt: skip


def test_every_text_color_is_readable_in_both_themes() -> None:
    """AA para texto normal: 4,5:1. A paleta padrão não pode depender de quem olha."""
    b = default()
    weak = [
        f"{theme} {fg}/{bg}: {_contrast(tokens[fg], tokens[bg]):.2f}"
        for theme, tokens in (("light", b.light), ("dark", b.dark))
        for fg, bg in TEXT_ON
        if _contrast(tokens[fg], tokens[bg]) < 4.5
    ]
    assert weak == []


def test_cards_stand_out_from_the_page_and_lines_are_visible() -> None:
    b = default()
    for tokens in (b.light, b.dark):
        assert _contrast(tokens["line"], tokens["surface"]) >= 1.5


def test_documentation_colors_are_readable() -> None:
    css = (Path(__file__).resolve().parents[2] / "docs" / "site" / "estilo.css").read_text(encoding="utf-8")
    t = dict(re.findall(r"--([a-z-]+):\s*(#[0-9a-f]{6})", css))
    assert "prefers-color-scheme" not in css, "a documentação tem um tema só, para não variar entre leitores"
    pairs = [
        ("texto", "fundo"), ("texto", "cartao"), ("apagado", "fundo"), ("apagado", "cartao"),
        ("laranja-texto", "fundo"), ("laranja-texto", "cartao"), ("texto", "laranja-claro"),
        ("laranja-texto", "laranja-claro"), ("bom", "bom-claro"), ("atencao", "atencao-claro"),
        ("ruim", "ruim-claro"), ("azul", "laranja"),
    ]  # fmt: skip
    assert [p for p in pairs if _contrast(t[p[0]], t[p[1]]) < 4.5] == []


def test_terminal_colors_work_on_a_light_and_on_a_dark_terminal() -> None:
    weak = [
        f"{name} sobre {background}"
        for name, color in default().terminal.items()
        for background in ("#FFFFFF", "#000000", "#0C0C0C")
        if _contrast(color, background) < 4.0
    ]
    assert weak == []
