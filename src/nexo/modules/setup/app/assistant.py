"""Assistente do console: só o que o console não entende sem IA vai para um modelo rápido (papel `assistant`).

Devolve uma resposta curta e, quando dá, o comando do Nexo que resolve. O comando volta para o console, que confere
se ele existe e pede confirmação quando custa ou muda algo; a IA nunca executa nada sozinha.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.platform import config

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"reply": {"type": "string", "minLength": 1}, "command": {"type": "string"}},
    "required": ["reply", "command"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Answer:
    reply: str
    argv: tuple[str, ...]
    run_id: str = ""


def ask(
    ctx: AppContext,
    message: str,
    *,
    catalog: str,
    project: str,
    steps: str,
    focus: str,
    history: list[tuple[str, str]],
    known: set[str],
) -> Answer:
    """`known`: primeiras palavras válidas de comando (comando fora do catálogo é descartado)."""
    from nexo.kernel import services

    llm = services.load("execution.llm")
    from nexo.kernel import services

    content = services.load("knowledge.content")

    root = ctx.project_root or ctx.cwd
    cfg = config.resolved(ctx).values
    chat = "\n".join(f"- {who}: {text}" for who, text in history[-8:]) or "(início da conversa)"
    prompt = content.prompt(ctx, "assistant").format(
        project=project or "(pasta sem projeto Nexo)",
        steps=steps or "(nada pendente)",
        focus=focus or "(nenhum)",
        history=chat,
        catalog=catalog,
        message=message,
    )

    def validate(data: dict[str, Any]) -> list[str]:
        argv = _argv(str(data.get("command") or ""))
        if argv and argv[0].lower() not in known:
            return [f"comando desconhecido: {argv[0]} (use só comandos do catálogo, sem 'nexo')"]
        return []

    result = llm.call_structured(
        ctx,
        "assistant",
        "console",
        prompt,
        SCHEMA,
        root,
        data_class=str(cfg.get("data_class") or "confidential"),
        validate=validate,
        max_attempts=2,
    )
    return Answer(str(result.data["reply"]).strip(), _argv(str(result.data.get("command") or "")), result.run_id)


def _argv(command: str) -> tuple[str, ...]:
    command = command.strip()
    if command.lower().startswith("nexo "):
        command = command[6:]
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    return tuple(w for w in words if w.strip())  # `""` (sem comando) vira vazio
