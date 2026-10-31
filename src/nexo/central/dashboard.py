"""HTML do dashboard: servido pelo coletor, sem CDN e sem JavaScript; CSS e gráficos SVG inline.

Toda página recebe dados já filtrados pela visibilidade (o HTML só apresenta). Cores, fontes e nome vêm da marca
(`resources/brand.yaml`, sobrescrita por `NX_BRAND`); aqui só se usam as variáveis (`var(--accent)`).

Ordem de leitura de cada página: perguntas respondidas → o que precisa de atenção → gráficos → tabelas →
todos os indicadores (recolhidos quando a página tem perguntas).
"""

from __future__ import annotations

import html
import math
import time
import urllib.parse
from typing import Any

from nexo.central import brand as collector_brand
from nexo.central.dashboard_css import CSS
from nexo.central.visibility import ALL_PAGES, Principal
from nexo.kernel.brand import css as brand_css
from nexo.kernel.finops.money import brl

NAV: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Resultado", ("executive", "quality", "dora")),
    ("Custo", ("finops", "routing")),
    ("Times", ("squad", "me")),
    ("Plataforma", ("board", "insights", "brain", "fleet", "flags", "killswitch", "people")),
)
MONTHS = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")


def e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def fmt(value: Any, kind: str = "num") -> str:
    if value is None:
        return "—"
    if kind == "brl":
        return brl(float(value))
    if kind == "pct":
        return f"{float(value):.1f}%".replace(".", ",")
    if kind == "int":
        return f"{int(value):,}".replace(",", ".")
    if isinstance(value, float):
        return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return str(value)


def month_label(value: str) -> str:
    """`2026-10` → `out/26` (eixo dos gráficos)."""
    if len(value) == 7 and value[4] == "-" and value[5:].isdigit():
        return f"{MONTHS[int(value[5:]) - 1]}/{value[2:4]}"
    if len(value) == 8 and value[4:6] == "-S" and value[6:].isdigit():  # semana ISO: 2026-S41
        return f"sem {value[6:]}"
    return value[-7:]


def _head(title: str) -> str:
    current = collector_brand.current()
    return (
        f"<!doctype html><html lang=pt-BR><head><meta charset=utf-8><meta name=viewport "
        f'content="width=device-width,initial-scale=1"><title>{e(title)} · {e(current.name)}</title>'
        f"<style>{brand_css(current)}{CSS}</style></head>"
    )


def layout(title: str, principal: Principal | None, active: str, body: str) -> str:
    name = collector_brand.current().name
    if principal is None:
        return f"{_head(title)}<body><div class=center>{body}</div></body></html>"
    groups = []
    for group, keys in NAV:
        links = [
            f'<a class="{"on" if key == active else ""}" href="/dash/{key}">{e(ALL_PAGES[key][0])}</a>'
            for key in keys
            if key in ALL_PAGES and principal.can_page(key)
        ]
        if links:
            groups.append(f"<div class=group><span>{e(group)}</span>{''.join(links)}</div>")
    side = (
        f'<nav class=side aria-label="Páginas"><a class=brand href="/dash"><i></i>{e(name)}</a>{"".join(groups)}'
        f"<div class=me><span>{e(principal.dev)}</span><small>perfil {e(principal.role)}</small>"
        '<a class=btn href="/logout">Sair</a></div></nav>'
    )
    return f"{_head(title)}<body><div class=app>{side}<main>{body}</main></div></body></html>"


# ---------------------------------------------------------------------- filtros


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
            ("squad", "squad"),
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
        + select("squad", "squad")
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


# ---------------------------------------------------------------------- blocos


def _drill_link(drill: dict[str, str] | None, filters: dict[str, str]) -> str | None:
    if not drill:
        return None
    return "/dash/drill?" + urllib.parse.urlencode({**filters, **drill})


def _kpis(kpis: list[dict[str, Any]], filters: dict[str, str]) -> str:
    cards = []
    for k in kpis:
        if k["value"] is None:
            cards.append(
                f'<div class="k empty-k" id="kpi-{e(k["id"])}"><small>{e(k["label"])}</small><div>sem dados</div></div>'
            )
            continue
        value = fmt(k["value"], k["fmt"])
        link = _drill_link(k.get("drill"), filters)
        inner = f'<a href="{e(link)}" title="ver de onde vem o número">{e(value)} ↗</a>' if link else e(value)
        cards.append(f'<div class=k id="kpi-{e(k["id"])}"><small>{e(k["label"])}</small><div>{inner}</div></div>')
    return f"<div class=kgrid>{''.join(cards)}</div>" if cards else ""


