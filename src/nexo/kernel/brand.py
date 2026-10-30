"""Identidade visual (marca): nome, fontes e cores do dashboard e do console num arquivo só.

O padrão fica em `resources/brand.yaml`; a org sobrescreve só o que muda (cérebro da org para o console,
`NX_BRAND` no coletor). Aqui só se valida, mescla e traduz para CSS e para estilos do terminal (sem E/S).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from nexo.kernel.errors import NexoError

HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
THEME_KEYS = (
    "bg",
    "surface",
    "surface_2",
    "line",
    "text",
    "muted",
    "faint",
    "accent",
    "accent_soft",
    "on_accent",
    "good",
    "good_soft",
    "warn",
    "warn_soft",
    "bad",
    "bad_soft",
)
TERMINAL_KEYS = ("accent", "good", "warn", "bad")
SECTIONS = {"name", "fonts", "light", "dark", "terminal"}
FONT_KEYS = ("sans", "mono")


@dataclass(frozen=True)
class Brand:
    name: str
    fonts: dict[str, str]
    light: dict[str, str]
    dark: dict[str, str]
    terminal: dict[str, str]


def merge(default: dict[str, Any], override: dict[str, Any] | None = None, source: str = "brand.yaml") -> Brand:
    """Marca final: o padrão com a sobrescrita por cima, chave a chave. Erro claro em chave ou cor inválida."""
    override = override or {}
    unknown = sorted(set(override) - SECTIONS)
    if unknown:
        raise NexoError(f"{source}: seção desconhecida {', '.join(unknown)} (use {', '.join(sorted(SECTIONS))})")
    parts: dict[str, dict[str, str]] = {}
    for section, keys in (
        ("fonts", FONT_KEYS),
        ("light", THEME_KEYS),
        ("dark", THEME_KEYS),
        ("terminal", TERMINAL_KEYS),
    ):
        extra = override.get(section) or {}
        if not isinstance(extra, dict):
            raise NexoError(f"{source}: `{section}` deve ser um mapeamento")
        bad = sorted(set(extra) - set(keys))
        if bad:
            raise NexoError(f"{source}: {section}.{bad[0]} não existe (chaves: {', '.join(keys)})")
        values = {k: str(v) for k, v in {**(default.get(section) or {}), **extra}.items()}
        if section != "fonts":
            wrong = [f"{section}.{k}={v}" for k, v in values.items() if not HEX.match(v)]
            if wrong:
                raise NexoError(f"{source}: cor inválida {wrong[0]} (use #RRGGBB)")
        missing = [k for k in keys if k not in values]
        if missing:
            raise NexoError(f"{source}: falta {section}.{missing[0]}")
        parts[section] = values
    name = str(override.get("name") or default.get("name") or "Nexo").strip()
    return Brand(name=name, **parts)


def _tokens(theme: dict[str, str]) -> str:
    return ";".join(f"--{k.replace('_', '-')}:{v}" for k, v in theme.items())


def css(brand: Brand) -> str:
    """Variáveis CSS nos três estados do tema: claro (padrão), escuro pelo sistema e escuro/claro escolhido."""
    fonts = f"--sans:{brand.fonts['sans']};--mono:{brand.fonts['mono']}"
    dark = _tokens(brand.dark) + ";color-scheme:dark"
    return (
        f":root{{{_tokens(brand.light)};{fonts};color-scheme:light}}"
        f'@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{{dark}}}}}'
        f':root[data-theme="dark"]{{{dark}}}'
    )


def terminal_styles(brand: Brand) -> dict[str, str]:
    """Estilos nomeados do console (rich): o código usa `nexo.accent`, nunca uma cor solta."""
    t = brand.terminal
    return {
        "nexo.accent": t["accent"],
        "nexo.title": "bold",
        "nexo.brand": f"bold {t['accent']}",
        "nexo.good": t["good"],
        "nexo.warn": t["warn"],
        "nexo.bad": t["bad"],
        "nexo.muted": "dim",
        "nexo.key": f"bold {t['accent']}",
        "nexo.chip": f"{t['accent']}",
    }
