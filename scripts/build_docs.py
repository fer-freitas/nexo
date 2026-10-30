"""Gera `docs/site/*.html` a partir de `docs/site/src/`: mesma navegação, índice da página e placar de estados."""

from __future__ import annotations

import html
import re
import sys
import unicodedata
from pathlib import Path

SITE = Path(__file__).resolve().parents[1] / "docs" / "site"
GROUPS = (
    ("Entender", (("index", "Início"), ("funcional", "Como funciona"), ("arquitetura", "Arquitetura"))),
    ("Acompanhar", (("features", "Features"),)),
    ("Decidir", (("executivo", "Para executivos"),)),
    ("Usar", (("usuarios", "Guia do usuário"),)),
)


def slug(text: str) -> str:
    plain = unicodedata.normalize("NFKD", html.unescape(text)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")


def with_anchors(body: str) -> tuple[str, list[tuple[str, str]]]:
    toc: list[tuple[str, str]] = []

    def anchor(match: re.Match[str]) -> str:
        text = re.sub(r"<[^>]+>", "", match[1])
        toc.append((slug(text), text))
        return f'<h2 id="{slug(text)}">{match[1]}</h2>'

    return re.sub(r"<h2>(.*?)</h2>", anchor, body), toc


def fill_counts(body: str) -> str:
    """O placar conta os selos da própria página: não tem como divergir da tabela."""
    rows = re.findall(r'<td><span class="selo (real|sim|falta)">', body)
    for key, name in (("real", "provado"), ("sim", "simulador"), ("falta", "pendente")):
        body = body.replace("{{" + name + "}}", str(rows.count(key)))
    return body


def navigation(current: str, toc: list[tuple[str, str]]) -> str:
    parts = []
    for group, pages in GROUPS:
        parts.append(f'<p class="grupo">{group}</p><ul>')
        for name, label in pages:
            mark = ' aria-current="page"' if name == current else ""
            parts.append(f'<li><a href="{name}.html"{mark}>{label}</a>')
            if name == current and len(toc) > 1:
                items = "".join(f'<li><a href="#{key}">{text}</a></li>' for key, text in toc)
                parts.append(f'<ul class="indice">{items}</ul>')
            parts.append("</li>")
        parts.append("</ul>")
    return "".join(parts)


def render(name: str, label: str) -> str:
    source = fill_counts((SITE / "src" / f"{name}.html").read_text(encoding="utf-8"))
    body, toc = with_anchors(source.strip())
    footer = (SITE / "src" / "_rodape.html").read_text(encoding="utf-8").strip()
    return f"""<!doctype html>
<html lang="pt-BR">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{label}: Nexo</title><link rel="stylesheet" href="estilo.css"></head>
<body>
<a class="pular" href="#conteudo">Pular para o conteúdo</a>
<div class="pagina">
<aside class="lateral"><a class="marca" href="index.html">Nexo</a><nav aria-label="Documentação">{navigation(name, toc)}</nav></aside>
<main class="conteudo" id="conteudo">
{body}
<p class="rodape">{footer}</p>
</main>
</div>
</body>
</html>
"""


def pages() -> dict[str, str]:
    return {name: render(name, label) for _group, items in GROUPS for name, label in items}


def main() -> int:
    check = "--check" in sys.argv
    stale = []
    for name, text in pages().items():
        path = SITE / f"{name}.html"
        if check and (not path.is_file() or path.read_text(encoding="utf-8") != text):
            stale.append(path.name)
        elif not check:
            path.write_text(text, encoding="utf-8", newline="\n")
    if stale:
        print(f"desatualizado: {', '.join(stale)}; rode `python scripts/build_docs.py`")
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
