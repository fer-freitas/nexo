"""Filtros do dashboard: período, squads e o restante, sem JavaScript."""

from __future__ import annotations

import html
import time
import urllib.parse
from typing import Any

MONTHS = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")


def e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def month_label(value: str) -> str:
    """`2026-10` → `out/26` (eixo dos gráficos)."""
    if len(value) == 7 and value[4] == "-" and value[5:].isdigit():
        return f"{MONTHS[int(value[5:]) - 1]}/{value[2:4]}"
    if len(value) == 8 and value[4:6] == "-S" and value[6:].isdigit():  # semana ISO: 2026-S41
        return f"sem {value[6:]}"
    return value[-7:]


def _query(current: dict[str, str], **changes: str) -> str:
    merged = {k: v for k, v in {**current, **changes}.items() if v}
    return urllib.parse.urlencode(merged)


def _month_add(month: str, delta: int) -> str:
    year, mon = int(month[:4]), int(month[5:7])
    index = year * 12 + (mon - 1) + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def filters_form(
    page_name: str,
    current: dict[str, str],
    period: list[str],
    opts: dict[str, list[str]],
    names: dict[str, str] | None = None,
) -> str:
    """Período em atalhos; o resto dos filtros recolhido em "Mais filtros" (abre sem JavaScript)."""
    now = time.strftime("%Y-%m")
    presets = (("Este mês", now, now), ("3 meses", _month_add(now, -2), now), ("6 meses", _month_add(now, -5), now))
    chips = []
    matched = False
    for label, start, end in presets:
        on = period[0] == start and period[1] == end
        matched = matched or on
        href = f"/dash/{e(page_name)}?{e(_query(current, month_from=start, month_to=end))}"
        chips.append(f'<a class="chip{" on" if on else ""}" href="{href}">{e(label)}</a>')
    if not matched:
        chips.append(f"<span class='chip on'>{e(month_label(period[0]))} a {e(month_label(period[1]))}</span>")

    def select(name: str, label: str) -> str:
        values = opts.get(name) or []
        items = "".join(
            f'<option value="{e(v)}"{" selected" if current.get(name) == v else ""}>{e(v)}</option>' for v in values
        )
        return f'<label>{e(label)}<select name="{name}"><option value="">todos</option>{items}</select></label>'

    active = [
        (key, label)
        for key, label in (
            ("directorate", "diretoria"),
            ("sigla", "sigla"),
            ("project", "projeto"),
            ("stack", "stack"),
            ("tool", "ferramenta"),
            ("model", "modelo"),
        )
        if current.get(key)
    ]
    for key, label in active:
        href = f"/dash/{e(page_name)}?{e(_query(current, **{key: ''}))}"
        chips.append(f'<a class="chip on" href="{href}" title="tirar o filtro">{e(label)}: {e(current[key])} ✕</a>')
    form = (
        f'<form class=filters method=get action="/dash/{e(page_name)}">'
        f'<label>de<input name=month_from value="{e(period[0])}" size=7 placeholder="AAAA-MM"></label>'
        f'<label>até<input name=month_to value="{e(period[1])}" size=7 placeholder="AAAA-MM"></label>'
        + select("directorate", "diretoria")
        + (select("sigla", "sigla") if opts.get("sigla") else "")
        + _project_select(current.get("project", ""), names or {})
        + select("stack", "stack")
        + select("tool", "ferramenta")
        + select("model", "modelo")
        + "<button class='btn primary'>Aplicar</button></form>"
    )
    more = f"Mais filtros{f' ({len(active)})' if active else ''}"
    return (
        f"<div class=chips role=group aria-label=Período>{''.join(chips)}"
        f"<details class=more><summary class=chip>{e(more)} ▾</summary>{form}</details></div>"
    )


def squad_bar(action: str, current: dict[str, str], squads: list[str]) -> str:
    """Quem vê mais de um squad escolhe quais entram no recorte. Sem JavaScript: caixas de seleção num formulário."""
    if len(squads) < 2:
        return ""
    chosen = {s for s in current.get("squad", "").split(",") if s}
    boxes = "".join(
        f'<label class=pick><input type=checkbox name=squad value="{e(s)}"{" checked" if s in chosen else ""}>'
        f"<span>{e(s)}</span></label>"
        for s in squads
    )
    keep = "".join(
        f'<input type=hidden name="{e(k)}" value="{e(v)}">' for k, v in current.items() if k != "squad" and v
    )
    query = _query(current, squad="")
    clear = f'<a href="{e(action)}{"?" + e(query) if query else ""}">Ver todos</a>' if chosen else ""
    return (
        f'<form class=squadbar method=get action="{e(action)}" role=group aria-label="Filtrar por squad">{keep}'
        f"<b>Squads</b><div class=picks>{boxes}</div><button class=btn>Filtrar</button>{clear}</form>"
    )


def _project_select(current: str, names: dict[str, str]) -> str:
    """Projeto pelo nome; o mesmo nome em máquinas diferentes ganha o código curto ao lado."""
    count: dict[str, int] = {}
    for name in names.values():
        count[name] = count.get(name, 0) + 1
    items = sorted(names.items(), key=lambda kv: (kv[1].lower(), kv[0]))
    options = "".join(
        f'<option value="{e(h)}"{" selected" if h == current else ""}>'
        f"{e(n + (f' · {h[:6]}' if count[n] > 1 else ''))}</option>"
        for h, n in items
    )
    if current and current not in names:
        options += f'<option value="{e(current)}" selected>{e(current[:8])}</option>'
    return f'<label>projeto<select name=project><option value="">todos</option>{options}</select></label>'
