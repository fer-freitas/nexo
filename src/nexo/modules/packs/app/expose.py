"""Comandos do Nexo dentro das próprias CLIs de IA: `/nexo-pm-refine` chama `nexo pm refine`, com as mesmas regras."""

from __future__ import annotations

from typing import Any

from nexo.kernel import modules
from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError

FOLDERS = {
    "claude": (".claude/commands", "{name}.md"),
    "copilot": (".github/prompts", "{name}.prompt.md"),
}
TEMPLATE = """---
description: {summary}
---

Rode no terminal, nesta pasta, o comando abaixo, e mostre o resultado sem resumir o que ele pedir para decidir:

    nexo {path} $ARGUMENTS

Regras:

- Não contorne o Nexo. Se ele recusar, explique o motivo que ele deu e pare.
- Não aprove nem decida nada em nome da pessoa: isso é com ela.
"""


def write(ctx: AppContext, tool: str) -> dict[str, Any]:
    if tool not in FOLDERS:
        raise NexoError(f"ferramenta sem esse recurso: {tool} (use {', '.join(FOLDERS)})")
    if ctx.project_root is None:
        raise NexoError("rode dentro de um repositório preparado (`nexo init`)")
    folder, pattern = FOLDERS[tool]
    target = ctx.project_root / folder
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for manifest in modules.builtin():
        for command in manifest.commands:
            words = command.path.split()
            if len(words) == 2 and words[0] in ("pm", "qa", "tl", "dev"):
                name = "nexo-" + "-".join(words)
                body = TEMPLATE.format(summary=command.summary, path=command.path)
                (target / pattern.format(name=name)).write_text(body, encoding="utf-8", newline="\n")
                written.append(name)
    ctx.audit("pack.exposed", {"tool": tool, "commands": len(written)})
    return {"tool": tool, "folder": folder, "commands": sorted(written)}
