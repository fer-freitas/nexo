"""Markdown → HTML mínimo para destinos que não aceitam Markdown (Confluence storage)."""

from __future__ import annotations

import html
import re


def _inline(text: str) -> str:
    out = html.escape(text)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    return re.sub(r"(?<![*\w])_([^_]+)_(?![\w])", r"<em>\1</em>", out)


def markdown_to_html(markdown: str) -> str:
    lines = markdown.splitlines()
    out: list[str] = []
    in_list: str | None = None
    in_code = False
    para: list[str] = []

    def flush() -> None:
        nonlocal in_list
        if para:
            out.append("<p>" + " ".join(_inline(p) for p in para) + "</p>")
            para.clear()
        if in_list:
            out.append(f"</{in_list}>")
            in_list = None

    for line in lines:
        if line.startswith("```"):
            flush()
            out.append("</code></pre>" if in_code else "<pre><code>")
            in_code = not in_code
            continue
        if in_code:
            out.append(html.escape(line))
            continue
        heading = re.match(r"^(#{1,4})\s+(.*)$", line)
        item = re.match(r"^\s*(?:[-*]|(\d+)\.)\s+(.*)$", line)
        if heading:
            flush()
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
        elif item:
            kind = "ol" if item.group(1) else "ul"
            if para:
                out.append("<p>" + " ".join(_inline(p) for p in para) + "</p>")
                para.clear()
            if in_list != kind:
                if in_list:
                    out.append(f"</{in_list}>")
                out.append(f"<{kind}>")
                in_list = kind
            out.append(f"<li>{_inline(item.group(2))}</li>")
        elif not line.strip():
            flush()
        elif line.startswith(">"):
            flush()
            out.append(f"<blockquote>{_inline(line.lstrip('> '))}</blockquote>")
        else:
            para.append(line.strip())
    flush()
    return "\n".join(out)