def _questions(items: list[dict[str, Any]], filters: dict[str, str]) -> str:
    if not items:
        return ""
    cards = []
    for q in items:
        value = fmt(q["value"], q["fmt"]) if q["value"] is not None else "—"
        link = _drill_link(q.get("drill"), filters)
        big = (
            f'<a class=big href="{e(link)}" title="ver de onde vem o número">{e(value)}</a>'
            if link and q["value"] is not None
            else f"<span class=big>{e(value)}</span>"
        )
        extra = ""
        if q.get("bar") is not None:
            width = max(0.0, min(100.0, float(q["bar"])))
            extra += f'<div class=bar aria-hidden=true><span style="width:{width:.0f}%"></span></div>'
        if q.get("status_text"):
            extra += f'<span class="pill {e(q.get("status") or "neutral")}">{e(q["status_text"])}</span>'
        cards.append(
            f'<div class=q id="q-{e(q["id"])}"><span class=ask>{e(q["ask"])}</span>{big}'
            f"<span class=ctx>{e(q['context'])}</span>{extra}</div>"
        )
    return f"<div class=questions>{''.join(cards)}</div>"


def _attention(items: list[dict[str, str]]) -> str:
    if not items:
        return (
            "<section class=card><div class=card-head><h2>Precisa de atenção</h2><small>nada agora</small></div>"
            "<p class=empty><b>Tudo em dia.</b> Nada parado ou faltando neste recorte.</p></section>"
        )
    rows = []
    for a in items:
        button = f'<a class=btn href="{e(a["link"])}">{e(a["action"] or "abrir")}</a>' if a.get("link") else ""
        code = f"<code>{e(a['action'])}</code>" if a.get("action") and not a.get("link") else ""
        rows.append(
            f'<li><span class="sev {e(a["level"])}"></span><div><b>{e(a["title"])}</b>'
            f"<small>{e(a['detail'])}</small>{code}</div>{button}</li>"
        )
    n = len(items)
    return (
        f"<section class=card><div class=card-head><h2>Precisa de atenção</h2><small>{n} "
        f"{'item' if n == 1 else 'itens'}</small></div><ul class=att>{''.join(rows)}</ul></section>"
    )


def _nice(top: float) -> float:
    if top <= 0:
        return 1.0
    power = float(10 ** math.floor(math.log10(top)))
    for step in (1, 2, 2.5, 5, 10):
        if top <= step * power:
            return step * power
    return 10 * power


def _tick(value: float, kind: str) -> str:
    if kind == "pct":
        return f"{value:.0f}%"
    if value >= 1000:
        return f"{value / 1000:.1f}k".replace(".0k", "k").replace(".", ",")
    return f"{value:.0f}" if value == int(value) else f"{value:.1f}".replace(".", ",")


