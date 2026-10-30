"""Subconjunto determinístico de Mustache para documentos Markdown.

Suporta `{{nome}}`, nomes com ponto (`{{a.b}}`), `{{.}}`, seções `{{#x}}…{{/x}}` (lista itera, objeto empilha
contexto, verdadeiro renderiza uma vez), invertidas `{{^x}}…{{/x}}` e comentários `{{! … }}`. Sem escape HTML
(o destino é Markdown). Linha que só contém uma marcação de seção é removida inteira, para o Markdown ficar limpo.
Variável ausente vira texto vazio — a checagem de seções obrigatórias acontece depois, no documento renderizado.
"""

from __future__ import annotations

import re
from typing import Any

TAG = re.compile(r"\{\{\s*([#^/!]?)\s*([^}]*?)\s*\}\}")


class TemplateSyntaxError(ValueError):
    pass


def _standalone(template: str) -> str:
    """Remove a quebra de linha de linhas que só têm `{{#x}}`, `{{^x}}`, `{{/x}}` ou `{{! }}`."""
    return re.sub(r"(?m)^[ \t]*(\{\{\s*[#^/!][^}]*\}\})[ \t]*\r?\n", r"\1", template)


def _parse(template: str) -> list[Any]:
    root: list[Any] = []
    stack: list[tuple[str, str, list[Any]]] = []
    current = root
    pos = 0
    for m in TAG.finditer(template):
        if m.start() > pos:
            current.append(template[pos : m.start()])
        kind, name = m.group(1), m.group(2)
        if kind == "!":
            pass
        elif kind in "#^" and kind:
            node: list[Any] = []
            current.append((kind, name, node))
            stack.append((kind, name, current))
            current = node
        elif kind == "/":
            if not stack or stack[-1][1] != name:
                raise TemplateSyntaxError(f"fechamento inesperado {{{{/{name}}}}}")
            _, _, current = stack.pop()
        else:
            current.append(("var", name))
        pos = m.end()
    if pos < len(template):
        current.append(template[pos:])
    if stack:
        raise TemplateSyntaxError(f"seção {{{{#{stack[-1][1]}}}}} sem fechamento")
    return root


def _lookup(stack: list[Any], name: str) -> Any:
    if name == ".":
        return stack[-1]
    head, *rest = name.split(".")
    for ctx in reversed(stack):
        if isinstance(ctx, dict) and head in ctx:
            value = ctx[head]
            for part in rest:
                value = value.get(part) if isinstance(value, dict) else None
            return value
    return None


def _fmt(value: Any) -> str:
    if value is None or value is False:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, list):
        return ", ".join(_fmt(v) for v in value)
    return str(value)


def _render(nodes: list[Any], stack: list[Any]) -> str:
    out: list[str] = []
    for node in nodes:
        if isinstance(node, str):
            out.append(node)
        elif node[0] == "var":
            out.append(_fmt(_lookup(stack, node[1])))
        else:
            kind, name, children = node
            value = _lookup(stack, name)
            empty = value is None or value is False or value == "" or value == [] or value == {}
            if kind == "^":
                if empty:
                    out.append(_render(children, stack))
            elif not empty:
                items = value if isinstance(value, list) else [value]
                for item in items:
                    out.append(_render(children, [*stack, item] if item is not True else stack))
    return "".join(out)


def render(template: str, data: dict[str, Any]) -> str:
    return _render(_parse(_standalone(template)), [data])


def check(template: str) -> list[str]:
    try:
        _parse(_standalone(template))
    except TemplateSyntaxError as exc:
        return [str(exc)]
    return []