def _chart(c: dict[str, Any]) -> str:
    points = [(str(k), None if v is None else float(v)) for k, v in c["points"]]
    if not points:
        return ""
    title = f"<div class=card-head><h2>{e(c['title'])}</h2></div>"
    if not any(v for _, v in points):
        return (
            f'<section class=card id="c-{e(c["id"])}">{title}<p class=empty><b>Sem dados no período.</b> '
            "O gráfico aparece quando houver uso registrado.</p></section>"
        )
    kind = c["fmt"]
    top = 100.0 if kind == "pct" else _nice(max(v or 0 for _, v in points))
    width, height, left, right, upper, lower = 640, 210, 52, 12, 22, 30
    plot_w, plot_h = width - left - right, height - upper - lower
    slot = plot_w / len(points)
    bar = max(6.0, min(56.0, slot * 0.58))
    parts = []
    for frac in (0, 0.5, 1):
        y = upper + plot_h * (1 - frac)
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}"/>')
        parts.append(f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end">{e(_tick(top * frac, kind))}</text>')
    peak = max(range(len(points)), key=lambda i: points[i][1] or 0)
    last = len(points) - 1
    for i, (label, value) in enumerate(points):
        cx = left + slot * i + slot / 2
        v = value or 0
        h = plot_h * min(v, top) / top
        shown = fmt(value, kind) if value is not None else "sem dados"
        if v <= 0:
            parts.append(
                f'<rect class=zero x="{cx - bar / 2:.1f}" y="{upper + plot_h - 1:.1f}" width="{bar:.1f}" height="1">'
                f"<title>{e(month_label(label))}: {e(shown)}</title></rect>"
            )
        else:
            cls = "" if i == last else " class=past"
            parts.append(
                f'<rect{cls} x="{cx - bar / 2:.1f}" y="{upper + plot_h - h:.1f}" width="{bar:.1f}" height="{h:.1f}" '
                f'rx="3"><title>{e(month_label(label))}: {e(shown)}</title></rect>'
            )
            if i in (last, peak):
                parts.append(
                    f'<text class=v x="{cx:.1f}" y="{upper + plot_h - h - 6:.1f}" text-anchor="middle">'
                    f"{e(shown)}</text>"
                )
        parts.append(f'<text x="{cx:.1f}" y="{height - 10}" text-anchor="middle">{e(month_label(label))}</text>')
    return (
        f'<section class=card id="c-{e(c["id"])}">{title}<svg class=chart viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="{e(c["title"])}">{"".join(parts)}</svg></section>'
    )


STATE_PILL = {
    "Concluido": ("concluída", "good"),
    "AguardandoHumano": ("aguardando decisão", "warn"),
    "Escalado": ("escalada", "bad"),
    "Bloqueado": ("bloqueada", "bad"),
    "EmDev": ("em andamento", "neutral"),
    "Pronto": ("pronta", "neutral"),
    "Refinamento": ("em refinamento", "neutral"),
    "Cancelado": ("cancelada", "neutral"),
}


def _cell(column: str, value: Any, names: dict[str, str] | None = None) -> str:
    if column == "estado" and value in STATE_PILL:
        label, level = STATE_PILL[value]
        return f"<td><span class='pill {level}'>{e(label)}</span></td>"
    if column == "projeto" and isinstance(value, str) and names and names.get(value):  # nome enviado pela CLI
        return f'<td title="{e(value)}">{e(names[value])}</td>'
    if column == "projeto" and isinstance(value, str) and len(value) >= 12 and value.isalnum():
        return f'<td><code class=id title="{e(value)}">{e(value[:8])}</code></td>'  # sem nome: código curto
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"<td class=n>{e(fmt(value))}</td>"
    return f"<td>{e(fmt(value))}</td>"


def _table(t: dict[str, Any], names: dict[str, str] | None = None) -> str:
    numeric = [
        bool(t["rows"]) and all(isinstance(r[i], (int, float)) or r[i] is None for r in t["rows"])
        for i in range(len(t["columns"]))
    ]
    head = "".join(f"<th{' class=n' if numeric[i] else ''}>{e(c)}</th>" for i, c in enumerate(t["columns"]))
    rows = "".join(
        "<tr>" + "".join(_cell(t["columns"][i], v, names) for i, v in enumerate(row)) + "</tr>" for row in t["rows"]
    )
    cls = " ind" if t.get("individual") else ""
    badge = "<span class=tag>individual · auditado</span>" if t.get("individual") else ""
    count = f"<small>{len(t['rows'])} {'linha' if len(t['rows']) == 1 else 'linhas'}</small>" if t["rows"] else ""
    body = (
        f"<div class=wrap><table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>"
        if t["rows"]
        else "<p class=empty>Nada neste recorte. Ajuste o período ou os filtros.</p>"
    )
    return (
        f'<section class="card{cls}" id="t-{e(t["id"])}"><div class=card-head><h2>{e(t["title"])}{badge}</h2>'
        f"{count}</div>{body}</section>"
    )


def _period_text(data: dict[str, Any]) -> str:
    scope = "todos os squads" if data["squads"] is None else (", ".join(data["squads"]) or "nenhum squad")
    start, end = data["period"]
    span = month_label(start) if start == end else f"{month_label(start)} a {month_label(end)}"
    return f"{span} · {scope}"


def render_page(
    data: dict[str, Any], principal: Principal, opts: dict[str, list[str]], names: dict[str, str] | None = None
) -> str:
    filters = data["filters"]
    controls = filters_form(data["page"], filters, data["period"], opts, names) if data["page"] != "me" else ""
    body = f"<div class=top><div><h1>{e(data['title'])}</h1><p>{e(_period_text(data))}</p></div>{controls}</div>"
    headline = data.get("headline") or []
    body += _questions(headline, filters)
    if data["kpis"] and not headline:  # sem perguntas: o resumo (indicadores) vem antes do detalhe
        body += _kpis(data["kpis"], filters)
    charts = [_chart(c) for c in data["charts"]]
    if headline or data.get("attention"):
        todo = _attention(data.get("attention") or [])
        body += f"<div class=cols>{charts[0]}{todo}</div>" if charts else todo
        charts = charts[1:]
    if charts:
        body += f"<div class=charts>{''.join(charts)}</div>"
    body += "".join(_table(t, names) for t in data["tables"])
    if data["kpis"] and headline:
        grid = _kpis(data["kpis"], filters)
        body += f"<details class=all><summary>Todos os indicadores ({len(data['kpis'])})</summary>{grid}</details>"
    if data["org"]:
        body += (
            "<section class=card><div class=card-head><h2>Referência: total da org</h2>"
            f"<small>para comparar com o seu recorte</small></div>{_kpis(data['org'], {})}</section>"
        )
    body += "".join(f"<p class=note>{e(n)}</p>" for n in data["notes"])
    return layout(data["title"], principal, data["page"], body)


# ---------------------------------------------------------------------- outras páginas


def render_insights(items: list[dict[str, Any]], principal: Principal, csrf: str | None, state: str) -> str:
    labels = {
        "aberto": "abertos",
        "reconhecido": "reconhecidos",
        "resolvido": "resolvidos",
        "descartado": "descartados",
    }
    tabs = "".join(
        f'<a class="chip{" on" if s == state else ""}" href="/dash/insights?state={s}">{labels.get(s, "todos")}</a>'
        for s in ("aberto", "reconhecido", "resolvido", "descartado", "")
    )
    cards = []
    for i in items:
        actions = ""
        if csrf is not None and i["state"] in ("aberto", "reconhecido"):
            options = "".join(
                f'<option value="{s}">{s}</option>'
                for s in (["reconhecido"] if i["state"] == "aberto" else []) + ["resolvido", "descartado"]
            )
            actions = (
                f'<form method=post action="/dash/insights/{e(i["id"])}" class=inline><input type=hidden name=csrf '
                f'value="{e(csrf)}"><label>novo estado<select name=state>{options}</select></label>'
                "<label>motivo (obrigatório para descartar)<input name=reason size=40></label>"
                "<button class='btn primary'>Atualizar</button></form>"
            )
        evidence = ", ".join(f"{e(k)}: {e(v)}" for k, v in i["evidence"].items())
        issue = f' · <a href="{e(i["issue_url"])}">issue</a>' if i.get("issue_url") else ""
        level = {"alta": "bad", "media": "warn"}.get(str(i["severity"]), "info")
        cards.append(
            f'<section class=card id="i-{e(i["id"])}"><ul class=att><li><span class="sev {level}"></span><div>'
            f"<b>{e(i['title'])}</b><small>{e(i['detector'])} · {e(i['state'])} · dono sugerido: {e(i['owner'])}"
            f" · desde {e(i['first_seen'][:10])}{issue}</small></div><span class='pill {level}'>"
            f"{e(i['severity'])}</span></li></ul><p>{e(i['action'])}</p><p class=note>evidência: {evidence}</p>"
            + (f"<p class=note>motivo: {e(i['reason'])}</p>" if i.get("reason") else "")
            + actions
            + "</section>"
        )
    empty = "<p class=empty><b>Nenhum insight neste estado.</b> Os detectores rodam a cada envio de telemetria.</p>"
    body = (
        f"<div class=top><div><h1>Insights</h1><p>o que os detectores acharam e quem deve olhar</p></div>"
        f"<div class=chips>{tabs}</div></div>" + ("".join(cards) or empty)
    )
    return layout("Insights", principal, "insights", body)


def render_drill(data: dict[str, Any], principal: Principal) -> str:
    t = {
        "id": "drill",
        "title": f"Origem dos números ({data['source']})",
        "columns": data["columns"],
        "rows": data["rows"],
        "individual": None,
    }
    return layout(
        "De onde vem o número",
        principal,
        "",
        "<div class=top><div><h1>De onde vem o número</h1><p>as linhas que somam o indicador</p></div></div>"
        + _table(t)
        + "<p class=note>ids de projeto são hashes; nenhuma pessoa é identificada.</p>",
    )


def render_login(message: str = "", token_login: bool = True, next_url: str = "/dash") -> str:
    name = collector_brand.current().name
    form = (
        f'<form method=post action="/login"><input type=hidden name=next value="{e(next_url)}">'
        "<label>Token pessoal<input type=password name=token autocomplete=off></label>"
        "<button class='btn primary'>Entrar</button></form>"
        if token_login
        else '<a class="btn primary" href="/login">Entrar com a conta corporativa</a>'
    )
    msg = f"<p class=err>{e(message)}</p>" if message else ""
    return layout(
        "Entrar",
        None,
        "",
        f'<div class=login><a class=brand href="/dash"><i></i>{e(name)}</a><h1>Entrar no portal</h1>{msg}{form}'
        "<p class=note>Quem administra o portal cria o seu token.</p></div>",
    )


def _simple(title: str, principal: Principal, body: str) -> str:
    return layout(
        title, principal, "", f"<div class=top><div><h1>{e(title)}</h1></div></div><section class=card>{body}</section>"
    )


def render_verify(principal: Principal, csrf: str, message: str = "") -> str:
    msg = f"<p>{e(message)}</p>" if message else ""
    return _simple(
        "Autorizar dispositivo",
        principal,
        f"{msg}<p>Digite o código mostrado por <code>nexo login central</code>.</p>"
        f'<form method=post action="/v2/device/verify" class=inline><input type=hidden name=csrf value="{e(csrf)}">'
        '<label>código<input name=user_code placeholder="ABCD-1234" size=12></label>'
        "<button class='btn primary'>Autorizar</button></form>",
    )


def render_clarification(principal: Principal, cid: str, csrf: str, message: str = "") -> str:
    msg = f"<p>{e(message)}</p>" if message else ""
    return _simple(
        "Responder ao Nexo",
        principal,
        f"{msg}<p class=note>Pergunta {e(cid)} — o texto está no cartão do Teams "
        "(o central não guarda a pergunta).</p>"
        f'<form method=post action="/v2/clarifications/{e(cid)}/form" class=inline>'
        f'<input type=hidden name=csrf value="{e(csrf)}"><label>resposta<textarea name=answer rows=5 cols=70>'
        "</textarea></label><button class='btn primary'>Enviar</button></form>",
    )


def render_approval(
    principal: Principal, aid: str, gate: str, csrf: str, decided: str | None = None, message: str = ""
) -> str:
    msg = f"<p>{e(message)}</p>" if message else ""
    if decided:
        label = {"approve": "aprovada", "reject": "reprovada"}.get(decided, decided)
        body = f"<p>Decisão registrada: <b>{e(label)}</b>.</p>"
    else:
        body = (
            f'<form method=post action="/v2/approvals/{e(aid)}/form" class=inline>'
            f'<input type=hidden name=csrf value="{e(csrf)}">'
            "<label>motivo (obrigatório para reprovar)<textarea name=reason rows=3 cols=70></textarea></label>"
            "<button class='btn primary' name=decision value=\"approve\">Aprovar</button>"
            '<button class=btn name=decision value="reject">Reprovar</button></form>'
        )
    return _simple(
        f"Portão {gate} aguardando decisão",
        principal,
        f"{msg}<p class=note>Aprovação {e(aid)} — o motivo do portão e o resumo estão no cartão do Teams (o central "
        f"não guarda código nem diff). Quem disparou a história não pode decidir.</p>{body}",
    )


FLAG_COLUMNS = (
    ("key", "flag"),
    ("title", "funcionalidade"),
    ("area", "área"),
    ("state", "empresa"),
    ("squads", "squads"),
    ("stage", "estágio"),
    ("module", "módulo"),
    ("depends", "desliga junto"),
)


def render_flags(
    snap: dict[str, Any],
    hist: list[dict[str, Any]],
    principal: Principal,
    csrf: str | None,
    area: str = "",
    sort: str = "",
    desc: bool = False,
    machines: int = 0,
) -> str:
    """Feature flags com filtro por área e ordenação por coluna (links do servidor, sem JavaScript)."""
    from nexo.kernel import modules
    from nexo.kernel.config.flags import CATALOG

    areas = sorted({f.area for f in CATALOG})
    sort = sort if sort in dict(FLAG_COLUMNS) else "area"
    items = []
    for f in CATALOG:
        if area and f.area != area:
            continue
        value = snap["global"].get(f.key)
        state = {True: ("ligada", "good"), False: ("desligada", "bad")}.get(value, ("padrão (ligada)", "neutral"))
        squads = (
            ", ".join(f"{s}: {'on' if v[f.key] else 'off'}" for s, v in sorted(snap["squads"].items()) if f.key in v)
            or "—"
        )
        projects = ", ".join(
            f"{s}: {'on' if v[f.key] else 'off'}" for s, v in sorted((snap.get("projects") or {}).items()) if f.key in v
        )
        if projects:
            squads = f"{squads} · projeto {projects}" if squads != "—" else f"projeto {projects}"
        items.append(
            {
                "key": f.key,
                "title": f.title,
                "area": f.area,
                "state": state,
                "squads": squads,
                "stage": f.stage,
                "module": f.module,
                "depends": ", ".join(modules.dependents(f.module)) or "—",
            }
        )
    items.sort(key=lambda i: (str(i[sort][0] if sort == "state" else i[sort]).lower(), i["key"]), reverse=desc)
    rows = [
        f"<tr><td><code class=id>{e(i['key'])}</code></td><td>{e(i['title'])}</td><td>{e(i['area'])}</td>"
        f"<td><span class='pill {i['state'][1]}'>{e(i['state'][0])}</span></td><td>{e(i['squads'])}</td>"
        f"<td>{e(i['stage'])}</td><td>{e(i['module'])}</td><td>{e(i['depends'])}</td></tr>"
        for i in items
    ]

    def link(**changes: str) -> str:
        query = {k: v for k, v in {"area": area, "sort": sort, "dir": "desc" if desc else "", **changes}.items() if v}
        return "/dash/flags" + (f"?{urllib.parse.urlencode(query)}" if query else "")

    head = "".join(
        f'<th><a class=sort href="{e(link(sort=key, dir="" if sort != key or desc else "desc"))}">{e(label)}'
        f"{(' ▼' if desc else ' ▲') if sort == key else ''}</a></th>"
        for key, label in FLAG_COLUMNS
    )
    chips = f'<a class="chip{" on" if not area else ""}" href="{e(link(area=""))}">todas</a>' + "".join(
        f'<a class="chip{" on" if a == area else ""}" href="{e(link(area=a))}">{e(a)}</a>' for a in areas
    )
    form = ""
    if csrf is not None:
        options = "".join(f'<option value="{e(f.key)}">{e(f.key)}</option>' for f in CATALOG)
        form = (
            "<section class=card><div class=card-head><h2>Alterar</h2></div>"
            f"<p>Antes de aplicar: {machines} máquina(s) ativas na empresa. A coluna de dependências mostra o que "
            "desliga junto. Vale em até 2 minutos para quem está online; offline, vale o último valor conhecido.</p>"
            f'<form method=post action="/dash/flags" class=inline>'
            f'<input type=hidden name=csrf value="{e(csrf)}"><label>flag<select name=key>{options}</select></label>'
            "<label>valor<select name=value><option>off</option><option>on</option><option>inherit</option>"
            "</select></label><label>squad (vazio = empresa)<input name=squad size=14></label>"
            "<label>projeto<input name=project size=14></label>"
            "<label>motivo (obrigatório)<input name=reason size=40 required></label><button class='btn primary'>Aplicar</button>"
            "</form></section>"
        )
    history = "".join(
        f"<tr><td><code class=id>{e(h['key'])}</code></td><td>{e(h['squad'] or 'empresa')}</td>"
        f"<td>{e(h['from_value'])}</td><td>{e(h['to_value'])}</td><td>{e(h['actor'])}</td><td>{e(h['reason'])}</td></tr>"
        for h in hist
    )
    history_body = (
        "<div class=wrap><table><thead><tr><th>flag</th><th>escopo</th><th>de</th><th>para</th><th>quem</th>"
        f"<th>motivo</th></tr></thead><tbody>{history}</tbody></table></div>"
        if hist
        else "<p class=empty>Nenhuma mudança registrada.</p>"
    )
    body = (
        "<div class=top><div><h1>Feature flags</h1><p>Precedência: central por squad → central → cérebro do squad → "
        "cérebro da org → padrão. O piso de segurança (política, auditoria, assinatura, telemetria, kill switch) não "
        "tem flag.</p></div></div>" + form + "<section class=card><div class=card-head><h2>Funcionalidades</h2>"
        f"<small>{len(rows)} de {len(CATALOG)}</small></div><div class=chips role=group aria-label=Área>{chips}</div>"
        f"<div class=wrap><table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>"
        f"<section class=card><div class=card-head><h2>Sempre ligado</h2><small>piso</small></div>"
        f"<p>{e(', '.join(sorted(modules.FLOOR)))}: fazem parte do piso de segurança, sem botão e com recusa no servidor.</p></section>"
        f"<section class=card><div class=card-head><h2>Histórico</h2></div>{history_body}</section>"
    )
    return layout("Feature flags", principal, "flags", body)
